"""Bucle de long-polling: escucha mensajes de Telegram y responde a comandos.

Corre en un hilo daemon junto al scheduler. Solo atiende al chat autorizado
(TELEGRAM_CHAT_ID); ignora a cualquier otro que encuentre el bot.

Para no llenar el chat, los botones de navegación (menú, vistas, «Actualizar») editan el
mensaje en el que están. Las acciones (fichar, vender, auto-pujar) abren un mensaje propio
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
OPENS_NEW_MESSAGE = ("bid:", "ask:", "snipe:", "unsnipe:")


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
        # Confirmar o cancelar: el mensaje de confirmación pasa a decir qué ha pasado.
        _show(message_id, reply, buttons or [nav("menu")])


def _show(message_id: int | None, text: str, buttons) -> None:
    if message_id:
        edit_message(message_id, text, buttons)
    else:
        send_message(text, buttons)


def run_polling() -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram no configurado: el bot interactivo no arranca.")
        return

    client = BiwengerClient()  # cliente propio del hilo de polling (sesión aparte)
    set_commands(COMMAND_MENU)
    logger.info("Bot interactivo de Telegram escuchando comandos.")

    offset: int | None = None
    while True:
        try:
            for update in get_updates(offset, timeout=30):
                offset = update["update_id"] + 1

                # Clic en un botón.
                callback = update.get("callback_query")
                if callback:
                    message = callback.get("message") or {}
                    chat_id = str((message.get("chat") or {}).get("id"))
                    answer_callback_query(callback["id"])
                    if chat_id != str(settings.telegram_chat_id):
                        continue
                    handle_button(callback.get("data", ""), message.get("message_id"), client)
                    continue

                # Mensaje de texto (comando).
                message = update.get("message") or update.get("edited_message") or {}
                chat_id = str((message.get("chat") or {}).get("id"))
                text = message.get("text") or ""
                if chat_id != str(settings.telegram_chat_id):
                    continue  # solo respondemos al chat autorizado
                if not text.startswith("/"):
                    continue

                logger.info("Comando recibido: %s", text.split()[0])
                command = text.strip().split()[0].lower().split("@")[0].lstrip("/")
                if command not in QUICK:
                    send_typing()
                reply, buttons = handle_command(text, client)
                send_message(reply, with_nav(command, buttons) if command != "token" else None)
        except TelegramError as exc:  # su texto ya viene sin el token
            logger.warning("Error en el polling de Telegram: %s", exc)
            time.sleep(5)
        except Exception as exc:  # noqa: BLE001 - el bucle nunca debe morir
            logger.warning("Error atendiendo a Telegram: %s", describe_error(exc), exc_info=True)
            time.sleep(5)
