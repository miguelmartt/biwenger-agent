"""Tests de las automatizaciones con límites: alineación auto y cláusula auto.

Lo importante aquí es que NADA se dispara sin que el usuario lo active con /auto,
y que cuando está activado NO se repite (ni la alineación por jornada, ni la
subida de cláusula por jugador). Son las salvaguardas que evitan sorpresas.
"""
from __future__ import annotations

from core import services
from core.models import Player, Position, TeamState
from data.db import get_setting, init_db, set_setting


def _p(pid, name, pos, price, last_season=190, clause=None):
    return Player(id=pid, name=name, position=pos, price=price,
                  points_last_season=last_season, clause=clause)


class _Client:
    """Cliente falso que registra las llamadas de escritura (para verificar que
    solo se ejecutan cuando toca)."""

    def __init__(self, mine, balance=5_000_000, owned=None, round_id=100):
        self._mine = mine
        self._balance = balance
        self._owned = owned or {}
        self._round_id = round_id
        self.raised: list[tuple[int, int]] = []
        self.lineups: list[tuple] = []

    def get_all_players(self):
        return {p.id: p for p in self._mine}

    def get_my_team(self):
        return TeamState(team_id=1, name="Hustle Hard", balance=self._balance,
                         player_ids=[p.id for p in self._mine], owned=self._owned)

    def get_current_round_id(self):
        return self._round_id

    def get_player(self, pid):
        return {p.id: p for p in self._mine}[pid]

    def raise_clause(self, pid, amount):
        self.raised.append((pid, amount))

    def set_lineup(self, ids, formation, captain_id):
        self.lineups.append((ids, formation, captain_id))


# --------------------------------------------------------------------------- #
# Gating: por defecto TODO está apagado (el bot no actúa sin permiso).
# --------------------------------------------------------------------------- #
def test_auto_lineup_off_by_default():
    init_db()
    client = _Client([_p(1, "A", Position.FORWARD, 1_000_000)])
    assert services.auto_set_lineup(client) is None
    assert client.lineups == []  # no tocó nada


def test_auto_clause_off_by_default():
    init_db()
    catalog = {1: _p(1, "A", Position.FORWARD, 1_000_000)}
    client = _Client([catalog[1]])
    team = client.get_my_team()
    assert services.auto_raise_clauses(client, catalog, team) == []
    assert client.raised == []


# --------------------------------------------------------------------------- #
# El toggle (botón /auto) persiste el ajuste correctamente.
# --------------------------------------------------------------------------- #
def test_toggle_persists_setting():
    init_db()
    from bot.actions import handle_callback

    text, _ = handle_callback("toggle:auto_lineup:on", client=None)
    assert get_setting("auto_lineup") == "on"
    assert "activada" in text

    text, _ = handle_callback("toggle:auto_lineup:off", client=None)
    assert get_setting("auto_lineup") == "off"
    assert "desactivada" in text


# --------------------------------------------------------------------------- #
# Auto-cláusula: cuando está ON, sube la de un crack vulnerable UNA sola vez.
# --------------------------------------------------------------------------- #
def test_auto_clause_raises_once_when_on():
    init_db()
    set_setting("auto_clause", "on")
    # Crack (5 pts/j) con cláusula muy por debajo de 1.4x su precio -> vulnerable.
    crack = _p(1, "Crack", Position.FORWARD, 10_000_000, last_season=190)
    catalog = {1: crack}
    owned = {1: {"clause": 11_000_000, "price": 10_000_000}}  # 1.1x < 1.4x -> riesgo
    client = _Client([crack], owned=owned)
    team = client.get_my_team()

    msgs = services.auto_raise_clauses(client, catalog, team)
    assert len(client.raised) == 1
    assert client.raised[0][0] == 1
    assert msgs and "Crack" in msgs[0]

    # Segunda pasada: ya está marcada, no debe repetir.
    msgs2 = services.auto_raise_clauses(client, catalog, team)
    assert client.raised == [(1, 20_000_000)]  # sigue habiendo UNA sola subida
    assert msgs2 == []


def test_auto_clause_skips_well_protected_player():
    init_db()
    set_setting("auto_clause", "on")
    # Cláusula alta (3x el precio) -> no es vulnerable, no se toca.
    crack = _p(1, "Blindado", Position.FORWARD, 10_000_000, last_season=190)
    catalog = {1: crack}
    owned = {1: {"clause": 30_000_000, "price": 10_000_000}}
    client = _Client([crack], owned=owned)
    team = client.get_my_team()

    assert services.auto_raise_clauses(client, catalog, team) == []
    assert client.raised == []


if __name__ == "__main__":
    test_auto_lineup_off_by_default()
    test_auto_clause_off_by_default()
    test_toggle_persists_setting()
    test_auto_clause_raises_once_when_on()
    test_auto_clause_skips_well_protected_player()
    print("OK: automatizaciones con límites correctas")
