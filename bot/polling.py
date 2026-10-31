"""Bucle de long-polling: escucha mensajes de Telegram y responde a comandos.

Corre en un hilo daemon junto al scheduler. Solo atiende a TU chat privado: el mensaje o el
clic tiene que venir de un chat de tipo "private" cuyo id, y el de quien escribe, sean
TELEGRAM_CHAT_ID. Ignora a cualquier otro que encuentre el bot, los grupos y los mensajes
editados (editar un comando viejo no lo vuelve a ejecutar).

Nada se repite tras un reinicio o una caída:
  - al arrancar se descarta lo que Telegram acumuló mientras el bot estaba parado
    (getUpdates con offset=-1);
  - el offset se guarda en la BD en cuanto se recoge cada update, ANTES de actuar: si el
    proceso cae a medias, ese update no se vuelve a procesar;
  - un botón de acción (fichar, pujar, vender, ajustes…) de un mensaje enviado antes de
    arrancar este proceso se ignora; los de navegación (menú, actualizar) siguen valiendo.

Para no llenar el chat, los botones de navegación (menú, vistas, «Actualizar») editan el
mensaje en el que están. Las acciones (fichar, vender, cláusula) abren un mensaje propio
desde la lista y su confirmación edita ese mismo mensaje.
"""
from __future__ import annotations

import logging
import time

from bot.actions import handle_callback
from bot.commands import COMMAND_MENU, handle_command, nav, with_nav
from bot.telegram_bot import (
    TelegramError,
    answer_callback_query,
    delete_message,
    edit_message,
    get_updates,
    send_message,
    send_typing,
    set_commands,
)
from config.settings import settings
from core.client import BiwengerClient
from core.redact import describe_error

logger = logging.getLogger(__name__)

# Vistas que tardan un par de segundos: se enseña «escribiendo…» mientras se calculan.
QUICK = {"menu", "mas", "start", "help", "ayuda", "auto"}
# Primer paso de una acción desde una lista: va en un mensaje nuevo para no perder la lista.
OPENS_NEW_MESSAGE = ("bid:", "ask:", "unsnipe:")
# Botones que solo leen: valen aunque su mensaje sea de antes de arrancar el proceso.
READ_ONLY_BUTTONS = ("cmd:",)
OFFSET_KEY = "telegram_offset"
OLD_MESSAGE = "Ese botón es de antes de reiniciar el bot. No he hecho nada: vuelve a pedirlo."


def is_authorized(sender: dict | None, chat: dict | None) -> bool:
    """¿Viene de TU chat privado, escrito por ti?"""
    allowed = str(settings.telegram_chat_id or "").strip()
    chat = chat or {}
    return (
        bool(allowed)
        and chat.get("type") == "private"
        and str(chat.get("id")) == allowed
        and str((sender or {}).get("id")) == allowed
    )


def handle_button(data: str, message_id: int | None, client: BiwengerClient) -> None:
    """Un clic en un botón: navegar (edita el mensaje) o actuar."""
    if data.startswith("cmd:"):
        command = data.split(":", 1)[1]
        if command not in QUICK:
            send_typing()
        reply, buttons = handle_command("/" + command, client)
        _show(message_id, reply, with_nav(command, buttons))
        return

    reply, buttons = handle_callback(data, client)
    if data.startswith("toggle:"):
        # Vuelve a la pantalla de ajustes ya actualizada, con lo que ha cambiado arriba.
        settings_text, settings_buttons = handle_command("/auto", client)
        _show(message_id, f"{reply}\n\n{settings_text}", with_nav("auto", settings_buttons))
    elif data.startswith(OPENS_NEW_MESSAGE):
        send_message(reply, buttons)
    else:
        # Confirmar, ejecutar o cancelar: el mensaje pasa a decir qué ha pasado.
        _show(message_id, reply, buttons or [nav("menu")])


def _show(message_id: int | None, text: str, buttons) -> None:
    if message_id:
        edit_message(message_id, text, buttons)
    else:
        send_message(text, buttons)


def handle_update(update: dict, client: BiwengerClient, started_at: int) -> None:
    """Un update de Telegram: un clic en un botón o un comando de texto."""
    callback = update.get("callback_query")
    if callback:
        message = callback.get("message") or {}
        if not is_authorized(callback.get("from"), message.get("chat")):
            answer_callback_query(callback["id"])
            logger.warning("Botón ignorado: no viene de tu chat privado")
            return
        data = callback.get("data") or ""
        if not data.startswith(READ_ONLY_BUTTONS) and int(message.get("date") or 0) < started_at:
            answer_callback_query(callback["id"], OLD_MESSAGE)
            return
        answer_callback_query(callback["id"])
        handle_button(data, message.get("message_id"), client)
        return

    # Solo mensajes nuevos: un edited_message (o cualquier otro tipo) se ignora.
    message = update.get("message")
    if not message or not is_authorized(message.get("from"), message.get("chat")):
        return
    text = message.get("text") or ""
    if not text.startswith("/"):
        return

    command = text.strip().split()[0].lower().split("@")[0].lstrip("/")
    logger.info("Comando recibido: /%s", command)
    if command == "token":
        reply, _ = handle_command(text, client)
        if len(text.split()) > 1:
            # El mensaje lleva el token en claro: fuera del chat en cuanto está guardado.
            if delete_message(message.get("message_id")):
                reply += "\n🧹 He borrado del chat tu mensaje con el token."
            else:
                reply += "\n⚠️ No he podido borrar tu mensaje con el token: bórralo tú del chat."
        send_message(reply)
        return
    if command not in QUICK:
        send_typing()
    reply, buttons = handle_command(text, client)
    send_message(reply, with_nav(command, buttons))


def _saved_offset() -> int:
    from data.db import get_setting
    try:
        return int(get_setting(OFFSET_KEY) or 0)
    except ValueError:
        return 0


def _save_offset(offset: int) -> None:
    from data.db import set_setting
    set_setting(OFFSET_KEY, str(offset))


def skip_backlog() -> int:
    """Descarta lo que Telegram guardó mientras el bot estaba parado: con offset=-1 devuelve
    solo el último update y olvida los anteriores; el siguiente getUpdates confirma ese
    también. Devuelve el offset desde el que seguir (y lo guarda)."""
    newest = max((u["update_id"] + 1 for u in get_updates(-1, timeout=0)), default=0)
    offset = max(_saved_offset(), newest)
    if offset:
        _save_offset(offset)
    return offset


def run_polling() -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram no configurado: el bot interactivo no arranca.")
        return

    started_at = int(time.time())  # los botones de acción de mensajes anteriores no valen
    client = BiwengerClient()  # cliente propio del hilo de polling (sesión aparte)
    set_commands(COMMAND_MENU)

    offset: int | None = None
    while True:
        try:
            if offset is None:
                offset = skip_backlog()
                logger.info("Bot interactivo de Telegram escuchando comandos.")
            for update in get_updates(offset, timeout=30):
                offset = max(offset, update["update_id"] + 1)
                _save_offset(offset)  # antes de actuar: si algo cae a medias, no se repite
                handle_update(update, client, started_at)
        except TelegramError as exc:  # su texto ya viene sin el token
            logger.warning("Error en el polling de Telegram: %s", exc)
            time.sleep(5)
        except Exception as exc:  # noqa: BLE001 - el bucle nunca debe morir
            logger.warning("Error atendiendo a Telegram: %s", describe_error(exc), exc_info=True)
            time.sleep(5)
