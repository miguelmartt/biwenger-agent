"""Tests del planificador de calendario multi-jornada.

Verifica que (a) parseamos bien `nextGames` de Biwenger sacando rival, local/
visitante y dificultad propia de cada partido, y (b) el mensaje separa rachas
fáciles de rachas duras para ayudar a decidir a quién capitanear o vender."""
from __future__ import annotations

from core.client import _team_fixtures
from core.models import Player, Position, TeamState, UpcomingFixture
from core.services import fixture_calendar_message


def test_team_fixtures_parses_side_opponent_and_difficulty():
    # Equipo 10 juega en casa vs equipo 20, y fuera vs equipo 30.
    teams = {
        "10": {"name": "MiEquipo", "nextGames": [
            {"date": 1, "home": {"id": 10, "difficulty": {"rating": 30}},
             "away": {"id": 20, "name": "RivalFacil"}},
            {"date": 2, "home": {"id": 30, "name": "RivalDuro"},
             "away": {"id": 10, "difficulty": {"rating": 75}}},
        ]},
        "20": {"name": "RivalFacil", "nextGames": []},
    }
    out = _team_fixtures(teams, weeks=5)
    runs = out[10]
    assert len(runs) == 2
    assert runs[0].opponent == "RivalFacil" and runs[0].is_home is True
    assert runs[0].difficulty == 30.0
    assert runs[1].opponent == "RivalDuro" and runs[1].is_home is False
    assert runs[1].difficulty == 75.0
    # Un equipo sin nextGames no aparece en el mapa.
    assert 20 not in out


def test_team_fixtures_respects_weeks_limit():
    teams = {"10": {"name": "M", "nextGames": [
        {"home": {"id": 10, "difficulty": {"rating": 50}}, "away": {"id": i}} for i in range(10)
    ]}}
    assert len(_team_fixtures(teams, weeks=3)[10]) == 3


class _Client:
    def __init__(self, players, fixtures_by_team):
        self._players = players
        self._fixtures = fixtures_by_team

    def get_all_players(self):
        return {p.id: p for p in self._players}

    def get_my_team(self):
        return TeamState(team_id=1, name="Hustle Hard", balance=0,
                         player_ids=[p.id for p in self._players], owned={})

    def get_player(self, pid):
        return {p.id: p for p in self._players}[pid]

    def get_team_fixtures(self, weeks=5):
        return self._fixtures


def _p(pid, name, team_id):
    return Player(id=pid, name=name, position=Position.FORWARD, price=1_000_000, team_id=team_id)


def _run(*diffs):
    return [UpcomingFixture(opponent="X", is_home=True, difficulty=d) for d in diffs]


def test_calendar_splits_easy_and_hard_runs():
    players = [_p(1, "Facilon", team_id=10), _p(2, "Sufridor", team_id=20)]
    fixtures = {10: _run(30, 35, 30, 40, 38), 20: _run(70, 65, 72, 60, 68)}
    out = fixture_calendar_message(_Client(players, fixtures))
    assert "Racha fácil" in out and "Facilon" in out
    assert "Racha dura" in out and "Sufridor" in out


def test_calendar_handles_no_fixtures():
    players = [_p(1, "A", team_id=10)]
    out = fixture_calendar_message(_Client(players, {}))  # sin calendario aún
    assert "Aún no hay calendario" in out


if __name__ == "__main__":
    test_team_fixtures_parses_side_opponent_and_difficulty()
    test_team_fixtures_respects_weeks_limit()
    test_calendar_splits_easy_and_hard_runs()
    test_calendar_handles_no_fixtures()
    print("OK: calendario correcto")
