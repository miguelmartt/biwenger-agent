"""Utilidades de Telegram: envío y edición de mensajes (con botones opcionales),
registro del menú de comandos, lectura de updates y respuesta a botones.

Los textos son planos salvo que vengan marcados como `Html(...)`: entonces se envían
con parse_mode HTML (negritas, <code>, <pre>…) y quien los construye escapa los datos.

La URL de la API lleva el token del bot (`/bot<TOKEN>/…`) y requests la copia en el texto
de sus excepciones: aquí los fallos se registran solo con su tipo y su código HTTP, y
`get_updates` lanza un TelegramError limpio en vez de la excepción original.
"""
from __future__ import annotations

import html
import json
import logging
import re

import requests

from config.settings import settings
from core.redact import describe_error

logger = logging.getLogger(__name__)

API = "https://api.telegram.org"
# Solo mensajes nuevos y clics en botones: ni mensajes editados ni nada más.
ALLOWED_UPDATES = ["message", "callback_query"]


class TelegramError(RuntimeError):
    """Fallo hablando con Telegram, con un texto que no incluye el token."""

# Un "botón" es (texto_visible, callback_data). Los botones pueden ir como lista plana
# (uno por fila, el formato de siempre) o como lista de filas para ponerlos en cuadrícula.
Button = tuple[str, str]
Buttons = list[Button] | list[list[Button]]


class Html(str):
    """Texto que se envía como HTML de Telegram."""


def grid(buttons: list[Button], columns: int = 2) -> list[list[Button]]:
    """Reparte una lista de botones en filas de `columns`."""
    return [buttons[i:i + columns] for i in range(0, len(buttons), columns)]


def _base() -> str | None:
    if not settings.telegram_bot_token:
        return None
    return f"{API}/bot{settings.telegram_bot_token}"


def _inline_keyboard(buttons: Buttons | None) -> dict | None:
    if not buttons:
        return None
    rows = buttons if isinstance(buttons[0], list) else [[b] for b in buttons]
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


# Telegram rechaza mensajes de más de 4096 caracteres; troceamos por debajo.
TELEGRAM_MAX = 4000


def _split_text(text: str, limit: int = TELEGRAM_MAX) -> list[str]:
    """Parte un texto largo en trozos <= limit, respetando saltos de línea (y, si
    una sola línea es enorme, la corta en crudo)."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # una línea gigante: córtala en crudo
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        add = (current + "\n" + line) if current else line
        if len(add) > limit:
            chunks.append(current)
            current = line
        else:
            current = add
    if current:
        chunks.append(current)
    return chunks


def _plain(text: str) -> str:
    """HTML → texto plano, por si Telegram rechaza el marcado."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _post(method: str, payload: dict) -> requests.Response | None:
    base = _base()
    if not base:
        return None
    try:
        return requests.post(f"{base}/{method}", json=payload, timeout=10)
    except requests.RequestException as exc:
        logger.warning("Telegram %s falló: %s", method, describe_error(exc))
        return None


def _send_with_fallback(method: str, payload: dict, is_html: bool) -> requests.Response | None:
    """Envía; si el HTML no es válido para Telegram, reintenta como texto plano."""
    if is_html:
        payload = {**payload, "parse_mode": "HTML"}
    resp = _post(method, payload)
    if resp is not None and resp.status_code == 400 and is_html and "can't parse entities" in resp.text:
        logger.warning("Telegram rechazó el HTML; lo mando como texto plano")
        payload = {k: v for k, v in payload.items() if k != "parse_mode"}
        payload["text"] = _plain(payload["text"])
        resp = _post(method, payload)
    return resp


def send_message(text: str, buttons: Buttons | None = None) -> None:
    if not _base() or not settings.telegram_chat_id:
        logger.warning("Telegram no configurado; mensaje no enviado:\n%s", text)
        return
    is_html = isinstance(text, Html)
    parts = _split_text(text)
    kb = _inline_keyboard(buttons)
    for i, part in enumerate(parts):
        payload = {"chat_id": settings.telegram_chat_id, "text": part,
                   "disable_web_page_preview": True}
        # Los botones solo en el último trozo.
        if kb and i == len(parts) - 1:
            payload["reply_markup"] = kb
        resp = _send_with_fallback("sendMessage", payload, is_html)
        if resp is not None and resp.status_code != 200:
            logger.error("Fallo enviando mensaje a Telegram: %s", resp.text[:200])


def edit_message(message_id: int, text: str, buttons: Buttons | None = None) -> None:
    """Cambia el texto y los botones de un mensaje ya enviado (el panel no llena el chat).
    Si el texto no cabe en un mensaje, lo manda nuevo."""
    if not _base() or not settings.telegram_chat_id:
        return
    if len(text) > TELEGRAM_MAX:
        send_message(text, buttons)
        return
    payload = {"chat_id": settings.telegram_chat_id, "message_id": message_id, "text": text,
               "disable_web_page_preview": True}
    kb = _inline_keyboard(buttons)
    if kb:
        payload["reply_markup"] = kb
    resp = _send_with_fallback("editMessageText", payload, isinstance(text, Html))
    if resp is not None and resp.status_code != 200 and "message is not modified" not in resp.text:
        logger.warning("No pude editar el mensaje (%s); lo mando nuevo", resp.text[:200])
        send_message(text, buttons)


def send_typing() -> None:
    """«escribiendo…» mientras se calcula algo lento (alineación, mercado)."""
    if settings.telegram_chat_id:
        _post("sendChatAction", {"chat_id": settings.telegram_chat_id, "action": "typing"})


def answer_callback_query(callback_id: str, text: str | None = None) -> None:
    """Confirma a Telegram que recibimos el clic de un botón (quita el 'cargando')."""
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    _post("answerCallbackQuery", payload)


def delete_message(message_id: int | None) -> bool:
    """Borra un mensaje del chat (p. ej. el tuyo con /token <secreto>). True si se borró."""
    if not message_id or not settings.telegram_chat_id:
        return False
    resp = _post("deleteMessage", {"chat_id": settings.telegram_chat_id, "message_id": message_id})
    return resp is not None and resp.status_code == 200


def set_commands(commands: list[tuple[str, str]]) -> None:
    _post("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]})


def get_updates(offset: int | None, timeout: int = 30) -> list[dict]:
    """Updates nuevos de Telegram. Con offset=-1 devuelve solo el último y Telegram olvida
    todos los anteriores (así se descarta lo acumulado mientras el bot estaba parado)."""
    base = _base()
    if not base:
        return []
    try:
        resp = requests.get(
            f"{base}/getUpdates",
            params={"offset": offset, "timeout": timeout, "allowed_updates": json.dumps(ALLOWED_UPDATES)},
            timeout=timeout + 10,
        )
    except requests.RequestException as exc:
        raise TelegramError(f"getUpdates falló: {describe_error(exc)}") from None
    if resp.status_code != 200:
        raise TelegramError(f"getUpdates respondió {resp.status_code}")
    return resp.json().get("result", []) or []
