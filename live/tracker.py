"""Seguimiento en vivo de tus jugadores durante los partidos.

Usa los datos EN VIVO de Biwenger (no Flashscore, que es frágil): durante un
partido, Biwenger va rellenando los eventos (goles, asistencias, tarjetas) de
cada jugador. Este módulo detecta los eventos NUEVOS de tu plantilla y genera
los avisos, sin repetir ninguno.
"""
from __future__ import annotations

import logging

from core.client import BiwengerClient
from core.models import LivePlayerEvent
from data.db import filter_new_live_events

logger = logging.getLogger(__name__)

# Qué eventos avisamos y cómo. Los que no estén aquí (p.ej. amarilla) no molestan.
EVENT_LABEL = {
    1: "⚽ ¡GOL!",
    2: "⚽ ¡GOL de penalti!",
    3: "🅰️ Asistencia",
    5: "🟥 Tarjeta roja",
}


def _format(event: LivePlayerEvent) -> str | None:
    label = EVENT_LABEL.get(event.event_type)
    if label is None:
        return None
    minute = f" ({event.minute}')" if event.minute else ""
    return f"{label} — {event.player_name}{minute}"


def live_updates(client: BiwengerClient, my_player_ids: list[int]) -> list[str]:
    """Devuelve los mensajes de eventos NUEVOS de tus jugadores (vacío si nada)."""
    events = client.get_my_live_events(my_player_ids)
    fresh = filter_new_live_events(events)
    messages = [m for e in fresh if (m := _format(e))]
    return messages
