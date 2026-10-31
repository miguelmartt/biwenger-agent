"""Comandos interactivos del bot de Telegram.

Cada comando devuelve (texto, botones). La lógica real vive en core.services y el
formato de las vistas nuevas en bot.views (compartidos con los avisos automáticos).

Menos es más: el menú tiene 6 botones en dos columnas y lo secundario va detrás de
«➕ Más»; el menú «/» de Telegram enseña solo lo esencial (el resto se puede escribir).
"""
from __future__ import annotations

import logging

from bot import views
from bot.telegram_bot import Html, grid
from config.settings import settings
from core import services
from core.client import BiwengerClient

logger = logging.getLogger(__name__)

Button = tuple[str, str]
Rows = list[list[Button]]

MENU_MAIN: list[Button] = [
    ("📋 Parte", "cmd:parte"),
    ("⚽ Alineación", "cmd:alineacion"),
    ("💰 Mercado", "cmd:mercado"),
    ("🏆 Liga", "cmd:liga"),
    ("📅 Jornada", "cmd:jornada"),
    ("➕ Más", "cmd:mas"),
]
MENU_MORE: list[Button] = [
    ("👥 Mi equipo", "cmd:equipo"),
    ("⏱️ Vender", "cmd:vender"),
    ("🔁 Optimizar", "cmd:optimizar"),
    ("🗓️ Calendario", "cmd:calendario"),
    ("🕵️ Diferenciales", "cmd:diferenciales"),
    ("🎯 Quiniela", "cmd:quiniela"),
    ("📊 Última jornada", "cmd:resumen"),
    ("👀 Rivales", "cmd:resumendiario"),
    ("⚙️ Ajustes", "cmd:auto"),
    ("⬅️ Volver", "cmd:menu"),
]
# Botones del parte diario: lo justo para seguir sin agobiar.
REPORT_BUTTONS: Rows = [[("⚽ Alineación", "cmd:alineacion"), ("☰ Menú", "cmd:menu")]]

_WHO = f", {settings.owner_name}" if settings.owner_name else ""
MENU_TEXT = f"⚽ <b>Biwenger</b>{_WHO and ' · ' + views.esc(settings.owner_name)}\n¿Qué quieres ver?"
MORE_TEXT = "➕ <b>Más opciones</b>"

HELP_TEXT = (
    "ℹ️ <b>Qué puedo hacer</b>\n"
    "Cada mañana te mando el parte con lo importante. Yo recomiendo; tú decides, y nada se "
    "ejecuta sin que pulses ✅.\n"
    "\n"
    "<b>Lo de cada día</b>\n"
    "/parte · el parte, ahora\n"
    "/alineacion · el once óptimo con capitán\n"
    "/mercado · chollos y cláusulas, con botón para fichar\n"
    "/liga · la clasificación\n"
    "/jornada · partidos y quién de los tuyos juega\n"
    "/menu · todo con botones\n"
    "\n"
    "<b>Cuando te haga falta</b>\n"
    "/equipo · /vender · /optimizar · /calendario · /diferenciales · /quiniela · "
    "/resumen · /resumendiario · /objetivos · /auto · /aprendizaje\n"
    "/token &lt;valor&gt; · renueva el token de Biwenger si caduca"
)

# El menú «/» de Telegram: solo lo esencial.
COMMAND_MENU = [
    ("parte", "El parte del día"),
    ("alineacion", "Once óptimo y capitán"),
    ("mercado", "Chollos y fichajes"),
    ("liga", "Clasificación"),
    ("jornada", "Partidos de la jornada"),
    ("menu", "Todo con botones"),
    ("help", "Qué puedo hacer"),
]

# Vistas que se pueden «Actualizar» desde su botón (se recalculan al pulsar).
REFRESHABLE = {"parte", "alineacion", "mercado", "liga", "jornada", "equipo", "vender", "optimizar",
               "calendario", "diferenciales", "quiniela", "resumen", "resumendiario", "objetivos",
               "aprendizaje"}


