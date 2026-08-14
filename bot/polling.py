"""Bucle de long-polling: escucha mensajes de Telegram y responde a comandos.

Corre en un hilo daemon junto al scheduler. Solo atiende al chat autorizado
(TELEGRAM_CHAT_ID); ignora a cualquier otro que encuentre el bot.
"""
from __future__ import annotations

import logging
import time

from bot.actions import handle_callback
from bot.commands import COMMAND_MENU, handle_command
from bot.telegram_bot import answer_callback_query, get_updates, send_message, set_commands
from config.settings import settings
from core.client import BiwengerClient

logger = logging.getLogger(__name__)


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
                    chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id"))
                    answer_callback_query(callback["id"])
                    if chat_id != str(settings.telegram_chat_id):
                        continue
                    data = callback.get("data", "")
                    if data.startswith("cmd:"):
                        # Botón del menú diario -> ejecuta el comando correspondiente.
                        reply, buttons = handle_command("/" + data.split(":", 1)[1], client)
                    else:
                        # Botón de acción (fichar / cláusula / confirmar / cancelar).
                        reply, buttons = handle_callback(data, client)
                    send_message(reply, buttons)
                    continue

                # Mensaje de texto (comando).
                message = update.get("message") or update.get("edited_message") or {}
                chat_id = str((message.get("chat") or {}).get("id"))
                text = message.get("text") or ""
                if chat_id != str(settings.telegram_chat_id):
                    continue  # solo respondemos al chat autorizado
                if not text.startswith("/"):
                    continue

                logger.info("Comando recibido: %s", text)
                reply, buttons = handle_command(text, client)
                send_message(reply, buttons)
        except Exception as exc:  # noqa: BLE001 - el bucle nunca debe morir
            logger.warning("Error en el polling de Telegram: %s", exc)
            time.sleep(5)
