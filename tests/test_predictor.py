"""Tests del predictor V2: baseline de temporada pasada y dificultad de partido."""
from __future__ import annotations

from core.models import Player, Position
from lineup.predictor import predict


def _p(**kw):
    base = dict(id=1, name="X", position=Position.MIDFIELDER, price=1_000_000)
    base.update(kw)
    return Player(**base)


def test_injured_returns_zero():
    assert predict(_p(fitness=[10, 10, 10], status="injured")) == 0.0


def test_uses_last_season_when_no_current_data():
    # Sin fitness de la temporada actual, usa pointsLastSeason / 38 como baseline.
    p = _p(fitness=[], points_last_season=190)  # 190/38 = 5 por partido
    # dificultad neutra (None -> factor 1)
    assert predict(p) == 5.0


def test_easy_fixture_beats_hard_fixture():
    easy = _p(fitness=[], points_last_season=190, fixture_difficulty=10)   # fácil
    hard = _p(fitness=[], points_last_season=190, fixture_difficulty=90)   # difícil
    assert predict(easy) > predict(hard)


def test_current_form_takes_priority_over_last_season():
    # Con datos de la temporada en curso, se usan esos (no el año pasado).
    p = _p(fitness=[8, 8, 8, 8, 8], points_last_season=0, fixture_difficulty=50)
    assert predict(p) == 8.0  # dificultad 50 = neutra


if __name__ == "__main__":
    test_injured_returns_zero()
    test_uses_last_season_when_no_current_data()
    test_easy_fixture_beats_hard_fixture()
    test_current_form_takes_priority_over_last_season()
    print("OK: predictor V2 correcto")
