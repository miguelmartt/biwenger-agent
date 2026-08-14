"""Comandos interactivos del bot de Telegram.

Cada comando devuelve el texto de respuesta. La lógica real vive en
core.services (compartida con los avisos automáticos).
"""
from __future__ import annotations

import logging

from config.settings import settings
from core import services
from core.client import BiwengerClient

logger = logging.getLogger(__name__)

Button = tuple[str, str]

# Saludo diario con botones: el usuario elige qué ver (menos mensajes, más control).
# Se personaliza con OWNER_NAME si está puesto; si no, saludo genérico.
_WHO = f", {settings.owner_name}" if settings.owner_name else ""
DAILY_GREETING = f"👋 ¡Buenas{_WHO}! ¿Qué quieres ver hoy?"
MENU_BUTTONS: list[Button] = [
    ("⚽ Alineación óptima", "cmd:alineacion"),
    ("💰 Mercado y economía", "cmd:mercado"),
    ("⏱️ Vender (timing)", "cmd:vender"),
    ("📅 Calendario", "cmd:calendario"),
    ("🕵️ Diferenciales", "cmd:diferenciales"),
    ("🎯 Quiniela", "cmd:quiniela"),
    ("📊 Resumen jornada", "cmd:resumen"),
    ("👥 Mi equipo", "cmd:equipo"),
    ("ℹ️ ¿Qué hace cada cosa?", "cmd:help"),
]

HELP_TEXT = (
    "ℹ️ Esto es lo que puedo hacer por ti. Tú decides siempre; yo recomiendo y "
    "ejecuto solo cuando pulsas el botón.\n"
    "\n"
    "⚽ TU EQUIPO\n"
    "/alineacion — el once óptimo de la jornada, con capitán inteligente (mejores "
    "puntos + bonus si es diferencial).\n"
    "/equipo — tu plantilla y tu saldo de un vistazo.\n"
    "/resumen — cómo te fue en la última jornada (y qué aprendió el modelo).\n"
    "\n"
    "💰 MERCADO Y ECONOMÍA\n"
    "/mercado — chollos, tendencias de precio y cláusulas, con botones para fichar.\n"
    "/optimizar — la mejor jugada (vende X, ficha Z) mirando ya las próximas jornadas.\n"
    "/vender — a quién soltar YA (precio en techo/bajada o calendario duro).\n"
    "/diferenciales — buenos jugadores que casi nadie tiene en tu liga (tu ventaja).\n"
    "\n"
    "📅 PLANIFICACIÓN\n"
    "/calendario — lo fácil o difícil que tiene tu equipo las próximas 5 jornadas.\n"
    "/quiniela — pronóstico 1X2 de la jornada.\n"
    "/objetivos — tus auto-pujas (sniping) programadas.\n"
    "\n"
    "⚙️ AJUSTES\n"
    "/auto — enciende/apaga automatizaciones (alineación y subir cláusula).\n"
    "/aprendizaje — cómo va el auto-calibrado de mis predicciones.\n"
    "/token <valor> — renueva el token de Biwenger si caduca, sin tocar el servidor.\n"
    "/help — esta guía."
)

# Lista para registrar el menú de comandos en Telegram (setMyCommands).
COMMAND_MENU = [
    ("alineacion", "Once óptimo de la jornada"),
    ("mercado", "Chollos, tendencias y cláusulas"),
    ("optimizar", "La mejor jugada de mercado"),
    ("calendario", "Dificultad de las próximas jornadas"),
    ("diferenciales", "Jugadores que casi nadie tiene"),
    ("vender", "A quién vender ya (timing)"),
    ("quiniela", "Pronóstico 1X2 de la jornada"),
    ("resumen", "Resumen de la última jornada"),
    ("objetivos", "Tus auto-pujas programadas"),
    ("auto", "Activa/desactiva automatizaciones"),
    ("equipo", "Tu plantilla y saldo"),
    ("help", "Ayuda"),
]


def _cmd_help(_: BiwengerClient) -> tuple[str, list[Button] | None]:
    return HELP_TEXT, None


def _cmd_lineup(client: BiwengerClient) -> tuple[str, list[Button] | None]:
    return services.lineup_message(client), None


def _cmd_market(client: BiwengerClient) -> tuple[str, list[Button] | None]:
    report = services.economy_report(client)
    return report.as_text(), report.action_buttons()


def _cmd_squad(client: BiwengerClient) -> tuple[str, list[Button] | None]:
    return services.squad_message(client), None


def _cmd_quiniela(client: BiwengerClient) -> tuple[str, list[Button] | None]:
    return services.quiniela_message(client), None


