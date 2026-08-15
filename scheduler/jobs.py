"""Orquestación de todos los jobs periódicos en un único proceso APScheduler.

Un solo proceso (en vez de varios scripts sueltos con cron) para que todo
comparta el mismo cliente autenticado, el mismo log y el mismo estado de
DRY_RUN — y para que el sniper pueda dispararse con precisión de segundos
sin depender de la granularidad de minutos de un cron tradicional.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from bidding.sniper import process_snipes
from bot.commands import DAILY_GREETING, MENU_BUTTONS
from bot.telegram_bot import send_message
from core import services
from core.client import BiwengerClient, BiwengerTokenExpired
from data.db import detect_status_changes, init_db, save_player_snapshots
from live.tracker import live_updates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

client = BiwengerClient()

# Etiquetas legibles para los cambios de estado de un jugador.
STATUS_LABEL = {
    "injured": "🤕 lesionado",
    "sanctioned": "🟥 sancionado",
    "suspended": "🟥 sancionado",
    "doubt": "⚠️ en duda",
}


def job_sync_state() -> None:
    """Sincroniza: guarda snapshot de precios/puntos y avisa de lesiones nuevas.

    Corre cada 30 min. Las alertas de lesión solo saltan cuando un jugador
    CAMBIA de estado (no se repiten cada media hora), y se agrupan en un único
    mensaje si cambian varios a la vez.
    """
    catalog = client.get_all_players()
    team = client.get_my_team()
    mine = services._enrich(client, team.player_ids, catalog, owned=True)
    market = services.free_market_players(client, catalog)

    save_player_snapshots(mine + market)

    # Alertas de lesión/sanción SOLO de tu plantilla y SOLO al cambiar de estado.
    changes = detect_status_changes(mine)
    if changes:
        lines = ["🚑 Cambios en tu plantilla:"]
        for player, _old, new in changes:
            lines.append(f"  {STATUS_LABEL.get(new, new)} — {player.name} (revisa tu alineación)")
        send_message("\n".join(lines))

    # Alerta proactiva: chollos excepcionales NUEVOS en el mercado libre (con dedup).
    text, buttons = services.bargain_alerts(client, catalog)
    if text:
        send_message(text, buttons)

    # Auto-cláusula (si está activada): blinda cracks vulnerables.
    for message in services.auto_raise_clauses(client, catalog, team):
        send_message(message)

    # Espía de rivales: detecta y ACUMULA los movimientos (no los envía). Se mandan
    # juntos en el resumen diario de las 15:00, para no gotear un mensaje por cada uno.
    try:
        services.collect_rival_moves(client, catalog)
    except Exception as exc:  # noqa: BLE001 - no dejes que tumbe el snapshot
        logger.warning("job_sync_state: espía de rivales no concluyente (%s)", exc)

    # Timing de ventas: aviso proactivo (deduplicado) solo de caídas urgentes.
    try:
        alert = services.sell_timing_alerts(client, catalog)
        if alert:
            send_message(alert)
    except Exception as exc:  # noqa: BLE001
        logger.warning("job_sync_state: timing de ventas no concluyente (%s)", exc)

    logger.info("job_sync_state: snapshot guardado, %s cambios de estado", len(changes))


def job_suggest_lineup() -> None:
    send_message(services.lineup_message(client))


def job_economy_report() -> None:
    report = services.economy_report(client)
    send_message(report.as_text(), report.action_buttons())


def job_quiniela() -> None:
    send_message(services.quiniela_message(client))


def job_daily_menu() -> None:
    """Saludo diario con botones: el usuario elige qué ver (en vez de spamearle informes)."""
    send_message(DAILY_GREETING, MENU_BUTTONS)


def job_save_predictions() -> None:
    """Guarda las predicciones de la jornada (para que el bot aprenda luego)."""
    n = services.save_predictions_for_round(client)
    logger.info("job_save_predictions: guardadas %s predicciones", n)


def job_postmatch() -> None:
    """Resumen de la jornada + aprendizaje, los lunes por la mañana."""
    send_message(services.postmatch_message(client))
    learned = services.learn_from_round(client)
    if learned:
        send_message(learned)


def job_daily_analysis() -> None:
    """Recalcula en segundo plano la caché del resumen diario (infracciones del
    reglamento + zona de castigo). Es la parte pesada; corre cada pocas horas para
    que el botón /resumendiario responda al instante leyendo la caché."""
    try:
        services.refresh_daily_analysis(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("job_daily_analysis: no concluyente (%s)", exc)


def job_rival_digest() -> None:
    """Resumen ÚNICO diario (15:00): movimientos de rivales del día + infracciones
    del reglamento + zona de castigo, en un solo mensaje. Vacía lo acumulado."""
    try:
        services.refresh_daily_analysis(client)  # datos frescos justo antes de enviar
        digest = services.daily_digest(consume=True)
        if digest:
            send_message(digest)
    except Exception as exc:  # noqa: BLE001
        logger.warning("job_rival_digest: resumen diario no concluyente (%s)", exc)


def job_token_health() -> None:
    """Comprueba a diario que el token sigue vivo; si no, avisa con antelación
    para que el usuario lo renueve con /token (sin tocar el servidor)."""
    try:
        client.get_balance()
    except BiwengerTokenExpired:
        send_message(
            "⚠️ Tu token de Biwenger ha caducado. Renuévalo fácil: entra a biwenger.as.com, "
            "consola (F12) → localStorage.getItem('satellizer_token'), copia el texto y mándame "
            "aquí: /token <el_token>. (Sin tocar el servidor.)"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("job_token_health: no concluyente (%s)", exc)


def job_live_tracking() -> None:
    """Sigue a tus jugadores en directo durante los partidos. Corre cada 5 min pero
    solo hace algo si hay algún partido en curso (si no, sale sin gastar nada)."""
    if not client.has_live_games(int(time.time())):
        return
    team = client.get_my_team()
    for message in live_updates(client, team.player_ids):
        send_message(message)


def job_sniper() -> None:
    """Auto-puja: puja por tus objetivos justo antes del cierre del mercado."""
    for message in process_snipes(client):
        send_message(message)


def job_auto_lineup() -> None:
    """Si la auto-alineación está activada, pone el once óptimo unas horas antes
    del primer partido de la jornada. Corre cada hora pero solo actúa en la ventana."""
    from data.db import get_setting

    if get_setting("auto_lineup") != "on":
        return
    fixtures = client.get_round_fixtures()
    dates = [f.date for f in fixtures if f.date]
    if not dates:
        return
    seconds_to_first = min(dates) - int(time.time())
    if 0 < seconds_to_first <= 4 * 3600:  # dentro de las 4h antes del primer partido
        message = services.auto_set_lineup(client)
        if message:
            send_message(message)


def _on_job_error(event) -> None:
    """Avisa por Telegram si un job falla; mensaje especial si el token caducó."""
    exc = event.exception
    if isinstance(exc, BiwengerTokenExpired):
        send_message(
            "⚠️ El token de Biwenger ha caducado. Renuévalo desde aquí mismo, sin tocar el "
            "servidor: saca 'satellizer_token' del navegador (F12 → consola) y mándame "
            "/token <el_token>. En cuanto lo hagas, el agente sigue solo."
        )
    else:
        logger.error("Job %s falló: %s", event.job_id, exc)


def build_scheduler() -> BlockingScheduler:
    init_db()
    scheduler = BlockingScheduler(timezone="Europe/Madrid")
    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)

    # Sincroniza precios/puntos y detecta lesiones (silencioso salvo cambios).
    scheduler.add_job(job_sync_state, CronTrigger(minute="*/30"), id="sync_state")
    # Un ÚNICO mensaje diario: saludo con botones (evita el spam de volcar informes).
    scheduler.add_job(job_daily_menu, CronTrigger(hour=9), id="daily_menu")
    # Seguimiento en vivo: cada 5 min, pero solo actúa si hay partido en curso.
    scheduler.add_job(job_live_tracking, CronTrigger(minute="*/5"), id="live_tracking")
    # Guardar predicciones de la jornada (viernes, antes de que se juegue).
    scheduler.add_job(job_save_predictions, CronTrigger(day_of_week="fri", hour=12), id="save_predictions")
    # Resumen de la jornada + aprendizaje: lunes por la mañana.
    scheduler.add_job(job_postmatch, CronTrigger(day_of_week="mon", hour=10), id="postmatch")
    # Auto-puja (sniping): cada minuto, puja por los objetivos cuyo cierre es inminente.
    scheduler.add_job(job_sniper, CronTrigger(minute="*"), id="sniper")
    # Salud del token: chequeo diario, avisa con antelación si caducó.
    scheduler.add_job(job_token_health, CronTrigger(hour=8), id="token_health")
    # Espía de rivales: UN único resumen diario a las 15:00 con todos los
    # movimientos del día juntos (en vez de un mensaje por cada fichaje/venta).
    scheduler.add_job(job_rival_digest, CronTrigger(hour=15), id="rival_digest")
    # Análisis pesado (infracciones + zona de castigo) en segundo plano cada 3h,
    # para que /resumendiario responda al instante desde la caché.
    scheduler.add_job(job_daily_analysis, CronTrigger(hour="*/3"), id="daily_analysis")
    # Y una ejecución inicial poco después de arrancar, para poblar la caché ya.
    scheduler.add_job(job_daily_analysis, "date",
                      run_date=datetime.now() + timedelta(seconds=20), id="daily_analysis_boot")
    # Auto-alineación (si está activada): revisa cada hora y pone el once óptimo
    # dentro de la ventana de 4h antes del primer partido de la jornada.
    scheduler.add_job(job_auto_lineup, CronTrigger(minute=0), id="auto_lineup")

    # Nota: alineación, mercado y quiniela ya NO se envían solos cada día; se piden
    # con los botones del menú o los comandos, para que no te lleguen mensajes de más.

    return scheduler


if __name__ == "__main__":
    build_scheduler().start()
