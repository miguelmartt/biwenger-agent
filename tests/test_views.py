"""Tests de las vistas del bot: formato de importes y fechas, la clasificación y el parte diario."""
from __future__ import annotations

from datetime import datetime

from bot import views
from core import services
from core.models import Player, Position, TeamState

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=views.TZ).timestamp()  # sábado 31 oct, 9:00


def test_money_and_change():
    assert views.money(1_690_000) == "1,69 M€"
    assert views.money(17_800_000) == "17,8 M€"
    assert views.money(850_000) == "850 k€"
    assert views.change(300_000) == "▲ 300 k€"
    assert views.change(-1_200_000) == "▼ 1,20 M€"
    assert views.change(0) == "="


def test_when_and_countdown():
    assert views.when(NOW + 12 * 3600, NOW) == "hoy 21:00"
    assert views.when(NOW + 29 * 3600, NOW) == "mañana 14:00"
    assert views.when(NOW + 2 * 86400 + 5 * 3600, NOW) == "lun 2 nov 14:00"
    assert views.countdown(2 * 86400 + 4 * 3600) == "2 d 4 h"
    assert views.countdown(3 * 3600 + 20 * 60) == "3 h 20 min"
    assert views.countdown(45 * 60) == "45 min"


def test_round_status_line():
    games = [{"date": NOW + 12 * 3600, "status": "pending"}]
    active = {"name": "Jornada 11"}
    assert "empieza hoy 21:00" in views.round_status_line(active, games, NOW)
    assert "en juego" in views.round_status_line(active, games, NOW + 13 * 3600)
    done = [{"date": NOW - 3600, "status": "finished"}]
    assert "terminada" in views.round_status_line(active, done, NOW)
    assert "No hay jornada" in views.round_status_line(None, [], NOW)


STANDINGS = [
    {"id": 1, "name": "Los Galácticos", "points": 980, "position": 1, "me": False},
    {"id": 2, "name": "Hustle Hard", "points": 946, "position": 2, "me": True},
    {"id": 3, "name": "Rayo & Cía", "points": 940, "position": 3, "me": False},
]


def test_position_line():
    me = STANDINGS[1]
    assert views.position_line(STANDINGS, me) == (
        "Vas <b>2.º</b> de 3 con 946 pts · a 34 del líder · 6 por encima del 3.º"
    )
    leader = dict(STANDINGS[0], me=True)
    assert "34 pts de ventaja sobre el 2.º" in views.position_line([leader] + STANDINGS[1:], leader)


class _Client:
    def __init__(self):
        self.players = {
            1: Player(id=1, name="Portero", position=Position.GOALKEEPER, price=2_000_000, team_id=10),
            2: Player(id=2, name="Lesionado", position=Position.FORWARD, price=9_000_000,
                      price_increment=-200_000, status="injured", team_id=11),
        }

    def get_all_players(self):
        return self.players

    def get_my_team(self):
        return TeamState(team_id=2, name="Hustle Hard", balance=1_690_000, player_ids=[1, 2])

    def get_standings(self):
        return STANDINGS

    def get_active_round(self):
        return {"id": 4909, "name": "Jornada 11", "games": [{"date": 4_102_444_800, "status": "pending"}]}

    def get_offers(self):
        return [{"id": 1}]


def test_standings_view_marks_me_and_escapes_names():
    text = views.standings_view(_Client())
    assert "▶ 2 Hustle Hard" in text
    assert "Rayo &amp; Cía" in text and "Rayo & Cía" not in text


def test_daily_report_has_everything_in_one_message(monkeypatch):
    urgent = {"player": _Client().players[2], "reasons": ["lleva 3 días bajando"], "urgency": 3.0}
    monkeypatch.setattr(services, "sell_recommendations", lambda client, catalog=None: [urgent])
    text = views.daily_report(_Client())
    assert text.startswith("☀️")
    assert "Vas <b>2.º</b> de 3" in text
    assert "Jornada 11" in text
    assert "Saldo <b>1,69 M€</b>" in text and "▼ 200 k€" in text
    assert "🤕 Lesionado lesionado" in text
    assert "1 oferta por tus jugadores" in text
    assert "lleva 3 días bajando" in text
