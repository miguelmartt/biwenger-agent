"""Tests del espía de rivales y los diferenciales.

Comprueba que (a) detectamos fichajes/ventas de rivales sin soltar un aviso
gigante la primera vez, y (b) los diferenciales distinguen tus jugadores únicos
y las gemas libres que nadie tiene."""
from __future__ import annotations

from datetime import datetime, timezone

from core.models import MarketListing, Player, Position, TeamState
from core.services import collect_rival_moves, daily_digest, differentials_message, rival_moves
from data.db import detect_rival_moves, init_db


def _p(pid, name, pos=Position.FORWARD, price=1_000_000, last_season=190):
    return Player(id=pid, name=name, position=pos, price=price, points_last_season=last_season)


# --------------------------------------------------------------------------- #
# Detección de movimientos de rivales (con estado en BD).
# --------------------------------------------------------------------------- #
def test_detect_rival_moves_seeds_silently_then_reports():
    init_db()
    # Primera vez: solo siembra, NO avisa (evita el volcado inicial).
    first = detect_rival_moves({"7": {"name": "Rival", "player_ids": [1, 2, 3]}})
    assert first == []

    # Rival ficha al 4 y suelta al 1.
    moves = detect_rival_moves({"7": {"name": "Rival", "player_ids": [2, 3, 4]}})
    assert len(moves) == 1
    name, added, removed = moves[0]
    assert name == "Rival"
    assert set(added) == {4} and set(removed) == {1}

    # Sin cambios: no reporta nada.
    assert detect_rival_moves({"7": {"name": "Rival", "player_ids": [2, 3, 4]}}) == []


class _Client:
    def __init__(self, mine, market, rivals):
        # rivals: {manager_id: {"name":.., "players":[ids]}}
        self._mine = mine
        self._market = market
        self._rivals = rivals

    def get_all_players(self):
        allp = self._mine + self._market
        return {p.id: p for p in allp}

    def get_my_team(self):
        return TeamState(team_id=1, name="Hustle Hard", balance=10_000_000,
                         player_ids=[p.id for p in self._mine], owned={})

    def get_player(self, pid):
        return {p.id: p for p in self._mine + self._market}[pid]

    def get_market(self):
        return [MarketListing(player_id=p.id, price=p.price,
                              until=datetime.now(timezone.utc), seller_id=None) for p in self._market]

    def get_league_managers(self):
        return [{"id": mid, "name": info["name"]} for mid, info in self._rivals.items()]

    def get_manager_clauses(self, user_id):
        return {pid: {"clause": None, "buy_price": None} for pid in self._rivals[user_id]["players"]}


def test_differentials_finds_yours_and_free_gems():
    # Tú tienes al 1 (crack) que nadie más tiene -> diferencial tuyo.
    # En el mercado libre está el 99 (crack) que nadie tiene -> gema libre.
    # El 2 lo tienen dos rivales -> NO es diferencial.
    mine = [_p(1, "MiJoya", last_season=228), _p(2, "Compartido", last_season=228)]
    market = [_p(99, "GemaLibre", last_season=228)]
    rivals = {
        10: {"name": "R1", "players": [2]},
        11: {"name": "R2", "players": [2]},
    }
    out = differentials_message(_Client(mine, market, rivals))
    assert "MiJoya" in out          # tuyo y único
    assert "GemaLibre" in out       # libre que nadie tiene
    assert "Compartido" not in out  # lo tienen los rivales


def test_rival_moves_service_names_players():
    init_db()
    mine = [_p(1, "Yo")]
    market = []
    rivals = {10: {"name": "Pepe", "players": [1, 2]}}
    catalog = {1: _p(1, "Yo"), 2: _p(2, "Fichado")}
    client = _Client(mine, market, rivals)
    # Sembramos con players [1,2].
    assert rival_moves(client, catalog) == []
    # Pepe ficha al jugador 2? Ya lo tenía; cambiemos a [1,3].
    rivals[10]["players"] = [1, 3]
    catalog[3] = _p(3, "Nuevo")
    msgs = rival_moves(client, catalog)
    assert msgs and "Pepe" in msgs[0] and "Nuevo" in msgs[0] and "Fichado" in msgs[0]


def test_daily_digest_accumulates_and_consumes():
    init_db()
    mine = [_p(1, "Yo")]
    rivals = {10: {"name": "Pepe", "players": [1, 2]}, 11: {"name": "Ana", "players": [5]}}
    catalog = {1: _p(1, "Yo"), 2: _p(2, "Fichado"), 5: _p(5, "OtroA"), 7: _p(7, "OtroB")}
    client = _Client(mine, [], rivals)

    # Siembra: primera detección no acumula nada.
    assert collect_rival_moves(client, catalog) == 0
    # Cambios: Pepe ficha al 7 y suelta al 2; Ana suelta al 5.
    rivals[10]["players"] = [1, 7]
    rivals[11]["players"] = []
    assert collect_rival_moves(client, catalog) == 2  # 2 managers con cambios

    # El botón (consume=False) muestra pero NO vacía.
    view = daily_digest(client, catalog, consume=False)
    assert view and "Movimientos de rivales de hoy" in view
    assert "Pepe" in view and "Ana" in view
    assert daily_digest(client, catalog, consume=False) is not None  # sigue ahí

    # El resumen de las 15:00 (consume=True) vacía lo acumulado.
    sent = daily_digest(client, catalog, consume=True)
    assert sent is not None
    assert daily_digest(client, catalog, consume=False) is None  # ya no queda nada


if __name__ == "__main__":
    test_detect_rival_moves_seeds_silently_then_reports()
    test_differentials_finds_yours_and_free_gems()
    test_rival_moves_service_names_players()
    print("OK: espía de rivales y diferenciales correctos")
