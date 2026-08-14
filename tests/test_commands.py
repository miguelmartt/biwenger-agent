"""Tests del dispatcher de comandos y del flujo de acciones con confirmación."""
from __future__ import annotations

from bot.commands import handle_command
from bot.actions import handle_callback


class _FakeClient:
    """Cliente falso. Las acciones son no-op (como en DRY_RUN real)."""

    def get_player(self, pid):
        class _P:
            name = f"Jugador {pid}"
        return _P()

    def get_balance(self):
        return 20_000_000

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
    # Pulsar '✅ Fichar' pide confirmación con botones Sí/Cancelar (no ejecuta nada).
    text, buttons = handle_callback("ask:f:12345:150000", _FakeClient())
    assert "¿Confirmas?" in text
    labels = [b[0] for b in buttons]
    datas = [b[1] for b in buttons]
    assert any("Sí" in l for l in labels)
    assert "do:f:12345:150000" in datas
    assert "cancel" in datas


def test_cancel_does_nothing():
    text, buttons = handle_callback("cancel", _FakeClient())
    assert "cancel" in text.lower()
    assert buttons is None


def test_execute_in_dry_run_does_not_act():
    # Con DRY_RUN (por defecto en tests), confirmar NO ejecuta compra real.
    text, _ = handle_callback("do:f:12345:150000", _FakeClient())
    assert "MODO PRUEBA" in text


def test_bid_selector_offers_levels():
    # Pulsar 'Fichar' abre el selector con niveles competitiva/fuerte/mínima.
    text, buttons = handle_callback("bid:12345:150000", _FakeClient())
    datas = [b[1] for b in buttons]
    labels = " ".join(b[0] for b in buttons)
    assert "Competitiva" in labels and "Fuerte" in labels
    # La competitiva debe ser mayor que el mínimo del mercado.
    do_amounts = [int(d.split(":")[3]) for d in datas if d.startswith("do:f:")]
    assert max(do_amounts) > 150000


def test_sell_flow_confirms_and_executes():
    text, buttons = handle_callback("ask:s:12345:2000000", _FakeClient())
    assert "venta" in text.lower()
    do = [b[1] for b in buttons if b[1].startswith("do:s:")]
    assert do
    result, _ = handle_callback(do[0], _FakeClient())
    assert "MODO PRUEBA" in result or "venta" in result.lower()


def test_bid_levels_capped_by_budget():
    from bidding.valuation import bid_levels
    levels = bid_levels(10_000_000, available_budget=11_000_000)
    # 'fuerte' (x1.35 = 13.5M) se capa por presupuesto (11M - colchón 1M = 10M).
    assert levels["fuerte"] <= 10_000_000


if __name__ == "__main__":
    test_help_lists_commands()
    test_unknown_command_is_friendly()
    test_ask_button_requests_confirmation()
    test_cancel_does_nothing()
    test_execute_in_dry_run_does_not_act()
    print("OK: comandos y flujo de confirmación correctos")
