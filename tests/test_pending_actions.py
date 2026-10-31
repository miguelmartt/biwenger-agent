"""Tests de las acciones con dinero: cada confirmación vale una vez, caduca a los 10 minutos
y, antes de ejecutarse, se comprueba contra Biwenger que nada ha cambiado.

El cliente falso no simula DRY_RUN: registra cada escritura que le llega, que es justo lo
que hay que contar (una sola compra por confirmación, ninguna si algo cambió).
"""
from __future__ import annotations

import dataclasses
import time
from datetime import datetime, timedelta, timezone

import pytest

from bot import actions
from bot.actions import handle_callback
from core.models import MarketListing, TeamState
from data import db

PID = 4242
RIVAL = 777
UNTIL = int(time.time()) + 3600


@pytest.fixture(autouse=True)
def _dry_run(monkeypatch):
    monkeypatch.setattr(actions, "settings", dataclasses.replace(actions.settings, dry_run=True))


class _Client:
    def __init__(self):
        self.price = 1_000_000                 # mínimo del mercado libre
        self.until = UNTIL                     # cierre del anuncio
        self.listed = True
        self.balance = 20_000_000
        self.rival_clause = 5_000_000
        self.rival_owns = True
        self.my_clause = 3_000_000
        self.my_price = 2_000_000
        self.i_own = True
        self.writes: list[tuple] = []

    def get_player(self, pid):
        return type("P", (), {"name": f"Jugador {pid}", "price": self.my_price})()

    def get_market(self):
        if not self.listed:
            return []
        return [MarketListing(player_id=PID, price=self.price,
                              until=datetime.fromtimestamp(self.until, tz=timezone.utc))]

    def get_balance(self):
        return self.balance

    def get_my_team(self):
        owned = {PID: {"clause": self.my_clause, "price": self.my_price}} if self.i_own else {}
        return TeamState(team_id=1, name="Yo", balance=self.balance, player_ids=list(owned), owned=owned)

    def get_manager_clauses(self, user_id):
        assert user_id == RIVAL
        return {PID: {"clause": self.rival_clause, "buy_price": 1}} if self.rival_owns else {}

    def place_bid(self, pid, amount, seller_id=None):
        self.writes.append(("bid", pid, amount))

    def pay_clause(self, pid, amount, owner_id=None):
        self.writes.append(("clause", pid, amount, owner_id))

    def raise_clause(self, pid, amount):
        self.writes.append(("raise", pid, amount))

    def list_for_sale(self, pid, price):
        self.writes.append(("sell", pid, price))


def _data(buttons, prefix):
    return [b[1] for b in buttons if b[1].startswith(prefix)]


def _ask_clause(client):
    _, buttons = handle_callback(f"ask:c:{PID}:{client.rival_clause}:{RIVAL}", client)
    return _data(buttons, "do:")[0]


# --------------------------------------------------------------------------- #
# Un solo uso
# --------------------------------------------------------------------------- #
def test_confirmation_runs_only_once():
    client = _Client()
    do = _ask_clause(client)
    first, _ = handle_callback(do, client)
    second, _ = handle_callback(do, client)
    assert "MODO PRUEBA" in first
    assert "ya se usó" in second
    assert client.writes == [("clause", PID, 5_000_000, RIVAL)]


def test_claim_is_atomic():
    batch, ids = db.create_pending_actions([{"mode": "s", "player_id": PID, "amount": 1, "ref_price": 1}])
    assert db.claim_pending_action(ids[0])[0] == "ok"
    assert db.claim_pending_action(ids[0])[0] == "used"
    assert db.claim_pending_action("noexiste")[0] == "missing"


def test_cancel_invalidates_the_confirmation():
    client = _Client()
    _, buttons = handle_callback(f"ask:s:{PID}:{client.my_price}", client)
    handle_callback(_data(buttons, "cancel:")[0], client)
    text, _ = handle_callback(_data(buttons, "do:")[0], client)
    assert "ya se usó" in text and client.writes == []


