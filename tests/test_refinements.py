"""Tests de los afinados: capitán inteligente, valor a futuro (calendario),
timing de ventas y su dedup proactivo."""
from __future__ import annotations

from datetime import datetime, timezone

from core.models import MarketListing, Player, Position, TeamState, UpcomingFixture
from core.services import (
    _run_factor,
    best_captain,
    optimize_squad,
    sell_recommendations,
    sell_timing_alerts,
)
from data.db import filter_new_sell_alerts, init_db


def _p(pid, name, pos=Position.FORWARD, price=1_000_000, last_season=190, team_id=None,
       price_history=None):
    return Player(id=pid, name=name, position=pos, price=price, points_last_season=last_season,
                  team_id=team_id, price_history=price_history or [])


# --------------------------------------------------------------------------- #
# Capitán inteligente
# --------------------------------------------------------------------------- #
def test_best_captain_prefers_points_without_ownership():
    a = _p(1, "Bueno")
    b = _p(2, "Mejor")
    ep = {1: 5.0, 2: 8.0}
    assert best_captain([a, b], ep).id == 2  # sin datos de propiedad -> más puntos


def test_best_captain_differential_breaks_near_tie():
    # Dos jugadores casi iguales en puntos; el diferencial (menos propietarios) gana.
    a = _p(1, "Popular")
    b = _p(2, "Diferencial")
    ep = {1: 8.0, 2: 7.6}
    counts = {1: 10, 2: 1}  # a lo tienen todos; b casi nadie
    out = best_captain([a, b], ep, own_counts=counts, n_managers=10)
    assert out.id == 2


def test_best_captain_does_not_pick_bad_differential():
    # Un diferencial flojo NO debe superar a un crack que casi todos tienen.
    a = _p(1, "Crack")
    b = _p(2, "MaloRaro")
    ep = {1: 9.0, 2: 3.0}
    counts = {1: 10, 2: 0}
    assert best_captain([a, b], ep, own_counts=counts, n_managers=10).id == 1


# --------------------------------------------------------------------------- #
# Valor a futuro por calendario
# --------------------------------------------------------------------------- #
def test_run_factor_boosts_easy_and_penalizes_hard():
    easy = [UpcomingFixture(opponent="x", is_home=True, difficulty=20) for _ in range(3)]
    hard = [UpcomingFixture(opponent="x", is_home=True, difficulty=80) for _ in range(3)]
    assert _run_factor(easy) > 1.0
    assert _run_factor(hard) < 1.0
    assert _run_factor([]) == 1.0  # sin datos -> neutro


class _Client:
    def __init__(self, mine, market=None, fixtures=None):
        self._mine = mine
        self._market = market or []
        self._fixtures = fixtures or {}

    def get_all_players(self):
        return {p.id: p for p in self._mine + self._market}

    def get_my_team(self):
        return TeamState(team_id=1, name="HH", balance=50_000_000,
                         player_ids=[p.id for p in self._mine], owned={})

    def get_player(self, pid):
        return {p.id: p for p in self._mine + self._market}[pid]

    def get_market(self):
        return [MarketListing(player_id=p.id, price=p.price,
                              until=datetime.now(timezone.utc), seller_id=None) for p in self._market]

    def get_team_fixtures(self, weeks=5):
        return self._fixtures


def test_optimizer_prefers_candidate_with_better_calendar():
    # Mío y candidato con MISMOS puntos base, pero el candidato tiene calendario fácil.
    mine = [_p(1, "Mio", team_id=10, last_season=190)]
    cand = _p(2, "Candi", team_id=20, last_season=190)
    fixtures = {
        10: [UpcomingFixture(opponent="x", is_home=True, difficulty=80) for _ in range(3)],  # duro
        20: [UpcomingFixture(opponent="x", is_home=True, difficulty=20) for _ in range(3)],  # fácil
    }
    out = optimize_squad(_Client(mine, [cand], fixtures))
    assert "Candi" in out and "mejor calendario" in out


# --------------------------------------------------------------------------- #
# Timing de ventas
# --------------------------------------------------------------------------- #
def _falling_history():
    # Precio claramente cayendo en las últimas muestras.
    return [(250101 + i, price) for i, price in enumerate([10_000_000, 10_000_000, 9_500_000, 9_000_000])]


def test_sell_recommendations_flags_falling_price():
    faller = _p(1, "Cayendo", team_id=10, price=9_000_000, price_history=_falling_history())
    steady = _p(2, "Estable", team_id=11, price=5_000_000,
                price_history=[(250101 + i, 5_000_000) for i in range(4)])
    client = _Client([faller, steady])
    recs = sell_recommendations(client)
    assert recs and recs[0]["player"].id == 1
    assert any("bajando" in r for r in recs[0]["reasons"])
    assert 2 not in [r["player"].id for r in recs]  # el estable no se recomienda vender


def test_sell_alerts_dedup_and_reset():
    init_db()
    faller = _p(1, "Cayendo", team_id=10, price=9_000_000, price_history=_falling_history())
    client = _Client([faller])

    first = sell_timing_alerts(client)
    assert first and "Cayendo" in first        # primer aviso
    assert sell_timing_alerts(client) is None   # no repite (dedup)

    # Si deja de estar en riesgo, se olvida; y si vuelve a caer, re-avisa.
    filter_new_sell_alerts([])                   # ya no está en riesgo -> olvidar
    again = sell_timing_alerts(client)
    assert again and "Cayendo" in again


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            init_db()
            fn()
    print("OK: afinados correctos")
