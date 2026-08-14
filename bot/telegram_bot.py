"""Utilidades de Telegram: envío de mensajes (con botones opcionales),
registro del menú de comandos, lectura de updates y respuesta a botones."""
from __future__ import annotations

import logging

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

API = "https://api.telegram.org"

# Un "botón" es (texto_visible, callback_data). Se envía una fila por botón.
Button = tuple[str, str]


def _base() -> str | None:
    if not settings.telegram_bot_token:
        return None
    return f"{API}/bot{settings.telegram_bot_token}"


def _inline_keyboard(buttons: list[Button] | None) -> dict | None:
    if not buttons:
        return None
    return {"inline_keyboard": [[{"text": t, "callback_data": d}] for t, d in buttons]}


def send_message(text: str, buttons: list[Button] | None = None) -> None:
    base = _base()
    if not base or not settings.telegram_chat_id:
        logger.warning("Telegram no configurado; mensaje no enviado:\n%s", text)
        return
    payload = {"chat_id": settings.telegram_chat_id, "text": text}
    kb = _inline_keyboard(buttons)
    if kb:
        payload["reply_markup"] = kb
    resp = requests.post(f"{base}/sendMessage", json=payload, timeout=10)
    if resp.status_code != 200:
        logger.error("Fallo enviando mensaje a Telegram: %s", resp.text[:200])


def answer_callback_query(callback_id: str, text: str | None = None) -> None:
    """Confirma a Telegram que recibimos el clic de un botón (quita el 'cargando')."""
    base = _base()
    if not base:
        return
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    try:
        requests.post(f"{base}/answerCallbackQuery", json=payload, timeout=10)
    except requests.RequestException as exc:
        logger.warning("No pude responder al callback: %s", exc)


def set_commands(commands: list[tuple[str, str]]) -> None:
    base = _base()
    if not base:
        return
    try:
        requests.post(
            f"{base}/setMyCommands",
            json={"commands": [{"command": c, "description": d} for c, d in commands]},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.warning("No pude registrar los comandos: %s", exc)


def get_updates(offset: int | None, timeout: int = 30) -> list[dict]:
    base = _base()
    if not base:
        return []
    resp = requests.get(
        f"{base}/getUpdates",
        params={"offset": offset, "timeout": timeout},
        timeout=timeout + 10,
    )
    resp.raise_for_status()
    return resp.json().get("result", []) or []
