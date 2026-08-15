"""Detector de infracciones del reglamento interno de la liga.

Las reglas concretas viven en `config/league_rules.py` (privado, en .gitignore).
Si ese fichero no existe, el detector queda apagado y no pasa nada. Todo esto es
SOLO lectura y SOLO informa: nunca actúa sobre la liga.

Reglas que comprueba:
- Cláusula por debajo del mínimo del reglamento (por tramos de valor). Fiable.
- Capitán con valor de mercado por encima del tope. Requiere leer la alineación
  del rival (best-effort; Biwenger no siempre la expone).
- Más de N jugadores del mismo club en el once. Requiere leer la alineación.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _load_rules():
    """Carga config/league_rules.py si existe. Si no, devuelve None (detector off)."""
    try:
        from config import league_rules as rules
    except Exception:  # noqa: BLE001 - fichero ausente = detector apagado
        return None
    if not getattr(rules, "RULE_CHECK_ENABLED", False):
        return None
    return rules


def _fmt(n) -> str:
    return f"{int(n):,}".replace(",", ".")


def _team_names(catalog) -> dict:
    """team_id -> un nombre representativo (para nombrar el club en los avisos)."""
    names: dict = {}
    for p in catalog.values():
        if p.team_id is not None and p.team_id not in names:
            names[p.team_id] = getattr(p, "team_name", None) or f"equipo {p.team_id}"
    return names


def required_min_clause(vm: int, tiers) -> int:
    """Cláusula mínima exigida por el reglamento para un valor de mercado dado."""
    for upper, mult in tiers:
        if vm <= upper:
            return int(round(vm * mult))
    return int(round(vm * tiers[-1][1]))


def check_low_clauses(client, catalog, rules) -> list[str]:
    """Jugadores de rivales con la cláusula por debajo del mínimo del reglamento."""
    tiers = getattr(rules, "CLAUSE_MIN_TIERS", None)
    if not tiers:
        return []
    # ¿Ya se exigen cláusulas? (empiezan después de cierta jornada).
    from_round = getattr(rules, "CLAUSE_RULES_FROM_ROUND", 0)
    try:
        current_round = client.get_current_round_id() or 0
    except Exception:  # noqa: BLE001
        current_round = 0
    if from_round and current_round and current_round <= from_round:
        return []

    out: list[str] = []
    for m in client.get_league_managers():
        try:
            clauses = client.get_manager_clauses(m["id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sin cláusulas de %s: %s", m.get("name"), exc)
            continue
        for pid, info in clauses.items():
            player = catalog.get(int(pid))
            clause = info.get("clause")
            if player is None or not clause:
                continue
            req = required_min_clause(player.price, tiers)
            if clause < req:
                out.append(
                    f"{m['name']}: {player.name} con cláusula {_fmt(clause)}€ "
                    f"(mínimo del reglamento {_fmt(req)}€)"
                )
    return out


def check_expensive_captains(client, catalog, rules) -> list[str]:
    """Rivales que capitanean a un jugador por encima del tope de valor."""
    cap_max = getattr(rules, "CAPTAIN_MAX_VALUE", None)
    if not cap_max:
        return []
    out: list[str] = []
    for m in client.get_league_managers():
        lu = client.get_manager_lineup(m["id"])
        if not lu or not lu.get("captain_id"):
            continue
        cap = catalog.get(int(lu["captain_id"]))
        if cap and cap.price > cap_max:
            out.append(
                f"{m['name']}: capitán {cap.name} vale {_fmt(cap.price)}€ "
                f"(tope {_fmt(cap_max)}€)"
            )
    return out


def check_club_limit(client, catalog, rules) -> list[str]:
    """Rivales que alinean más de N jugadores del mismo club en el once."""
    max_per_club = getattr(rules, "MAX_PLAYERS_PER_CLUB", None)
    if not max_per_club:
        return []
    names = _team_names(catalog)
    out: list[str] = []
    for m in client.get_league_managers():
        lu = client.get_manager_lineup(m["id"])
        if not lu:
            continue
        counts: dict = {}
        for pid in lu["player_ids"]:
            p = catalog.get(int(pid))
            if p is None or p.team_id is None:
                continue
            counts[p.team_id] = counts.get(p.team_id, 0) + 1
        for team_id, n in counts.items():
            if n > max_per_club:
                out.append(
                    f"{m['name']}: {n} jugadores del {names.get(team_id, 'mismo club')} "
                    f"en el once (máximo {max_per_club})"
                )
    return out


def compute_punishment(scores: dict, fines_by_position: dict) -> list[tuple]:
    """Zona de castigo: a partir de {nombre: puntos}, asigna las multas por puesto.

    El peor puntuado ocupa el último puesto. En empates dentro de la zona de
    castigo, la suma de las multas de los puestos afectados se reparte a partes
    iguales entre los empatados (reglamento 1.3). Devuelve [(nombre, multa €)].
    """
    if not scores or not fines_by_position:
        return []
    n = len(scores)
    ranked = sorted(scores.items(), key=lambda kv: kv[1])  # ascendente: peor primero
    # Puesto de cada manager: el peor (índice 0) es el puesto n.
    position = {name: n - i for i, (name, _pts) in enumerate(ranked)}

    from collections import defaultdict
    by_points = defaultdict(list)
    for name, pts in ranked:
        by_points[pts].append(name)

    out: list[tuple] = []
    for pts, names in by_points.items():
        occupied = [position[nm] for nm in names]
        fines = [fines_by_position[p] for p in occupied if p in fines_by_position]
        if fines:
            share = round(sum(fines) / len(names), 2)
            for nm in names:
                out.append((nm, share))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def punishment_report(client, catalog) -> str | None:
    """Aviso ORIENTATIVO de la zona de castigo de la última jornada (quién pagaría
    y cuánto). Best-effort: si no se puede reconstruir, devuelve None."""
    rules = _load_rules()
    if rules is None or not getattr(rules, "PUNISHMENT_ENABLED", False):
        return None
    fines = getattr(rules, "PUNISHMENT_BY_POSITION", None)
    if not fines:
        return None
    try:
        round_id = client.get_current_round_id()
    except Exception:  # noqa: BLE001
        round_id = None
    if not round_id:
        return None
    # Margen tras el reinicio de la 2ª vuelta: sin sanciones esas jornadas.
    reset = getattr(rules, "RESET_ROUND", 0)
    margin = getattr(rules, "PUNISHMENT_MARGIN_AFTER_RESET", 0)
    if reset and margin and reset <= round_id < reset + margin:
        return None
    try:
        scores = client.get_round_manager_scores(round_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Zona de castigo no reconstruible: %s", exc)
        return None
    penalties = compute_punishment(scores, fines)
    if not penalties:
        return None
    lines = ["💸 Zona de castigo de la jornada (orientativo, confírmalo en la app):"]
    for name, fine in penalties:
        lines.append(f"  • {name} paga {fine:g}€")
    return "\n".join(lines)


def compliance_lines(client, catalog) -> list[str]:
    """Todas las infracciones detectadas ahora mismo (lista de textos). Vacía si el
    detector está apagado o no hay nada que reprochar."""
    rules = _load_rules()
    if rules is None:
        return []
    lines: list[str] = []
    for check in (check_low_clauses, check_expensive_captains, check_club_limit):
        try:
            lines.extend(check(client, catalog, rules))
        except Exception as exc:  # noqa: BLE001 - un chequeo no debe tumbar el resto
            logger.warning("Chequeo de reglamento falló (%s): %s", check.__name__, exc)
    return lines