def test_old_buttons_with_amount_inside_do_nothing():
    # Botones de mensajes de antes del cambio: llevaban el importe en el callback_data.
    client = _Client()
    for data in (f"do:f:{PID}:150000", f"do:c:{PID}:5000000:{RIVAL}", f"snipe:{PID}:300000"):
        text, _ = handle_callback(data, client)
        assert "versión anterior" in text
    assert client.writes == []
    assert db.active_snipe_targets() == []


# --------------------------------------------------------------------------- #
# Caducidad
# --------------------------------------------------------------------------- #
def test_confirmation_expires_after_ten_minutes():
    client = _Client()
    do = _ask_clause(client)
    action_id = do.split(":")[1]
    with db.get_session() as s:
        s.get(db.PendingAction, action_id).created_at -= timedelta(seconds=db.PENDING_ACTION_TTL + 1)
        s.commit()
    text, _ = handle_callback(do, client)
    assert "caducó" in text and client.writes == []
    # Y no revive: ya está consumida.
    text, _ = handle_callback(do, client)
    assert "ya se usó" in text


# --------------------------------------------------------------------------- #
# Revalidación: si algo cambió desde que se pidió, no se hace nada
# --------------------------------------------------------------------------- #
def test_clause_changed_since_asked():
    client = _Client()
    do = _ask_clause(client)
    client.rival_clause = 9_000_000  # el rival la ha subido mientras tanto
    text, _ = handle_callback(do, client)
    assert "No he hecho nada" in text and "9.000.000" in text
    assert client.writes == []


def test_clause_owner_changed_since_asked():
    client = _Client()
    do = _ask_clause(client)
    client.rival_owns = False  # lo ha vendido o se lo han clausulado
    text, _ = handle_callback(do, client)
    assert "ya no es de ese rival" in text and client.writes == []


def test_clause_without_balance_is_refused():
    client = _Client()
    do = _ask_clause(client)
    client.balance = 5_500_000  # 5,5 M - 1 M de colchón < 5 M de cláusula
    text, _ = handle_callback(do, client)
    assert "colchón" in text and client.writes == []


def _bid_confirm(client, level="Competitiva"):
    _, selector = handle_callback(f"bid:{PID}:{client.price}", client)
    confirm = next(b[1] for b in selector if b[0].startswith(("🟢", "🔥", "⚪", "🎯")) and level in b[0])
    text, buttons = handle_callback(confirm, client)
    return text, buttons, selector


def test_bid_needs_explicit_confirmation():
    client = _Client()
    _, selector = handle_callback(f"bid:{PID}:{client.price}", client)
    assert _data(selector, "do:") == []           # elegir nivel no puja
    text, buttons, _ = _bid_confirm(client)
    assert "¿Confirmas?" in text and client.writes == []
    result, _ = handle_callback(_data(buttons, "do:")[0], client)
    assert "MODO PRUEBA" in result
    assert client.writes == [("bid", PID, 1_150_000)]


def test_using_one_bid_level_invalidates_the_others():
    client = _Client()
    _, buttons, selector = _bid_confirm(client, "Competitiva")
    handle_callback(_data(buttons, "do:")[0], client)
    other = next(b[1] for b in selector if "Fuerte" in b[0])
    text, _ = handle_callback(other, client)
    assert "ya no vale" in text
    assert len(client.writes) == 1


def test_bid_price_changed_since_asked():
    client = _Client()
    _, buttons, _ = _bid_confirm(client)
    client.price = 1_200_000
    text, _ = handle_callback(_data(buttons, "do:")[0], client)
    assert "precio mínimo" in text and client.writes == []


def test_bid_on_a_different_listing_is_refused():
    client = _Client()
    _, buttons, _ = _bid_confirm(client)
    client.until = UNTIL + 86_400  # el mercado cerró y ha vuelto a salir
    text, _ = handle_callback(_data(buttons, "do:")[0], client)
    assert "anuncio" in text and client.writes == []


