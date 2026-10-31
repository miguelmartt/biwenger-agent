"""Avisos cuando un trabajo programado deja de funcionar, y cuando vuelve.

Un aviso por cambio, nunca uno por ejecución: si la sincronización con Biwenger falla cada
media hora durante días, llega un mensaje al empezar y otro al volver a funcionar. Los
trabajos que corren cada pocos minutos necesitan dos fallos seguidos (un corte de red
puntual no merece un mensaje). Si caduca el token, fallan todos a la vez: un único aviso.
"""
from __future__ import annotations

import html
import logging
from collections.abc import Callable

from core.client import BiwengerTokenExpired
from core.redact import describe_error, redact

logger = logging.getLogger(__name__)

LABELS = {
    "sync_state": "sincronizar con Biwenger (precios, lesiones y chollos)",
    "daily_report": "mandarte el parte diario",
    "live_tracking": "seguir tus partidos en directo",
    "save_predictions": "guardar las predicciones de la jornada",
    "postmatch": "preparar el resumen de la jornada",
    "sniper": "lanzar las auto-pujas",
    "token_health": "comprobar el token",
    "rival_digest": "preparar el resumen de rivales",
    "daily_analysis": "analizar el reglamento",
    "daily_analysis_boot": "analizar el reglamento",
    "auto_lineup": "poner la alineación automática",
}
FREQUENT = {"sniper", "live_tracking"}
TOKEN = "token"
TOKEN_MESSAGE = (
    "⚠️ El token de Biwenger ha caducado y estoy parado. Renuévalo sin tocar el servidor: entra en "
    "biwenger.as.com, abre la consola (F12) y copia localStorage.getItem('satellizer_token'); "
    "luego mándame /token &lt;el_token&gt;."
)


class JobHealth:
    def __init__(self, notify: Callable[[str], None]):
        self.notify = notify
        self.failures: dict[str, int] = {}
        self.alerted: set[str] = set()

    def on_error(self, job_id: str, exc: BaseException) -> None:
        logger.error("Job %s falló: %s", job_id, describe_error(exc))
        count = self.failures.get(job_id, 0) + 1
        self.failures[job_id] = count
        key = TOKEN if isinstance(exc, BiwengerTokenExpired) else job_id
        if key in self.alerted or count < (2 if job_id in FREQUENT else 1):
            return
        self.alerted.add(key)
        self._send(TOKEN_MESSAGE if key == TOKEN else
                   f"⚠️ No he podido {LABELS.get(job_id, job_id)}: {_short(exc)}\n"
                   "Te aviso cuando vuelva a funcionar.")

    def on_success(self, job_id: str) -> None:
        self.failures.pop(job_id, None)
        if TOKEN in self.alerted:  # si un trabajo va bien, el token vuelve a valer
            self.alerted.discard(TOKEN)
            self._send("✅ El token de Biwenger vuelve a funcionar. Sigo con lo mío.")
        if job_id in self.alerted:
            self.alerted.discard(job_id)
            self._send(f"✅ Ya puedo volver a {LABELS.get(job_id, job_id)}.")

    def _send(self, text: str) -> None:
        try:
            self.notify(text)
        except Exception:  # noqa: BLE001 - un aviso que no sale no debe tumbar el scheduler
            logger.exception("No pude mandar el aviso de salud")


def _short(exc: BaseException) -> str:
    """El error para el aviso de Telegram: sin secretos y escapado (el aviso va en HTML)."""
    text = redact(str(exc)) or type(exc).__name__
    text = text if len(text) <= 200 else text[:200] + "…"
    return html.escape(text, quote=False)
