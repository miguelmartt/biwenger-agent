"""Lo que el bot enseña, en HTML de Telegram: el parte diario, la alineación, el equipo,
la clasificación y la jornada.

Cada vista pide los datos al cliente y los formatea; los nombres que vienen de Biwenger se
escapan siempre (un "&" en el nombre de un equipo rompería el mensaje).
"""
from __future__ import annotations

import html
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.telegram_bot import Html
from config.settings import settings
from core import services
from core.client import BiwengerClient
from core.models import Player, Position
from lineup.optimizer import best_lineup
from lineup.predictor import predict

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Madrid")
WEEKDAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
LINES = [(Position.GOALKEEPER, "POR"), (Position.DEFENDER, "DEF"), (Position.MIDFIELDER, "MED"),
         (Position.FORWARD, "DEL")]
STATUS_ICON = {"injured": "🤕", "sanctioned": "🟥", "suspended": "🟥", "doubt": "⚠️"}


# ---------------------------------------------------------------------- #
# Formato
# ---------------------------------------------------------------------- #
def esc(value) -> str:
    return html.escape(str(value), quote=False)


def _num(value: float, decimals: int) -> str:
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def money(n: int) -> str:
    """Importe corto: 1,69 M€ · 17,8 M€ · 850 k€."""
    n = int(n)
    if abs(n) >= 10_000_000:
        return f"{_num(n / 1e6, 1)} M€"
    if abs(n) >= 1_000_000:
        return f"{_num(n / 1e6, 2)} M€"
    return f"{_num(n / 1e3, 0)} k€"


def change(n: int) -> str:
    """Variación con flecha: ▲ 0,30 M€ · ▼ 120 k€ · = sin cambios."""
    if not n:
        return "="
    return ("▲ " if n > 0 else "▼ ") + money(abs(n))


def pct(value: float) -> str:
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f"{sign}{_num(abs(value), 1)} %"


def points(value: float) -> str:
    return _num(value, 1)


def when(epoch: int, now: float | None = None) -> str:
    """«hoy 21:00», «mañana 14:00» o «sáb 2 nov 14:00»."""
    dt = datetime.fromtimestamp(epoch, TZ)
    today = datetime.fromtimestamp(now or time.time(), TZ).date()
    days = (dt.date() - today).days
    hour = dt.strftime("%H:%M")
    if days == 0:
        return f"hoy {hour}"
    if days == 1:
        return f"mañana {hour}"
    return f"{WEEKDAYS[dt.weekday()]} {dt.day} {MONTHS[dt.month - 1]} {hour}"


def countdown(seconds: float) -> str:
    """«2 d 4 h», «3 h 20 min», «45 min»."""
    minutes = max(int(seconds), 0) // 60
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {mins} min"
    return f"{mins} min"


def _line_of(player: Player) -> str:
    return next((label for pos, label in LINES if pos == player.position), "—")


def _status(player: Player) -> str:
    icon = STATUS_ICON.get(player.status or "")
    return f" {icon}" if icon else ""


def _first_fixture(fixtures: dict, player: Player):
    upcoming = fixtures.get(player.team_id) or []
    return upcoming[0] if upcoming else None


def _rival(fixture) -> str:
    if fixture is None:
        return "sin partido"
    return f"{esc(fixture.opponent)} ({'casa' if fixture.is_home else 'fuera'})"


# ---------------------------------------------------------------------- #
# Jornada
# ---------------------------------------------------------------------- #
def _round_games(client: BiwengerClient) -> tuple[dict | None, list[dict]]:
    active = client.get_active_round()
    games = sorted((active or {}).get("games") or [], key=lambda g: g.get("date") or 0)
    return active, games


def round_status_line(active: dict | None, games: list[dict], now: float | None = None) -> str:
    """«📅 Jornada 11 empieza hoy 21:00 (en 2 h)» o «🔴 Jornada 11 en juego»."""
    if not active:
        return "📅 No hay jornada a la vista."
    now = now or time.time()
    name = esc(active.get("name") or "Jornada")
    first = min((g["date"] for g in games if g.get("date")), default=0)
    if first and first > now:
        return f"📅 <b>{name}</b> empieza {when(first, now)} (en {countdown(first - now)})"
    pending = [g for g in games if g.get("status") != "finished"]
    if pending:
        return f"🔴 <b>{name}</b> en juego · quedan {len(pending)} partidos"
    return f"✅ <b>{name}</b> terminada"