def _cmd_summary(client: BiwengerClient) -> tuple[str, list[Button] | None]:
    return services.postmatch_message(client), None


def _cmd_optimize(client: BiwengerClient) -> tuple[str, list[Button] | None]:
    return services.optimize_squad(client), None


def _cmd_calendar(client: BiwengerClient) -> tuple[str, list[Button] | None]:
    return services.fixture_calendar_message(client), None


def _cmd_differentials(client: BiwengerClient) -> tuple[str, list[Button] | None]:
    return services.differentials_message(client), None


def _cmd_sell(client: BiwengerClient) -> tuple[str, list[Button] | None]:
    return services.sell_timing_message(client)


def _cmd_learning(_: BiwengerClient) -> tuple[str, list[Button] | None]:
    from learning.tuner import learning_status
    return learning_status(), None


def _cmd_auto(_: BiwengerClient) -> tuple[str, list[Button] | None]:
    from data.db import get_setting

    lineup_on = get_setting("auto_lineup") == "on"
    clause_on = get_setting("auto_clause") == "on"
    text = (
        "⚙️ Automatizaciones (tú controlas qué se activa):\n"
        f"  ⚽ Alineación automática: {'✅ ON' if lineup_on else '⚪ OFF'} "
        "(pone tu once óptimo solo cada jornada, riesgo cero)\n"
        f"  🛡️ Subir cláusula automática: {'✅ ON' if clause_on else '⚪ OFF'} "
        "(blinda tus cracks vulnerables; gasta saldo)"
    )
    buttons = [
        (("⚽ Desactivar alineación auto" if lineup_on else "⚽ Activar alineación auto"),
         f"toggle:auto_lineup:{'off' if lineup_on else 'on'}"),
        (("🛡️ Desactivar cláusula auto" if clause_on else "🛡️ Activar cláusula auto"),
         f"toggle:auto_clause:{'off' if clause_on else 'on'}"),
    ]
    return text, buttons


def _cmd_targets(client: BiwengerClient) -> tuple[str, list[Button] | None]:
    from data.db import list_snipe_targets

    targets = [t for t in list_snipe_targets() if t.status in ("pending", "done")]
    if not targets:
        return "🎯 No tienes objetivos de auto-puja ahora mismo.", None
    lines = ["🎯 Tus objetivos de auto-puja:"]
    estado = {"pending": "⏳ esperando el cierre", "done": "✅ pujado"}
    buttons: list[Button] = []
    for t in targets:
        lines.append(f"  {t.player_name} — hasta {int(t.max_bid):,}€ ({estado.get(t.status, t.status)})".replace(",", "."))
        if t.status == "pending":
            buttons.append((f"❌ Quitar {t.player_name}", f"unsnipe:{t.player_id}"))
    return "\n".join(lines), (buttons or None)


HANDLERS = {
    "/start": _cmd_help,
    "/help": _cmd_help,
    "/alineacion": _cmd_lineup,
    "/mercado": _cmd_market,
    "/optimizar": _cmd_optimize,
    "/calendario": _cmd_calendar,
    "/diferenciales": _cmd_differentials,
    "/vender": _cmd_sell,
    "/quiniela": _cmd_quiniela,
    "/resumen": _cmd_summary,
    "/objetivos": _cmd_targets,
    "/aprendizaje": _cmd_learning,
    "/auto": _cmd_auto,
    "/equipo": _cmd_squad,
}


def handle_command(text: str, client: BiwengerClient) -> tuple[str, list[Button] | None]:
    """Despacha un mensaje de texto a su comando. Devuelve (texto, botones)."""
    parts = text.strip().split()
    # Primer token, en minúsculas, quitando un posible @nombrebot.
    cmd = parts[0].lower().split("@")[0]

    # Renovar el token en caliente (sin tocar el servidor): /token <valor>.
    if cmd == "/token":
        if len(parts) < 2:
            return "Uso: /token <tu_token>  (sácalo del navegador: localStorage.getItem('satellizer_token'))", None
        try:
            client.update_token(parts[1])
            return "✅ Token actualizado y guardado. Borra este mensaje del chat por seguridad. 🔒", None
        except Exception as exc:  # noqa: BLE001
            return f"No pude actualizar el token: {exc}", None

    handler = HANDLERS.get(cmd)
    if handler is None:
        return "No conozco ese comando. Escribe /help para ver lo que puedo hacer.", None
    try:
        return handler(client)
    except Exception as exc:  # noqa: BLE001 - un fallo no debe tumbar el bot
        logger.exception("Error ejecutando %s", cmd)
        return f"Ups, algo ha fallado ejecutando {cmd}: {exc}", None
