"""Tests de las alertas proactivas de chollos y el optimizador de plantilla."""
from __future__ import annotations

from datetime import datetime, timezone

from core.models import MarketListing, Player, Position, TeamState
from core.services import optimize_squad
from data.db import filter_new_bargains, init_db


def _p(pid, name, pos, price, last_season=190):
    return Player(id=pid, name=name, position=pos, price=price, points_last_season=last_season)


def test_bargain_dedup_only_new():
    init_db()
    assert set(filter_new_bargains([101, 102])) == {101, 102}  # primera vez: nuevos
    assert filter_new_bargains([101, 102]) == []               # segunda vez: ya avisados
    assert filter_new_bargains([101, 103]) == [103]            # solo el nuevo


class _Client:
    def __init__(self, mine, market, balance):
        self._mine, self._market, self._balance = mine, market, balance

    def get_all_players(self):
        return {p.id: p for p in self._mine + self._market}

    def get_my_team(self):
        return TeamState(team_id=1, name="Test", balance=self._balance,
                         player_ids=[p.id for p in self._mine], owned={})

    def get_market(self):
        return [MarketListing(player_id=p.id, price=p.price,
                              until=datetime.now(timezone.utc), seller_id=None) for p in self._market]

    def get_player(self, pid):
        return {p.id: p for p in self._mine + self._market}[pid]


def test_optimize_suggests_upgrade_swap():
    # Tengo un MF flojo; en el mercado hay un MF mejor y asequible -> debe sugerir el swap.
    mine = [_p(1, "MiFlojo", Position.MIDFIELDER, 2_000_000, last_season=76)]     # ~2 pts/j
    market = [_p(2, "Crack", Position.MIDFIELDER, 3_000_000, last_season=228)]    # ~6 pts/j
    out = optimize_squad(_Client(mine, market, balance=5_000_000))
    assert "Crack" in out and "MiFlojo" in out and "Vende" in out


def test_optimize_no_move_when_budget_insufficient():
    mine = [_p(1, "MiFlojo", Position.MIDFIELDER, 1_000_000, last_season=76)]
    market = [_p(2, "Carisimo", Position.MIDFIELDER, 50_000_000, last_season=300)]
    out = optimize_squad(_Client(mine, market, balance=100_000))  # no llega
    assert "No veo mejoras" in out


if __name__ == "__main__":
    test_bargain_dedup_only_new()
    test_optimize_suggests_upgrade_swap()
    test_optimize_no_move_when_budget_insufficient()
    print("OK: mercado nivel pro correcto")
