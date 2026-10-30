"""Tests del seguimiento en vivo: de dónde salen los eventos, dedup y formateo de avisos."""
from __future__ import annotations

import time

from core.client import BiwengerClient
from core.models import LivePlayerEvent
from data.db import filter_new_live_events, init_db
from live.tracker import _format

# Forma real de rounds/la-liga/{id} durante un partido (oct 2026): cada lado trae sus
# jugadores en reports, con events solo si ya ha pasado algo.
ROUND = {
    "id": 4909,
    "games": [
        {
            "id": 50818, "status": "secondTime", "minute": "70",
            "home": {"reports": [
                {"player": {"id": 35156, "name": "Pau Navarro"}, "points": None,
                 "events": [{"type": 1, "period": "secondTime", "metadata": 55},
                            {"type": 6, "period": "secondTime", "metadata": 61}]},
                {"player": {"id": 99, "name": "De otro"}, "points": None,
                 "events": [{"type": 1, "period": "firstTime", "metadata": 12}]},
            ]},
            "away": {"reports": [
                {"player": {"id": 2247, "name": "Bellerín"}, "points": None},
                [],
            ]},
        },
        {"id": 50819, "status": "preview", "home": {}, "away": {"reports": None}},
    ],
}


def test_live_events_come_from_the_round_and_only_mine():
    client = BiwengerClient(app_version="632", league_id="1", team_id="2")
    client._competition_cache = (time.monotonic(), {"activeEvents": [{"type": "round", "id": 4909}]})
    calls: list[str] = []
    client._request = lambda method, url, **kwargs: calls.append(url) or {"data": ROUND}

    events = client.get_my_live_events([35156, 2247])

    assert calls == ["https://biwenger.as.com/api/v2/rounds/la-liga/4909"]
    assert [(e.player_name, e.event_type, e.minute, e.round_id) for e in events] == [
        ("Pau Navarro", 1, 55, 4909), ("Pau Navarro", 6, 61, 4909),
    ]


def test_new_events_are_not_repeated():
    init_db()
    gol = LivePlayerEvent(player_id=7001, player_name="Pépé", event_type=1, minute=23, round_id=4899)
    # Primera vez: es nuevo.
    assert len(filter_new_live_events([gol])) == 1
    # Segunda vez (el job corre otra vez a los 5 min): ya no se repite.
    assert len(filter_new_live_events([gol])) == 0


def test_goal_assist_and_red_card_are_formatted():
    gol = LivePlayerEvent(7002, "Pépé", 1, 45, 4899)
    asis = LivePlayerEvent(7002, "Pépé", 3, 60, 4899)
    assert "GOL" in _format(gol) and "Pépé" in _format(gol) and "45'" in _format(gol)
    assert "Asistencia" in _format(asis)
    assert "roja" in _format(LivePlayerEvent(7002, "Pépé", 7, 80, 4899))
    assert "Lesionado" in _format(LivePlayerEvent(7002, "Pépé", 14, 17, 4899))


def test_substitutions_and_yellow_cards_are_ignored():
    # 4 sale, 5 entra, 6 amarilla: no molestan. El 5 llegó a avisarse como «roja».
    for event_type in (4, 5, 6):
        assert _format(LivePlayerEvent(7003, "X", event_type, 30, 4899)) is None
