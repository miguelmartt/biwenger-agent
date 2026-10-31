"""Tests del dispatcher de comandos y del flujo de acciones con confirmación."""
from __future__ import annotations

import dataclasses
import time
from datetime import datetime, timezone

import pytest

from bot import actions
from bot.commands import handle_command
from bot.actions import handle_callback
from core.models import MarketListing, TeamState

UNTIL = int(time.time()) + 3600


@pytest.fixture(autouse=True)
def _dry_run(monkeypatch):
    # Estos tests nunca deben depender del .env de quien los lance: siempre en modo prueba.
    monkeypatch.setattr(actions, "settings", dataclasses.replace(actions.settings, dry_run=True))


class _FakeClient:
    """Cliente falso. Las acciones son no-op (como en DRY_RUN real)."""

    def get_player(self, pid):
        class _P:
            name = f"Jugador {pid}"
            price = 2_000_000
        return _P()

    def get_balance(self):
        return 20_000_000

    def get_market(self):
        return [MarketListing(player_id=12345, price=150000,
                              until=datetime.fromtimestamp(UNTIL, tz=timezone.utc))]

    def get_my_team(self):
        return TeamState(team_id=1, name="Yo", balance=20_000_000, player_ids=[12345],
                         owned={12345: {"clause": 3_000_000, "price": 2_000_000}})

    def place_bid(self, pid, amount, seller_id=None):
        return None

    def pay_clause(self, pid, amount, owner_id=None):
        return None

    def list_for_sale(self, pid, price):
        return None


def test_help_lists_commands():
    text, buttons = handle_command("/help", _FakeClient())
    assert "/alineacion" in text and "/mercado" in text
    assert buttons is None


def test_unknown_command_is_friendly():
    text, _ = handle_command("/loquesea", _FakeClient())
    assert "/help" in text


def test_ask_button_requests_confirmation():
    # Pulsar un botón de la lista pide confirmación con botones Sí/Cancelar (no ejecuta nada).
    text, buttons = handle_callback("ask:s:12345:2000000", _FakeClient())
    assert "¿Confirmas?" in text
    labels = [b[0] for b in buttons]
    datas = [b[1] for b in buttons]
    assert any("Sí" in l for l in labels)
    # El botón de confirmar solo lleva el id de la acción guardada, no el importe.
    do = [d for d in datas if d.startswith("do:")]
    assert len(do) == 1 and len(do[0].split(":")) == 2 and "2000000" not in do[0]
    assert any(d.startswith("cancel") for d in datas)


def test_cancel_does_nothing():
    text, buttons = handle_callback("cancel", _FakeClient())
    assert "cancel" in text.lower()
    assert buttons is None


def test_execute_in_dry_run_does_not_act():
    # Con DRY_RUN, confirmar NO ejecuta compra real.
    _, buttons = handle_callback("bid:12345:150000", _FakeClient())
    level = next(b[1] for b in buttons if b[1].startswith("confirm:"))
    _, confirm = handle_callback(level, _FakeClient())
    do = next(b[1] for b in confirm if b[1].startswith("do:"))
    text, _ = handle_callback(do, _FakeClient())
    assert "MODO PRUEBA" in text


def test_bid_selector_offers_levels():
    # Pulsar 'Fichar' abre el selector con niveles competitiva/fuerte/mínima.
    text, buttons = handle_callback("bid:12345:150000", _FakeClient())
    labels = " ".join(b[0] for b in buttons)
    assert "Competitiva" in labels and "Fuerte" in labels
    # Ningún nivel puja directamente: todos abren la confirmación.
    assert not any(b[1].startswith("do:") for b in buttons)
    from data.db import peek_pending_action
    amounts = [peek_pending_action(b[1].split(":")[1]).amount for b in buttons if b[1].startswith("confirm:")]
    # La competitiva debe ser mayor que el mínimo del mercado.
    assert max(amounts) > 150000


def test_sell_flow_confirms_and_executes():
    text, buttons = handle_callback("ask:s:12345:2000000", _FakeClient())
    assert "venta" in text.lower()
    do = [b[1] for b in buttons if b[1].startswith("do:")]
    assert do
    result, _ = handle_callback(do[0], _FakeClient())
    assert "MODO PRUEBA" in result and "venta" in result.lower()


def test_toggle_only_accepts_known_switches():
    # Un botón con otra clave (p. ej. el token) u otro valor no escribe nada en la config.
    from data.db import get_setting

    for data in ("toggle:biwenger_token:x", "toggle:telegram_offset:0", "toggle:auto_clause:yes",
                 "toggle:autoclause_1:done"):
        text, _ = handle_callback(data, client=None)
        assert "No he entendido" in text
    assert get_setting("biwenger_token") is None
    assert get_setting("auto_clause") is None
    assert get_setting("autoclause_1") is None


def test_bid_levels_capped_by_budget():
    from bidding.valuation import bid_levels
    levels = bid_levels(10_000_000, available_budget=11_000_000)
    # 'fuerte' (x1.35 = 13.5M) se capa por presupuesto (11M - colchón 1M = 10M).
    assert levels["fuerte"] <= 10_000_000


def test_bid_levels_refuse_when_budget_is_short():
    from bidding.valuation import bid_levels
    # Saldo por debajo del colchón: antes devolvía los importes SIN capar.
    assert bid_levels(150_000, available_budget=500_000) == {}
    assert bid_levels(150_000, available_budget=1_000_000) == {}
    # Llega para algo, pero no para el mínimo del mercado: tampoco se ofrece una puja inválida.
    assert bid_levels(2_000_000, available_budget=2_500_000) == {}
    # Ningún nivel por debajo del mínimo del mercado.
    assert min(bid_levels(150_400, available_budget=20_000_000).values()) >= 150_400


def test_recommend_bid_never_exceeds_the_cap():
    from bidding.valuation import recommend_bid
    from core.models import Player, Position
    rec = recommend_bid(Player(id=1, name="X", position=Position.FORWARD, price=5_000_000), "urgent", 500_000)
    assert rec.max_bid == 0 and rec.ideal_bid == 0


if __name__ == "__main__":
    test_help_lists_commands()
    test_unknown_command_is_friendly()
    test_ask_button_requests_confirmation()
    test_cancel_does_nothing()
    test_execute_in_dry_run_does_not_act()
    print("OK: comandos y flujo de confirmación correctos")
