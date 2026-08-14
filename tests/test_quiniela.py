"""Tests del pronóstico de la quiniela (1X2 según dificultad de Biwenger)."""
from __future__ import annotations

from core.models import Fixture
from quiniela.predictor import predict_result, quiniela


def test_home_favourite_when_much_easier():
    # Celta(18) vs Osasuna(59): local mucho más fácil -> gana local, alta confianza.
    p = predict_result(Fixture("Celta", "Osasuna", 18, 59))
    assert p.pick == "1" and p.confidence == "alta"


def test_away_favourite():
    # Alavés(51) vs Getafe(29): visitante más fácil -> gana visitante.
    p = predict_result(Fixture("Alavés", "Getafe", 51, 29))
    assert p.pick == "2"


def test_draw_when_close():
    # Sevilla(41) vs Rayo(42): parejo -> empate, baja confianza.
    p = predict_result(Fixture("Sevilla", "Rayo Vallecano", 41, 42))
    assert p.pick == "X" and p.confidence == "baja"


def test_missing_difficulty_is_draw():
    p = predict_result(Fixture("A", "B", None, None))
    assert p.pick == "X"


def test_quiniela_returns_one_prediction_per_game():
    fixtures = [Fixture("A", "B", 30, 60), Fixture("C", "D", 50, 50)]
    assert len(quiniela(fixtures)) == 2


if __name__ == "__main__":
    for fn in [test_home_favourite_when_much_easier, test_away_favourite, test_draw_when_close,
               test_missing_difficulty_is_draw, test_quiniela_returns_one_prediction_per_game]:
        fn()
    print("OK: pronóstico de quiniela correcto")
