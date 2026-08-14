"""Tests del motor de economía: tendencias, chollos y alertas de venta."""
from __future__ import annotations

from core.models import Player, Position
from economy.analyzer import build_report
from economy.trends import analyze_trend


def _p(pid, name, price, history, fitness=None, last_season=190, difficulty=50):
    return Player(
        id=pid, name=name, position=Position.MIDFIELDER, price=price,
        price_history=history, fitness=fitness or [], points_last_season=last_season,
        fixture_difficulty=difficulty,
    )


def test_trend_detects_rising():
    hist = [(260810, 1_000_000), (260811, 1_020_000), (260812, 1_050_000), (260813, 1_090_000)]
    assert analyze_trend(hist).state == "subiendo"


def test_trend_detects_falling():
    hist = [(260810, 1_100_000), (260811, 1_060_000), (260812, 1_020_000), (260813, 980_000)]
    assert analyze_trend(hist).state == "bajando"


def test_trend_no_data():
    assert analyze_trend([(260813, 1_000_000)]).state == "sin_datos"


def test_sell_alerts_flag_my_falling_players():
    falling = [(260810, 1_100_000), (260811, 1_060_000), (260812, 1_020_000), (260813, 980_000)]
    stable = [(260810, 1_000_000), (260811, 1_001_000), (260812, 999_000), (260813, 1_000_000)]
    mine = [_p(1, "Cae", 980_000, falling), _p(2, "Estable", 1_000_000, stable)]
    report = build_report(mine, market_players=[], available_budget=10_000_000)
    sold = {a.player.name for a in report.sell}
    assert "Cae" in sold and "Estable" not in sold


def test_bargains_prefer_value_and_exclude_falling():
    rising = [(260810, 900_000), (260811, 950_000), (260812, 1_000_000), (260813, 1_060_000)]
    falling = [(260810, 6_000_000), (260811, 5_600_000), (260812, 5_200_000), (260813, 4_900_000)]
    market = [
        _p(10, "CholloBarato", 1_000_000, rising, last_season=190),   # buen valor, subiendo
        _p(11, "CaroQueCae", 5_000_000, falling, last_season=190),    # caro y cayendo -> fuera
    ]
    report = build_report(my_players=[], market_players=market, available_budget=10_000_000)
    names = [a.player.name for a in report.bargains]
    assert "CholloBarato" in names
    assert "CaroQueCae" not in names  # los que caen no se recomiendan comprar


if __name__ == "__main__":
    for fn in [test_trend_detects_rising, test_trend_detects_falling, test_trend_no_data,
               test_sell_alerts_flag_my_falling_players, test_bargains_prefer_value_and_exclude_falling]:
        fn()
    print("OK: motor de economía correcto")
