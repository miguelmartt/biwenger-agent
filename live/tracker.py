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

# Tipos de evento de Biwenger (el enum de su app): 1 gol, 2 gol de penalti, 3 asistencia,
# 4 sale, 5 entra, 6 amarilla, 7 roja, 8 doble amarilla, 9 gol en propia, 10 palo,
# 11 penalti fallado, 12 penalti parado, 13 gol anulado, 14 lesión, 15 y 17 tanda de
# penaltis, 16 penalti cometido. Solo avisamos de lo que importa; cambios, amarillas y
# palos no molestan.
EVENT_LABEL = {
    1: "⚽ ¡GOL!",
    2: "⚽ ¡GOL de penalti!",
    3: "🅰️ Asistencia",
    7: "🟥 Tarjeta roja",
    8: "🟥 Expulsado por doble amarilla",
    9: "😬 Gol en propia puerta",
    11: "❌ Penalti fallado",
    14: "🚑 Lesionado",
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