# ---------------------------------------------------------------------- #
# Vistas
# ---------------------------------------------------------------------- #
def lineup_view(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> Html:
    catalog = catalog if catalog is not None else client.get_all_players()
    players = services.enriched_squad(client, catalog)
    result = best_lineup(players)
    ep = {p.id: predict(p) for p in players}
    counts, n_managers = services._safe_ownership_counts(client)
    captain = services.best_captain(result.starters, ep, counts, n_managers) or result.captain
    fixtures = client.get_team_fixtures(weeks=1)
    active, games = _round_games(client)

    title = esc((active or {}).get("name") or "la jornada")
    out = [f"⚽ <b>Alineación para {title}</b>",
           f"{esc(result.formation)} · <b>{points(result.total_expected_points)} pts</b> esperados"]
    if captain:
        rare = counts and n_managers and counts.get(int(captain.id), 0) <= 1
        out.append(f"⭐ Capitán: <b>{esc(captain.name)}</b>" + (" · casi nadie más lo tiene" if rare else ""))
    starters = {p.id for p in result.starters}
    for pos, label in LINES:
        line = sorted((p for p in result.starters if p.position == pos), key=lambda p: -ep.get(p.id, 0))
        if not line:
            continue
        out += ["", f"<b>{label}</b>"]
        for p in line:
            fx = _first_fixture(fixtures, p)
            light = services._difficulty_emoji(fx.difficulty if fx else None)
            star = " ⭐" if captain and p.id == captain.id else ""
            out.append(f"{light} {esc(p.name)}{star}{_status(p)} · {points(ep.get(p.id, 0))} · {_rival(fx)}")
    bench = [p for p in players if p.id not in starters]
    if bench:
        out += ["", "🪑 Banquillo: " + ", ".join(f"{esc(p.name)}{_status(p)}" for p in bench)]
    first = min((g["date"] for g in games if g.get("date") and g.get("status") != "finished"), default=0)
    if first and first > time.time():
        out.append(f"⏰ Primer partido {when(first)}")
    out += ["", "<i>🟢 rival fácil · 🟡 normal · 🔴 difícil · puntos esperados</i>"]
    return Html("\n".join(out))


def squad_view(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> Html:
    catalog = catalog if catalog is not None else client.get_all_players()
    team = client.get_my_team()
    players = services._enrich(client, team.player_ids, catalog, owned=True)
    value = sum(p.price for p in players)
    delta = sum(p.price_increment for p in players)
    out = [f"👥 <b>{esc(team.name)}</b>",
           f"💰 Saldo <b>{money(team.balance)}</b> · valor {money(value)} ({change(delta)} hoy)"]
    for pos, label in LINES:
        line = sorted((p for p in players if p.position == pos), key=lambda p: -p.price)
        if not line:
            continue
        out += ["", f"<b>{label}</b>"]
        for p in line:
            trend = f" · {pct(p.price_trend_pct)}" if p.price_increment else ""
            out.append(f"{esc(p.name)}{_status(p)} · {money(p.price)}{trend}")
    return Html("\n".join(out))


def standings_view(client: BiwengerClient) -> Html:
    rows = client.get_standings()
    if not rows:
        return Html("🏆 No he podido leer la clasificación.")
    width = 14
    table = []
    for r in rows:
        mark = "▶" if r["me"] else " "
        name = (r["name"][:width - 1] + "…") if len(r["name"]) > width else r["name"]
        table.append(f"{mark}{r['position']:>2} {name.ljust(width)} {r['points']:>5}")
    out = ["🏆 <b>Clasificación</b>", "<pre>" + esc("\n".join(table)) + "</pre>"]
    me = next((r for r in rows if r["me"]), None)
    if me:
        out.append(position_line(rows, me))
    return Html("\n".join(out))


def position_line(rows: list[dict], me: dict) -> str:
    """«Vas 4.º de 13 · a 34 pts del líder · 6 por encima del 5.º»."""
    leader = rows[0]
    parts = [f"Vas <b>{me['position']}.º</b> de {len(rows)} con {me['points']} pts"]
    if me is leader or me["position"] == 1:
        if len(rows) > 1:
            parts.append(f"{me['points'] - rows[1]['points']} pts de ventaja sobre el 2.º")
    else:
        parts.append(f"a {leader['points'] - me['points']} del líder")
    below = next((r for r in rows if r["position"] == me["position"] + 1), None)
    if below and me["position"] != 1:
        parts.append(f"{me['points'] - below['points']} por encima del {below['position']}.º")
    return " · ".join(parts)


def round_view(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> Html:
    active, games = _round_games(client)
    if not active:
        return Html("📅 No hay jornada a la vista.")
    catalog = catalog if catalog is not None else client.get_all_players()
    mine = services.enriched_squad(client, catalog)
    by_team: dict[int, list[str]] = {}
    for p in mine:
        by_team.setdefault(p.team_id, []).append(p.name)

    out = [round_status_line(active, games), ""]
    for g in games:
        home, away = g.get("home") or {}, g.get("away") or {}
        if g.get("status") == "finished" and home.get("score") is not None:
            score = f"{home.get('score')}-{away.get('score')}"
            head = f"✅ {esc(home.get('name', '?'))} {score} {esc(away.get('name', '?'))}"
        else:
            head = f"{when(g['date']) if g.get('date') else '—'} · {esc(home.get('name', '?'))} – {esc(away.get('name', '?'))}"
        out.append(head)
        yours = by_team.get(home.get("id"), []) + by_team.get(away.get("id"), [])
        if yours:
            out.append("   👤 " + ", ".join(esc(n) for n in yours))
    playing = {(g.get(side) or {}).get("id") for g in games for side in ("home", "away")}
    without = [p.name for p in mine if p.team_id not in playing]
    if without:
        out += ["", "🚫 Sin partido: " + ", ".join(esc(n) for n in without)]
    return Html("\n".join(out))


def daily_report(client: BiwengerClient) -> Html:
    """El parte de cada mañana: todo lo importante en un mensaje, sin tener que pulsar nada."""
    catalog = client.get_all_players()
    now = time.time()
    hello = f"☀️ <b>Buenos días{', ' + esc(settings.owner_name) if settings.owner_name else ''}</b>"
    out = [f"{hello} · {_today(now)}"]

    try:
        rows = client.get_standings()
        me = next((r for r in rows if r["me"]), None)
        if me:
            out += ["", "🏆 " + position_line(rows, me)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Parte: sin clasificación (%s)", exc)

    try:
        active, games = _round_games(client)
        out += ["", round_status_line(active, games, now)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Parte: sin jornada (%s)", exc)

    team = client.get_my_team()
    players = services._enrich(client, team.player_ids, catalog, owned=True)
    value = sum(p.price for p in players)
    delta = sum(p.price_increment for p in players)
    out += ["", f"💰 Saldo <b>{money(team.balance)}</b> · plantilla {money(value)} ({change(delta)} ayer)"]

    alerts = []
    for p in players:
        if p.is_injured_or_suspended:
            label = {"injured": "lesionado", "doubt": "en duda"}.get(p.status, "sancionado")
            alerts.append(f"{STATUS_ICON.get(p.status, '⚠️')} {esc(p.name)} {label}")
    try:
        offers = client.get_offers()
        if offers:
            alerts.append(f"📨 {len(offers)} oferta{'s' if len(offers) != 1 else ''} por tus jugadores")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Parte: sin ofertas (%s)", exc)
    try:
        urgent = [r for r in services.sell_recommendations(client, catalog)
                  if r["urgency"] >= services.SELL_ALERT_MIN_URGENCY]
        for rec in urgent[:3]:
            alerts.append(f"📉 {esc(rec['player'].name)}: {esc(', '.join(rec['reasons']))} · plantéate venderlo")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Parte: sin timing de ventas (%s)", exc)
    if alerts:
        out += ["", "<b>Avisos</b>"] + [f"• {a}" for a in alerts]

    try:
        result = best_lineup(players)
        captain = result.captain
        if captain:
            out += ["", f"⭐ Capitán sugerido: <b>{esc(captain.name)}</b> · "
                        f"once de {points(result.total_expected_points)} pts esperados"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Parte: sin alineación (%s)", exc)
    return Html("\n".join(out))


def _today(now: float) -> str:
    dt = datetime.fromtimestamp(now, TZ)
    return f"{WEEKDAYS[dt.weekday()]} {dt.day} {MONTHS[dt.month - 1]}"
