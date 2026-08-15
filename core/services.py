"""Lógica de negocio de alto nivel, compartida por los avisos automáticos
(scheduler) y los comandos interactivos de Telegram. Así ambos usan
exactamente lo mismo sin duplicar código.
"""
from __future__ import annotations

import logging

from bidding.targets import free_market_only
from core.client import BiwengerClient
from core.models import Player
from economy.analyzer import ClauseRisk, ClauseTarget, build_report
from economy.trends import analyze_trend
from lineup.optimizer import best_lineup
from lineup.predictor import predict

# Cláusula por debajo de este múltiplo del precio de mercado = jugador vulnerable.
CLAUSE_RISK_RATIO = 1.4
# Solo protegemos a jugadores que rinden (no vale la pena blindar a un suplente).
MIN_POINTS_TO_PROTECT = 2.0
# Umbral de puntos esperados para considerar a un jugador un "diferencial" que
# merezca la pena (un jugador flojo que nadie tiene no es una ventaja).
DIFFERENTIAL_MIN_POINTS = 3.0
# Cuánto pesa ser diferencial al elegir capitán. Es un desempate estratégico
# (capitanear a alguien que casi nadie tiene te hace ganar puestos si peta), NO
# domina sobre los puntos esperados: no capitaneamos a un malo por diferencial.
DIFF_CAPTAIN_WEIGHT = 0.15
# Cuánto pesa la racha de calendario (varias jornadas) en el valor a futuro de un
# jugador para el optimizador. Más suave que la sensibilidad de una sola jornada.
RUN_SENSITIVITY = 0.4

logger = logging.getLogger(__name__)


def _fmt(n: int) -> str:
    """Formatea un número con puntos de millar (estilo español)."""
    return f"{int(n):,}".replace(",", ".")


