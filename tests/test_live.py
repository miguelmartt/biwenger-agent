"""Tests del seguimiento en vivo: dedup de eventos y formateo de avisos."""
from __future__ import annotations

from core.models import LivePlayerEvent
from data.db import filter_new_live_events, init_db
from live.tracker import _format


def test_new_events_are_not_repeated():
    init_db()
    gol = LivePlayerEvent(player_id=7001, player_name="Pépé", event_type=1, minute=23, round_id=4899)
    # Primera vez: es nuevo.
    assert len(filter_new_live_events([gol])) == 1
    # Segunda vez (el job corre otra vez a los 5 min): ya no se repite.
    assert len(filter_new_live_events([gol])) == 0


def test_goal_and_assist_are_formatted():
    gol = LivePlayerEvent(7002, "Pépé", 1, 45, 4899)
    asis = LivePlayerEvent(7002, "Pépé", 3, 60, 4899)
    assert "GOL" in _format(gol) and "Pépé" in _format(gol) and "45'" in _format(gol)
    assert "Asistencia" in _format(asis)


def test_yellow_card_is_ignored():
    # Amarilla (tipo 4) no genera aviso (no molestar con cosas menores).
    assert _format(LivePlayerEvent(7003, "X", 4, 30, 4899)) is None


if __name__ == "__main__":
    test_new_events_are_not_repeated()
    test_goal_and_assist_are_formatted()
    test_yellow_card_is_ignored()
    print("OK: seguimiento en vivo correcto")
