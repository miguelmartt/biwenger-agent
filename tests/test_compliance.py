"""Tests del detector de infracciones del reglamento."""
from __future__ import annotations

from types import SimpleNamespace

from compliance.checker import (
    check_club_limit,
    check_expensive_captains,
    check_low_clauses,
    compute_punishment,
    required_min_clause,
)
from core.models import Player, Position

RULES = SimpleNamespace(
    RULE_CHECK_ENABLED=True,
    CAPTAIN_MAX_VALUE=7_500_000,
    MAX_PLAYERS_PER_CLUB=3,
    CLAUSE_MIN_TIERS=[(2_000_000, 2.5), (10_000_000, 2.0), (float("inf"), 1.5)],
    CLAUSE_RULES_FROM_ROUND=2,
)


def _p(pid, name, price, team_id=None, pos=Position.FORWARD):
    return Player(id=pid, name=name, position=pos, price=price, team_id=team_id)


def test_required_min_clause_tiers():
    assert required_min_clause(1_000_000, RULES.CLAUSE_MIN_TIERS) == 2_500_000   # 2,5x
    assert required_min_clause(5_000_000, RULES.CLAUSE_MIN_TIERS) == 10_000_000  # 2x
    assert required_min_clause(20_000_000, RULES.CLAUSE_MIN_TIERS) == 30_000_000  # 1,5x


class _Client:
    def __init__(self, managers, clauses=None, lineups=None, round_id=10):
        self._managers = managers
        self._clauses = clauses or {}
        self._lineups = lineups or {}
        self._round_id = round_id

    def get_current_round_id(self):
        return self._round_id

    def get_league_managers(self):
        return self._managers

    def get_manager_clauses(self, user_id):
        return self._clauses.get(user_id, {})

    def get_manager_lineup(self, user_id):
        return self._lineups.get(user_id)


def test_low_clause_flagged_and_ok_ignored():
    catalog = {1: _p(1, "Caro", 5_000_000), 2: _p(2, "Bien", 5_000_000)}
    managers = [{"id": 10, "name": "Pepe"}]
    clauses = {10: {
        1: {"clause": 6_000_000, "buy_price": None},   # 5M -> mínimo 10M -> INFRACCIÓN
        2: {"clause": 12_000_000, "buy_price": None},  # 5M -> mínimo 10M -> OK
    }}
    out = check_low_clauses(_Client(managers, clauses=clauses), catalog, RULES)
    assert len(out) == 1 and "Caro" in out[0] and "Pepe" in out[0]


def test_low_clause_skipped_before_active_round():
    catalog = {1: _p(1, "Caro", 5_000_000)}
    managers = [{"id": 10, "name": "Pepe"}]
    clauses = {10: {1: {"clause": 1, "buy_price": None}}}
    # Estamos en jornada 2, las cláusulas se exigen DESPUÉS de la 2 -> aún no aplica.
    client = _Client(managers, clauses=clauses, round_id=2)
    assert check_low_clauses(client, catalog, RULES) == []


def test_expensive_captain_flagged():
    catalog = {1: _p(1, "Barato", 5_000_000), 2: _p(2, "Estrella", 9_000_000)}
    managers = [{"id": 10, "name": "Pepe"}, {"id": 11, "name": "Ana"}]
    lineups = {
        10: {"player_ids": [1], "captain_id": 2, "formation": "4-4-2"},  # capitán 9M -> INFRACCIÓN
        11: {"player_ids": [1], "captain_id": 1, "formation": "4-4-2"},  # capitán 5M -> OK
    }
    out = check_expensive_captains(_Client(managers, lineups=lineups), catalog, RULES)
    assert len(out) == 1 and "Estrella" in out[0] and "Pepe" in out[0]


def test_club_limit_flagged():
    # 4 jugadores del club 100 en el once -> supera el máximo de 3.
    catalog = {i: _p(i, f"J{i}", 1_000_000, team_id=100) for i in range(1, 5)}
    catalog[5] = _p(5, "Otro", 1_000_000, team_id=200)
    managers = [{"id": 10, "name": "Pepe"}]
    lineups = {10: {"player_ids": [1, 2, 3, 4, 5], "captain_id": 1, "formation": "x"}}
    out = check_club_limit(_Client(managers, lineups=lineups), catalog, RULES)
    assert len(out) == 1 and "Pepe" in out[0] and "4 jugadores" in out[0]


def test_club_limit_ok_when_within():
    catalog = {i: _p(i, f"J{i}", 1_000_000, team_id=100) for i in range(1, 4)}
    managers = [{"id": 10, "name": "Pepe"}]
    lineups = {10: {"player_ids": [1, 2, 3], "captain_id": 1, "formation": "x"}}
    assert check_club_limit(_Client(managers, lineups=lineups), catalog, RULES) == []


FINES = {8: 1, 9: 2, 10: 3}


def test_punishment_bottom_three_no_ties():
    # 10 managers, puntos distintos. Los 3 peores pagan 3, 2, 1.
    scores = {f"M{i}": i * 10 for i in range(1, 11)}  # M1 peor (10), M10 mejor (100)
    out = dict(compute_punishment(scores, FINES))
    assert out["M1"] == 3   # último
    assert out["M2"] == 2   # penúltimo
    assert out["M3"] == 1   # antepenúltimo
    assert "M4" not in out  # el resto no paga


def test_punishment_tie_splits_fines():
    # Empate en los dos últimos puestos (9º y 10º): 2€+3€ = 5€ a repartir -> 2,5€ cada uno.
    scores = {"A": 5, "B": 5, "C": 20, "D": 30, "E": 40}  # A y B empatados en lo más bajo
    out = dict(compute_punishment(scores, {3: 1, 4: 2, 5: 3}))
    assert out["A"] == 2.5 and out["B"] == 2.5   # (2+3)/2
    assert out["C"] == 1                          # antepenúltimo
    assert "D" not in out and "E" not in out


if __name__ == "__main__":
    test_required_min_clause_tiers()
    test_low_clause_flagged_and_ok_ignored()
    test_low_clause_skipped_before_active_round()
    test_expensive_captain_flagged()
    test_club_limit_flagged()
    test_club_limit_ok_when_within()
    print("OK: detector de infracciones correcto")