def _enrich(client: BiwengerClient, player_ids, catalog: dict[int, Player], owned: bool = False) -> list[Player]:
    players: list[Player] = []
    for pid in player_ids:
        p = catalog.get(pid)
        if p is None:
            try:
                p = client.get_player(pid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("No pude leer el jugador %s: %s", pid, exc)
                continue
        p.is_owned_by_me = owned
        players.append(p)
    return players


def enriched_squad(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> list[Player]:
    catalog = catalog if catalog is not None else client.get_all_players()
    team = client.get_my_team()
    return _enrich(client, team.player_ids, catalog, owned=True)


def free_market_players(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> list[Player]:
    catalog = catalog if catalog is not None else client.get_all_players()
    # Regla: solo mercado libre, nunca jugadores que venden compañeros de liga.
    free_ids = [l.player_id for l in free_market_only(client.get_market())]
    return _enrich(client, free_ids, catalog)


def snapshot_players(client: BiwengerClient) -> list[Player]:
    """Jugadores a guardar en el histórico: tu plantilla + el mercado libre."""
    catalog = client.get_all_players()
    return enriched_squad(client, catalog) + free_market_players(client, catalog)


def _attach_price_history(client: BiwengerClient, players: list[Player]) -> None:
    """Rellena el histórico de precios de cada jugador (detalle individual).

    El catálogo (competition data) NO trae la curva de precios; hay que pedir el
    detalle de cada jugador. Se hace solo para el puñado de jugadores del informe
    de economía (tu plantilla + mercado libre), no para toda LaLiga.
    """
    for p in players:
        if p.price_history:
            continue
        try:
            detail = client.get_player(p.id)
            p.price_history = detail.price_history
            if not p.fitness:
                p.fitness = detail.fitness
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sin histórico de precios para %s: %s", p.id, exc)


# ---------------------------------------------------------------------- #
# Mensajes listos para enviar por Telegram
# ---------------------------------------------------------------------- #
def _safe_ownership_counts(client: BiwengerClient) -> tuple[dict[int, int], int]:
    """Cuenta cuántos managers tienen a cada jugador, tolerando fallos de red.
    Devuelve ({} , 0) si no se puede (para caer al capitán por puntos sin romper)."""
    try:
        managers = client.get_league_managers()
        counts = _league_ownership_counts(client)
        return counts, len(managers) + 1  # +1 = tú
    except Exception as exc:  # noqa: BLE001
        logger.warning("No pude leer propiedad de la liga (%s); capitán por puntos.", exc)
        return {}, 0


def best_captain(starters: list[Player], ep_map: dict[int, float],
                 own_counts: dict[int, int] | None = None, n_managers: int = 0):
    """Elige capitán combinando puntos esperados y un pequeño bonus por diferencial
    (si casi nadie más lo tiene, capitanearlo renta más para subir en la liga).

    Sin datos de propiedad, es simplemente el de más puntos esperados."""
    if not starters:
        return None

    def score(p: Player) -> float:
        ep = ep_map.get(p.id, 0.0)
        if own_counts and n_managers > 0:
            frac_owned = own_counts.get(int(p.id), 0) / n_managers
            return ep * (1.0 + DIFF_CAPTAIN_WEIGHT * (1.0 - frac_owned))
        return ep

    return max(starters, key=score)


def lineup_message(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> str:
    catalog = catalog if catalog is not None else client.get_all_players()
    players = enriched_squad(client, catalog)
    result = best_lineup(players)
    names = ", ".join(p.name for p in result.starters)

    # Capitán inteligente: puntos esperados + desempate por diferencial.
    ep_map = {p.id: predict(p) for p in result.starters}
    counts, n_mgr = _safe_ownership_counts(client)
    cap = best_captain(result.starters, ep_map, counts, n_mgr) or result.captain

    captain = ""
    if cap:
        extra = ""
        if counts and n_mgr > 0:
            owners = counts.get(int(cap.id), 0)
            if owners <= 1:
                extra = " (¡y casi nadie más lo tiene!)"
        captain = f"\n⭐ Capitán (dobla puntos): {cap.name}{extra}"
    return (
        f"⚽ Alineación sugerida ({result.formation}, "
        f"{result.total_expected_points} pts esperados):\n{names}{captain}"
    )


def clause_opportunities(
    client: BiwengerClient, catalog: dict[int, Player], budget: int, max_items: int = 5
) -> list[ClauseTarget]:
    """Cláusulas de jugadores de rivales que puedes pagar y rinden bien.

    Solo RECOMENDACIÓN: nunca paga nada. el usuario decide. (Permitido tocar
    cláusulas de compañeros; distinto de la regla de no fichar lo que venden.)
    """
    targets: list[ClauseTarget] = []
    for manager in client.get_league_managers():
        try:
            clauses = client.get_manager_clauses(manager["id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("No pude leer la plantilla de %s: %s", manager["name"], exc)
            continue
        for pid, info in clauses.items():
            clause = info.get("clause")
            if not clause or clause > budget:  # solo lo que puedes pagar ya
                continue
            player = catalog.get(pid)
            if player is None:
                continue
            ep = predict(player)
            if ep <= 0:
                continue
            targets.append(
                ClauseTarget(
                    player=player, clause=int(clause), expected_points=ep,
                    owner_name=manager["name"], owner_id=manager["id"],
                )
            )
    targets.sort(key=lambda t: t.value_ratio, reverse=True)
    return targets[:max_items]


def clause_risks(client: BiwengerClient, catalog: dict[int, Player], team) -> list[ClauseRisk]:
    """Tus jugadores buenos con la cláusula baja (riesgo de que te los roben).

    Recomienda subirla; NO la sube solo (requiere tu confirmación por botón).
    """
    risks: list[ClauseRisk] = []
    for pid, owner in team.owned.items():
        clause = (owner or {}).get("clause")
        player = catalog.get(int(pid))
        if not clause or player is None:
            continue
        ep = predict(player)
        if ep < MIN_POINTS_TO_PROTECT:
            continue
        if clause < CLAUSE_RISK_RATIO * player.price:
            risks.append(
                ClauseRisk(
                    player=player, clause=int(clause), expected_points=ep,
                    suggested_clause=int(player.price * 2),  # blindar a 2x el valor
                )
            )
    risks.sort(key=lambda r: r.expected_points, reverse=True)
    return risks[:5]


def economy_report(client: BiwengerClient, catalog: dict[int, Player] | None = None):
    """Construye el informe de economía completo (chollos, cláusulas, protección)."""
    catalog = catalog if catalog is not None else client.get_all_players()
    team = client.get_my_team()
    my_players = _enrich(client, team.player_ids, catalog, owned=True)
    market = free_market_players(client, catalog)
    # Adjunta la curva de precios para poder analizar tendencias.
    _attach_price_history(client, my_players)
    _attach_price_history(client, market)

    report = build_report(my_players, market, available_budget=team.balance)
    report.clause_targets = clause_opportunities(client, catalog, team.balance)
    report.clause_risks = clause_risks(client, catalog, team)
    return report


def economy_message(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> str:
    return economy_report(client, catalog).as_text()


# Umbrales para avisar proactivamente de un chollo EXCEPCIONAL (no de cualquiera).
ALERT_MIN_EXPECTED = 3.0   # que rinda de verdad, no un suplente barato
ALERT_MIN_VALUE = 5.0      # buena relación puntos/precio


def bargain_alerts(client: BiwengerClient, catalog: dict[int, Player] | None = None):
    """Detecta chollos EXCEPCIONALES nuevos del mercado libre. Devuelve (texto, botones)
    para avisar al instante, o (None, None) si no hay nada nuevo. Con dedup (no repite)."""
    from data.db import filter_new_bargains

    catalog = catalog if catalog is not None else client.get_all_players()
    market = free_market_players(client, catalog)

    strong = []
    for p in market:
        ep = predict(p)
        value = ep / (p.price / 1_000_000 or 0.0001)
        if ep >= ALERT_MIN_EXPECTED and value >= ALERT_MIN_VALUE:
            strong.append((p, ep, round(value, 1)))
    strong.sort(key=lambda t: t[2], reverse=True)

    new_ids = set(filter_new_bargains([p.id for p, _, _ in strong]))
    fresh = [(p, ep, v) for p, ep, v in strong if p.id in new_ids][:4]
    if not fresh:
        return None, None

    lines = ["💎 ¡Chollo(s) nuevo(s) en el mercado libre!"]
    buttons = []
    for p, ep, v in fresh:
        lines.append(f"  {p.name} — {_fmt(p.price)}€ · {ep} pts/j · valor {v}")
        buttons.append((f"✅ Fichar {p.name} ({p.price // 1000}k)", f"bid:{p.id}:{p.price}"))
    return "\n".join(lines), buttons


def optimize_squad(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> str:
    """La 'jugada completa': mejores swaps (vende X, ficha Z) para mejorar tu equipo
    dentro de tu presupuesto, ordenados por mejora de puntos esperados."""
    catalog = catalog if catalog is not None else client.get_all_players()
    team = client.get_my_team()
    my_players = _enrich(client, team.player_ids, catalog, owned=True)
    market = free_market_players(client, catalog)
    budget = team.balance
    fixtures_by_team = _safe_team_fixtures(client)

    def fwd(p: Player) -> float:
        """Valor a futuro: puntos esperados ponderados por la racha de calendario."""
        return predict(p) * _run_factor(fixtures_by_team.get(p.team_id or -1, []))

    swaps = []
    for mine in my_players:
        fv_mine = fwd(mine)
        for cand in market:
            if cand.position != mine.position:
                continue
            cost = cand.price - mine.price  # neto: vendes el tuyo y fichas el candidato
            if cost > budget:
                continue
            gain = round(fwd(cand) - fv_mine, 2)
            if gain > 0:
                # ¿La mejora viene sobre todo del calendario? (para explicarlo).
                by_calendar = _run_factor(fixtures_by_team.get(cand.team_id or -1, [])) > \
                    _run_factor(fixtures_by_team.get(mine.team_id or -1, [])) + 0.05
                swaps.append((mine, cand, gain, cost, by_calendar))
    swaps.sort(key=lambda s: s[2], reverse=True)

    if not swaps:
        return "🧮 No veo mejoras claras ahora mismo con tu presupuesto. Tu equipo está bien armado."

    lines = ["🧮 Mejores jugadas para mejorar tu equipo (mirando las próximas jornadas):"]
    seen_out = set()
    shown = 0
    for mine, cand, gain, cost, by_calendar in swaps:
        if mine.id in seen_out:  # no repetir el mismo jugador a vender
            continue
        seen_out.add(mine.id)
        coste = f"coste {_fmt(cost)}€" if cost > 0 else f"te sobran {_fmt(-cost)}€"
        motivo = " 📅 (mejor calendario)" if by_calendar else ""
        lines.append(f"  📤 Vende {mine.name} → 📥 Ficha {cand.name}: +{gain} pts/j ({coste}){motivo}")
        shown += 1
        if shown >= 3:
            break
    return "\n".join(lines)


def sell_recommendations(client: BiwengerClient, catalog: dict[int, Player] | None = None):
    """Timing de ventas: tus jugadores a los que conviene vender YA — porque su
    precio ha hecho techo o está bajando, y/o les viene una racha de calendario
    dura. Solo recomienda; tú decides con el botón de vender.

    Devuelve una lista de dicts con jugador, motivo y urgencia (para ordenar)."""
    catalog = catalog if catalog is not None else client.get_all_players()
    team = client.get_my_team()
    my_players = _enrich(client, team.player_ids, catalog, owned=True)
    _attach_price_history(client, my_players)
    fixtures_by_team = _safe_team_fixtures(client)

    recs = []
    for p in my_players:
        trend = analyze_trend(p.price_history)
        run_factor = _run_factor(fixtures_by_team.get(p.team_id or -1, []))
        reasons = []
        urgency = 0.0
        if trend.state == "bajando":
            reasons.append(f"precio bajando ({trend.change_3d_pct:+.1f}% 3d)")
            urgency += 2.0 + abs(trend.change_3d_pct) / 10.0
        elif trend.state == "techo":
            reasons.append("precio en techo (a punto de bajar)")
            urgency += 1.2
        if run_factor < 0.95:  # racha de calendario dura por delante
            reasons.append("racha de partidos difícil")
            urgency += (0.95 - run_factor) * 4.0
        if reasons:
            recs.append({"player": p, "reasons": reasons, "urgency": round(urgency, 2),
                         "trend": trend})
    recs.sort(key=lambda r: r["urgency"], reverse=True)
    return recs


# Urgencia mínima para un aviso proactivo de venta (evita molestar por caídas leves).
SELL_ALERT_MIN_URGENCY = 2.5


def sell_timing_alerts(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> str | None:
    """Aviso proactivo (deduplicado) SOLO de ventas urgentes: precio cayendo con
    fuerza. Un único aviso por jugador; se re-permite si vuelve a caer más tarde."""
    from data.db import filter_new_sell_alerts

    recs = [r for r in sell_recommendations(client, catalog)
            if r["urgency"] >= SELL_ALERT_MIN_URGENCY]
    at_risk_ids = [r["player"].id for r in recs]
    new_ids = set(filter_new_sell_alerts(at_risk_ids))
    fresh = [r for r in recs if r["player"].id in new_ids]
    if not fresh:
        return None
    lines = ["⏱️ Ojo, timing de venta — estos pierden valor pronto:"]
    for r in fresh:
        p = r["player"]
        lines.append(f"  {r['trend'].emoji} {p.name} — {_fmt(p.price)}€ · {', '.join(r['reasons'])}")
    lines.append("\nMíralos con /vender si quieres soltarlos con un botón.")
    return "\n".join(lines)


def sell_timing_message(client: BiwengerClient, catalog: dict[int, Player] | None = None):
    """Mensaje + botones de venta para el timing de ventas (comando /vender)."""
    recs = sell_recommendations(client, catalog)
    if not recs:
        return "💚 Nada urgente que vender: tus jugadores aguantan precio y calendario.", None
    lines = ["⏱️ Timing de ventas — plantéate soltar a estos antes de que bajen:"]
    buttons: list[tuple[str, str]] = []
    for r in recs[:5]:
        p = r["player"]
        lines.append(f"  {r['trend'].emoji} {p.name} — {_fmt(p.price)}€ · {', '.join(r['reasons'])}")
        k = p.price // 1000
        buttons.append((f"📤 Vender {p.name} ({k}k)", f"ask:s:{p.id}:{p.price}"))
    lines.append("\n(Vender es reversible hasta el cierre; quítalo del mercado si te arrepientes.)")
    return "\n".join(lines), (buttons or None)


def _difficulty_emoji(diff: float | None) -> str:
    """Semáforo de dificultad de un partido (menor = más fácil)."""
    if diff is None:
        return "⚪"
    if diff < 40:
        return "🟢"
    if diff <= 60:
        return "🟡"
    return "🔴"


def _run_rating(fixtures) -> float | None:
    """Dificultad media de una racha de partidos (ignora los sin dato)."""
    vals = [f.difficulty for f in fixtures if f.difficulty is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _run_factor(fixtures, sensitivity: float = RUN_SENSITIVITY) -> float:
    """Multiplicador de valor a futuro según la racha de calendario: >1 si vienen
    partidos fáciles, <1 si vienen difíciles. Neutro (1.0) si no hay datos."""
    rating = _run_rating(fixtures)
    if rating is None:
        return 1.0
    return round(1.0 + (50.0 - rating) / 100.0 * sensitivity, 3)


def _safe_team_fixtures(client: BiwengerClient, weeks: int = 5) -> dict:
    """Calendario multi-jornada tolerante a fallos (para no romper el optimizador
    si Biwenger aún no ha publicado próximas jornadas)."""
    try:
        return client.get_team_fixtures(weeks)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sin calendario multi-jornada (%s); optimizo solo a 1 jornada.", exc)
        return {}


def fixture_calendar_message(client: BiwengerClient, weeks: int = 5,
                             catalog: dict[int, Player] | None = None) -> str:
    """Planificador de calendario: para tu plantilla, la dificultad de las próximas
    `weeks` jornadas. Separa rachas fáciles (aprovecha / capitanea) de rachas duras
    (rota o valora vender antes de que baje). Todo con datos internos de Biwenger."""
    catalog = catalog if catalog is not None else client.get_all_players()
    team = client.get_my_team()
    my_players = _enrich(client, team.player_ids, catalog, owned=True)
    fixtures_by_team = client.get_team_fixtures(weeks)

    rows = []  # (player, run_fixtures, rating)
    for p in my_players:
        run = fixtures_by_team.get(p.team_id or -1, [])
        if not run:
            continue
        rows.append((p, run, _run_rating(run)))

    if not rows:
        return ("📅 Aún no hay calendario de próximas jornadas disponible "
                "(Biwenger lo publica cuando se acerca la jornada).")

    # Rachas con dato de dificultad, ordenadas de más fácil a más difícil.
    rated = [r for r in rows if r[2] is not None]
    rated.sort(key=lambda r: r[2])

    def _line(p, run, rating):
        emojis = "".join(_difficulty_emoji(f.difficulty) for f in run)
        media = f"dif. media {rating}" if rating is not None else "sin dato"
        return f"  {p.name} ({p.position.label}) {emojis}  ({media})"

    lines = [f"📅 Calendario — próximas {weeks} jornadas de tu equipo:", ""]

    faciles = [r for r in rated if r[2] is not None and r[2] < 45][:5]
    duras = [r for r in rated if r[2] is not None and r[2] > 58][-5:]

    if faciles:
        lines.append("✅ Racha fácil (aprovéchalos / candidatos a capitán):")
        for p, run, rating in faciles:
            lines.append(_line(p, run, rating))
        lines.append("")
    if duras:
        lines.append("⚠️ Racha dura (ojo: rota, o valora vender antes de que baje):")
        for p, run, rating in reversed(duras):
            lines.append(_line(p, run, rating))
        lines.append("")

    if not faciles and not duras:
        lines.append("Sin rachas extremas: tu calendario es equilibrado. Detalle:")
        for p, run, rating in rated[:8]:
            lines.append(_line(p, run, rating))
        lines.append("")

    lines.append("🟢 fácil · 🟡 media · 🔴 difícil · ⚪ sin dato")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------- #
# Espía de rivales y diferenciales
# ---------------------------------------------------------------------- #
def _league_ownership_counts(client: BiwengerClient) -> dict[int, int]:
    """Cuántos managers de la liga (tú incluido) tienen a cada jugador.

    Solo lectura de datos de la liga (endpoints confirmados). Sirve para saber
    qué jugadores son 'diferenciales' (pocos los tienen)."""
    counts: dict[int, int] = {}
    for m in client.get_league_managers():
        for pid in client.get_manager_clauses(m["id"]):
            counts[int(pid)] = counts.get(int(pid), 0) + 1
    for pid in client.get_my_team().player_ids:
        counts[int(pid)] = counts.get(int(pid), 0) + 1
    return counts


def differentials_message(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> str:
    """Tus diferenciales (jugadores buenos que casi nadie más tiene = tu ventaja)
    y gemas libres (buenos jugadores que NADIE tiene y están en el mercado libre).

    Es solo información para decidir: no ficha ni vende nada por su cuenta."""
    catalog = catalog if catalog is not None else client.get_all_players()
    counts = _league_ownership_counts(client)
    team = client.get_my_team()
    mine = set(team.player_ids)

    yours = []
    for pid in mine:
        p = catalog.get(int(pid))
        if p is None:
            continue
        ep = predict(p)
        if counts.get(int(pid), 0) <= 1 and ep >= MIN_POINTS_TO_PROTECT:
            yours.append((p, ep))
    yours.sort(key=lambda x: x[1], reverse=True)

    gems = []
    for p in free_market_players(client, catalog):
        ep = predict(p)
        if counts.get(int(p.id), 0) == 0 and ep >= DIFFERENTIAL_MIN_POINTS:
            gems.append((p, ep))
    gems.sort(key=lambda x: x[1], reverse=True)

    if not yours and not gems:
        return ("🕵️ Sin diferenciales claros ahora mismo: los jugadores buenos "
                "están muy repartidos en tu liga.")

    lines = ["🕵️ Diferenciales (tu ventaja sobre la liga):", ""]
    if yours:
        lines.append("💎 Tuyos (casi nadie más los tiene, protégelos):")
        for p, ep in yours[:5]:
            lines.append(f"  {p.name} ({p.position.label}) — {ep} pts/j esperados")
        lines.append("")
    if gems:
        lines.append("🔓 Libres que NADIE tiene (fichaje sorpresa):")
        for p, ep in gems[:5]:
            lines.append(f"  {p.name} ({p.position.label}) — {ep} pts/j · {_fmt(p.price)}€")
        lines.append("")
    return "\n".join(lines).rstrip()


def rival_moves(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> list[str]:
    """Detecta fichajes/ventas NUEVOS de los rivales desde la última sincronización.

    Con dedup por estado en BD: la primera vez solo siembra el estado (sin avisar),
    y luego solo reporta lo que cambia. Un único aviso por movimiento, sin spam."""
    from data.db import detect_rival_moves

    catalog = catalog if catalog is not None else client.get_all_players()
    current: dict[str, dict] = {}
    for m in client.get_league_managers():
        ids = list(client.get_manager_clauses(m["id"]).keys())
        current[str(m["id"])] = {"name": m.get("name", "?"), "player_ids": ids}

    def _names(ids):
        return ", ".join((catalog[int(i)].name if int(i) in catalog else str(i)) for i in ids)

    messages = []
    for name, added, removed in detect_rival_moves(current):
        parts = []
        if added:
            parts.append(f"fichó a {_names(added)}")
        if removed:
            parts.append(f"soltó a {_names(removed)}")
        if parts:
            messages.append(f"🕵️ Movimiento rival — {name} {' y '.join(parts)}.")
    return messages


def collect_rival_moves(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> int:
    """Detecta los movimientos de rivales y los ACUMULA (no los envía) para el
    resumen diario. Se llama en cada sincronización; el envío es una vez al día."""
    from data.db import add_pending_rival_moves

    moves = rival_moves(client, catalog)  # detecta, actualiza estado y devuelve la lista
    if not moves:
        return 0
    # Guarda el texto limpio (sin el prefijo individual) para el resumen.
    texts = [m.replace("🕵️ Movimiento rival — ", "").rstrip(".") for m in moves]
    add_pending_rival_moves(texts)
    return len(texts)


def refresh_daily_analysis(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> None:
    """Calcula (en segundo plano) las infracciones del reglamento y la zona de
    castigo, y las guarda en caché. Es la parte PESADA (muchas peticiones), por eso
    se hace en un job periódico y NO al pulsar el botón."""
    import json

    from compliance.checker import compliance_lines, punishment_report
    from data.db import set_setting

    catalog = catalog if catalog is not None else client.get_all_players()
    try:
        lines = compliance_lines(client, catalog)
    except Exception as exc:  # noqa: BLE001
        logger.warning("refresh_daily_analysis: infracciones fallaron (%s)", exc)
        lines = []
    set_setting("cache_compliance", json.dumps(lines, ensure_ascii=False))

    try:
        pun = punishment_report(client, catalog) or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("refresh_daily_analysis: zona de castigo falló (%s)", exc)
        pun = ""
    set_setting("cache_punishment", pun)


def daily_digest(client: BiwengerClient | None = None, catalog: dict[int, Player] | None = None,
                 consume: bool = False) -> str | None:
    """Resumen diario, LEÍDO DE CACHÉ (instantáneo): movimientos de rivales
    acumulados del día + infracciones del reglamento + zona de castigo. Devuelve el
    texto o None si no hay nada.

    - `consume=True` (job de las 15:00): vacía los movimientos acumulados tras leerlos.
    - `consume=False` (botón bajo demanda): solo muestra, no vacía nada.
    """
    import json

    from data.db import (clear_pending_rival_moves, get_pending_rival_moves,
                         get_setting)

    parts: list[str] = []

    moves = get_pending_rival_moves()
    if moves:
        parts.append("🕵️ Movimientos de rivales de hoy:\n" +
                     "\n".join(f"  • {m}" for m in moves))

    try:
        infractions = json.loads(get_setting("cache_compliance") or "[]")
    except Exception:  # noqa: BLE001
        infractions = []
    if infractions:
        parts.append("🚨 Posibles infracciones del reglamento:\n" +
                     "\n".join(f"  • {x}" for x in infractions))

    punishment = get_setting("cache_punishment") or ""
    if punishment:
        parts.append(punishment)

    if consume and moves:
        clear_pending_rival_moves()

    if not parts:
        return None
    return "\n\n".join(parts)


def quiniela_message(client: BiwengerClient) -> str:
    """Pronóstico 1X2 de todos los partidos de la jornada (y lo guarda para evaluarlo)."""
    from quiniela.predictor import quiniela  # import local para evitar ciclos
    from data.db import save_quiniela

    fixtures = client.get_round_fixtures()
    if not fixtures:
        return "No hay partidos de jornada disponibles ahora mismo."

    preds = quiniela(fixtures)
    # Guardamos el pronóstico de esta jornada para el resumen post-jornada.
    round_id = client.get_current_round_id()
    if round_id:
        save_quiniela(round_id, [(p.fixture.home, p.fixture.away, p.pick) for p in preds])

    lines = ["🎯 Pronóstico de la jornada (1 = local, X = empate, 2 = visitante):", ""]
    for p in preds:
        lines.append(f"  {p.pick}  {p.fixture.home} vs {p.fixture.away}  {p.confidence_emoji}")
    lines.append("\n🟢 alta confianza · 🟡 media · 🔴 ajustado (arriesgado)")
    return "\n".join(lines)


def auto_set_lineup(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> str | None:
    """Si la auto-alineación está activada, pone tu once óptimo (una vez por jornada).
    Riesgo cero: la alineación es reversible. Devuelve el aviso o None."""
    from data.db import get_setting, set_setting

    if get_setting("auto_lineup") != "on":
        return None
    round_id = client.get_current_round_id()
    if str(round_id) == (get_setting("lineup_set_round") or ""):
        return None  # ya la pusimos esta jornada

    catalog = catalog if catalog is not None else client.get_all_players()
    players = _enrich(client, client.get_my_team().player_ids, catalog, owned=True)
    result = best_lineup(players)

    # Capitán inteligente (puntos + diferencial), con fallback al de más puntos.
    ep_map = {p.id: predict(p) for p in result.starters}
    counts, n_mgr = _safe_ownership_counts(client)
    captain = best_captain(result.starters, ep_map, counts, n_mgr) or result.captain

    client.set_lineup(
        [p.id for p in result.starters], result.formation,
        captain.id if captain else None,
    )
    set_setting("lineup_set_round", str(round_id))
    cap = f", capitán {captain.name}" if captain else ""
    return (f"⚽ He puesto tu alineación óptima ({result.formation}{cap}). "
            f"Cámbiala tú si quieres antes del cierre.")


def auto_raise_clauses(client: BiwengerClient, catalog: dict[int, Player], team) -> list[str]:
    """Si la auto-cláusula está activada, sube la cláusula de tus cracks vulnerables
    (una vez por jugador). Usa raise_clause (endpoint no confirmado; respeta DRY_RUN)."""
    from data.db import get_setting, set_setting

    if get_setting("auto_clause") != "on":
        return []
    messages = []
    for r in clause_risks(client, catalog, team):
        marker = f"autoclause_{r.player.id}"
        if get_setting(marker):  # ya subida antes, no repetir
            continue
        try:
            client.raise_clause(r.player.id, r.suggested_clause)
            set_setting(marker, "done")
            messages.append(
                f"🛡️ He subido la cláusula de {r.player.name} a {_fmt(r.suggested_clause)}€ "
                f"(estaba baja y podían robártelo)."
            )
        except Exception as exc:  # noqa: BLE001
            messages.append(f"⚠️ No pude subir la cláusula de {r.player.name}: {exc}")
    return messages


def save_predictions_for_round(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> int:
    """Guarda las predicciones de esta jornada (plantilla + mercado libre) para
    poder compararlas luego con lo real y que el bot aprenda."""
    from data.db import save_predictions
    from lineup.predictor import base_per_game, predict

    catalog = catalog if catalog is not None else client.get_all_players()
    round_id = client.get_current_round_id()
    if not round_id:
        return 0

    team = client.get_my_team()
    players = _enrich(client, team.player_ids, catalog, owned=True) + free_market_players(client, catalog)
    seen, rows = set(), []
    for p in players:
        if p.id in seen:
            continue
        seen.add(p.id)
        rows.append({
            "player_id": p.id,
            "base": base_per_game(p),
            "difficulty": p.fixture_difficulty,
            "starter_rate": p.starter_rate,
            "predicted": predict(p),
        })
    return save_predictions(round_id, rows)


def learn_from_round(client: BiwengerClient) -> str | None:
    """Registra los puntos reales de la última jornada y recalibra el predictor.
    Devuelve un texto-resumen del aprendizaje, o None si aún no hay datos."""
    from data.db import record_actuals
    from learning.tuner import retune

    round_id = client.get_current_round_id()
    if not round_id:
        return None
    # Puntos reales de los jugadores de los que teníamos predicción.
    from data.db import get_session, PredictionLog
    with get_session() as s:
        pids = [int(r.player_id) for r in s.query(PredictionLog).filter_by(round_id=str(round_id)).all()]
    if pids:
        points = client.get_round_player_points(round_id, pids)
        record_actuals(round_id, {pid: d.get("points") for pid, d in points.items() if d.get("points") is not None})

    result = retune()
    if not result:
        return None
    return (
        f"🧠 He aprendido de la jornada: con {result['samples']} datos, ajusté la sensibilidad "
        f"a la dificultad a {result['sensitivity']} y la calibración a {result['calib']}. "
        f"Error medio {result['mae_before']} → {result['mae_after']} pts."
    )


def postmatch_message(client: BiwengerClient) -> str:
    """Resumen de la jornada: puntos de tu equipo, MVP, capitán y acierto de quiniela."""
    from data.db import load_quiniela

    round_id = client.get_current_round_id()
    if not round_id:
        return "Aún no hay datos de jornada."

    team = client.get_my_team()
    starters = team.lineup.player_ids if team.lineup else team.player_ids
    captain_id = team.lineup.captain_id if team.lineup else None
    points = client.get_round_player_points(round_id, starters)

    # Solo cuentan los que jugaron (puntos no nulos).
    scored = {pid: d for pid, d in points.items() if d.get("points") is not None}
    if not scored:
        return "📊 La jornada aún no ha terminado (o no hay puntos todavía). Te lo cuento cuando cierre."

    def pts(pid):
        return scored.get(pid, {}).get("points", 0) or 0

    total = sum(pts(pid) for pid in scored)
    if captain_id in scored:
        total += pts(captain_id)  # el capitán dobla

    best = max(scored, key=pts)
    worst = min(scored, key=pts)

    lines = [f"📊 Resumen de la jornada — {total} puntos"]
    lines.append(f"\n⭐ MVP: {scored[best]['name']} ({pts(best)} pts)")
    lines.append(f"😴 El que menos: {scored[worst]['name']} ({pts(worst)} pts)")
    if captain_id and captain_id in scored:
        cap = scored[captain_id]
        good = "✅ buena elección" if captain_id == best else "🤔 mejorable"
        lines.append(f"🎖️ Capitán: {cap['name']} ({pts(captain_id)} pts x2 = {pts(captain_id) * 2}) — {good}")

    # Acierto de quiniela (si la habíamos guardado).
    saved = load_quiniela(round_id)
    results = client.get_round_results(round_id)
    if saved and results:
        hits = sum(1 for r in results if saved.get(f"{r['home']}|{r['away']}") == r["pick"])
        lines.append(f"\n🎯 Quiniela: {hits}/{len(results)} aciertos")

    return "\n".join(lines)


def squad_message(client: BiwengerClient, catalog: dict[int, Player] | None = None) -> str:
    catalog = catalog if catalog is not None else client.get_all_players()
    team = client.get_my_team()
    players = _enrich(client, team.player_ids, catalog, owned=True)

    lines = [f"👥 {team.name} — saldo: {_fmt(team.balance)} €"]
    by_pos: dict[str, list[Player]] = {}
    for p in players:
        by_pos.setdefault(p.position.label, []).append(p)
    for label in ("GK", "DF", "MF", "FW"):
        group = by_pos.get(label, [])
        if group:
            jugadores = ", ".join(f"{p.name} ({_fmt(p.price)}€)" for p in group)
            lines.append(f"\n{label}: {jugadores}")
    return "\n".join(lines)