def nav(command: str) -> list[Button]:
    """La fila de navegación de una vista: actualizar y volver al menú."""
    row = [("⬅️ Menú", "cmd:menu")]
    if command in REFRESHABLE:
        row.insert(0, ("🔄 Actualizar", f"cmd:{command}"))
    return row


def _cmd_menu(_: BiwengerClient):
    return Html(MENU_TEXT), grid(MENU_MAIN)


def _cmd_more(_: BiwengerClient):
    return Html(MORE_TEXT), grid(MENU_MORE)


def _cmd_help(_: BiwengerClient):
    return Html(HELP_TEXT), None


def _cmd_report(client: BiwengerClient):
    return views.daily_report(client), None


def _cmd_lineup(client: BiwengerClient):
    return views.lineup_view(client), None


def _cmd_squad(client: BiwengerClient):
    return views.squad_view(client), None


def _cmd_standings(client: BiwengerClient):
    return views.standings_view(client), None


def _cmd_round(client: BiwengerClient):
    return views.round_view(client), None


def _cmd_market(client: BiwengerClient):
    report = services.economy_report(client)
    return report.as_text(), report.action_buttons()


def _cmd_quiniela(client: BiwengerClient):
    return services.quiniela_message(client), None


def _cmd_summary(client: BiwengerClient):
    return services.postmatch_message(client), None


def _cmd_optimize(client: BiwengerClient):
    return services.optimize_squad(client), None


def _cmd_calendar(client: BiwengerClient):
    return services.fixture_calendar_message(client), None


def _cmd_differentials(client: BiwengerClient):
    return services.differentials_message(client), None


def _cmd_sell(client: BiwengerClient):
    return services.sell_timing_message(client)


def _cmd_daily_digest(client: BiwengerClient):
    text = services.daily_digest(client, consume=False)
    return (text or "📋 Sin novedades hoy: ni movimientos de rivales ni infracciones detectadas."), None


def _cmd_learning(_: BiwengerClient):
    from learning.tuner import learning_status
    return learning_status(), None


def _cmd_auto(_: BiwengerClient):
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


def _cmd_targets(client: BiwengerClient):
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
    "/start": _cmd_menu,
    "/menu": _cmd_menu,
    "/mas": _cmd_more,
    "/help": _cmd_help,
    "/ayuda": _cmd_help,
    "/parte": _cmd_report,
    "/alineacion": _cmd_lineup,
    "/equipo": _cmd_squad,
    "/liga": _cmd_standings,
    "/clasificacion": _cmd_standings,
    "/jornada": _cmd_round,
    "/mercado": _cmd_market,
    "/optimizar": _cmd_optimize,
    "/calendario": _cmd_calendar,
    "/diferenciales": _cmd_differentials,
    "/vender": _cmd_sell,
    "/resumendiario": _cmd_daily_digest,
    "/quiniela": _cmd_quiniela,
    "/resumen": _cmd_summary,
    "/objetivos": _cmd_targets,
    "/aprendizaje": _cmd_learning,
    "/auto": _cmd_auto,
}


def with_nav(command: str, buttons) -> Rows | None:
    """Los botones propios de una vista (fichar, vender…) en cuadrícula, más la fila de navegación.
    El menú y «Más» ya son navegación y se devuelven tal cual."""
    if command in ("menu", "mas", "start"):
        return buttons
    rows: Rows = []
    if buttons:
        rows = buttons if isinstance(buttons[0], list) else grid(list(buttons), 1)
    return rows + [nav(command)]


def handle_command(text: str, client: BiwengerClient):
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
            return "✅ Token actualizado y guardado. 🔒", None
        except Exception as exc:  # noqa: BLE001
            logger.warning("No pude actualizar el token (%s)", type(exc).__name__)
            return f"No pude actualizar el token ({type(exc).__name__}).", None

    handler = HANDLERS.get(cmd)
    if handler is None:
        return "No conozco ese comando. Escribe /menu o /help.", None
    try:
        return handler(client)
    except Exception as exc:  # noqa: BLE001 - un fallo no debe tumbar el bot
        logger.exception("Error ejecutando %s", cmd)
        return f"Ups, algo ha fallado ejecutando {cmd}: {exc}", None