def test_bid_without_balance_is_refused():
    client = _Client()
    _, buttons, _ = _bid_confirm(client)
    client.balance = 1_500_000  # se ha gastado el saldo en otra cosa
    text, _ = handle_callback(_data(buttons, "do:")[0], client)
    assert "colchón" in text and client.writes == []


def test_bid_selector_refuses_without_balance_reading():
    client = _Client()

    def boom():
        raise RuntimeError("timeout")
    client.get_balance = boom
    text, buttons = handle_callback(f"bid:{PID}:{client.price}", client)
    assert "saldo" in text and buttons is None


def test_bid_selector_refuses_when_budget_does_not_reach():
    client = _Client()
    client.balance = 1_500_000  # 500 k para gastar, mínimo 1 M
    text, buttons = handle_callback(f"bid:{PID}:{client.price}", client)
    assert "No te llega" in text and buttons is None


def test_snipe_goes_through_confirmation_and_binds_the_listing():
    client = _Client()
    _, buttons, _ = _bid_confirm(client, "Auto-pujar")
    assert db.active_snipe_targets() == []        # todavía nada: falta el ✅
    handle_callback(_data(buttons, "do:")[0], client)
    (target,) = db.active_snipe_targets()
    assert target.listing_until == UNTIL and target.max_bid == 1_350_000


def test_sell_price_changed_since_asked():
    client = _Client()
    _, buttons = handle_callback(f"ask:s:{PID}:{client.my_price}", client)
    client.my_price = 2_500_000
    text, _ = handle_callback(_data(buttons, "do:")[0], client)
    assert "valor de mercado" in text and client.writes == []


def test_raise_clause_checks_ownership_and_cost():
    client = _Client()
    _, buttons = handle_callback(f"ask:u:{PID}:6000000", client)
    client.i_own = False
    text, _ = handle_callback(_data(buttons, "do:")[0], client)
    assert "plantilla" in text and client.writes == []

    client = _Client()
    _, buttons = handle_callback(f"ask:u:{PID}:6000000", client)
    client.balance = 3_500_000  # 3 M de subida > 3,5 M - 1 M de colchón
    text, _ = handle_callback(_data(buttons, "do:")[0], client)
    assert "subida" in text and client.writes == []


def test_unreadable_state_means_no_action():
    client = _Client()
    do = _ask_clause(client)

    def boom(_uid):
        raise RuntimeError("timeout")
    client.get_manager_clauses = boom
    text, _ = handle_callback(do, client)
    assert "no he podido comprobar" in text and client.writes == []


def test_failed_write_is_not_repeated_by_the_same_button():
    client = _Client()
    do = _ask_clause(client)

    def boom(*_a, **_k):
        raise RuntimeError("500 Server Error")
    client.pay_clause = boom
    text, _ = handle_callback(do, client)
    assert "No se pudo completar" in text and "No lo repito" in text
    text, _ = handle_callback(do, client)
    assert "ya se usó" in text


# --------------------------------------------------------------------------- #
# Esquema: una BD de antes recibe las columnas nuevas
# --------------------------------------------------------------------------- #
def test_init_db_adds_new_columns_to_an_old_database():
    from sqlalchemy import inspect, text

    db.Base.metadata.drop_all(db.engine)
    with db.engine.begin() as conn:
        conn.execute(text("CREATE TABLE snipe_targets (player_id VARCHAR PRIMARY KEY, player_name VARCHAR,"
                          " max_bid INTEGER, status VARCHAR, result VARCHAR, created_at DATETIME)"))
        conn.execute(text("INSERT INTO snipe_targets VALUES ('1', 'Viejo', 100, 'pending', '', NULL)"))
    db.init_db()
    columns = {c["name"] for c in inspect(db.engine).get_columns("snipe_targets")}
    assert "listing_until" in columns
    (legacy,) = db.active_snipe_targets()
    assert legacy.listing_until is None
