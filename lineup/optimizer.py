"""Solver de alineación óptima (programación lineal entera, vía PuLP).

Dado tu plantilla y una puntuación esperada por jugador, elige los 11
titulares que maximizan la suma de puntos esperados respetando las reglas de
formación (cupos mín/máx por posición). Prueba varias formaciones habituales
y se queda con la de mayor puntuación total.
"""
from __future__ import annotations

from dataclasses import dataclass

import pulp

from core.models import Player, Position
from lineup.predictor import predict_all

# formación -> (min GK, min DF, min MF, min FW) y siempre 11 titulares en total.
# Biwenger normalmente exige exactamente 1 portero, así que se fuerza aparte.
FORMATIONS: dict[str, dict[Position, int]] = {
    "4-4-2": {Position.DEFENDER: 4, Position.MIDFIELDER: 4, Position.FORWARD: 2},
    "4-3-3": {Position.DEFENDER: 4, Position.MIDFIELDER: 3, Position.FORWARD: 3},
    "3-5-2": {Position.DEFENDER: 3, Position.MIDFIELDER: 5, Position.FORWARD: 2},
    "3-4-3": {Position.DEFENDER: 3, Position.MIDFIELDER: 4, Position.FORWARD: 3},
    "5-3-2": {Position.DEFENDER: 5, Position.MIDFIELDER: 3, Position.FORWARD: 2},
    "5-4-1": {Position.DEFENDER: 5, Position.MIDFIELDER: 4, Position.FORWARD: 1},
}


@dataclass
class LineupResult:
    formation: str
    starters: list[Player]
    total_expected_points: float
    captain: Player | None = None  # titular con más puntos esperados (dobla puntos)


def _solve_for_formation(
    players: list[Player], expected_points: dict[str, float], formation: str
) -> LineupResult | None:
    requirements = FORMATIONS[formation]
    prob = pulp.LpProblem(f"lineup_{formation}", pulp.LpMaximize)

    x = {p.id: pulp.LpVariable(f"x_{p.id}", cat="Binary") for p in players}

    prob += pulp.lpSum(x[p.id] * expected_points.get(p.id, 0.0) for p in players)

    # Exactamente 11 titulares.
    prob += pulp.lpSum(x.values()) == 11

    # Exactamente 1 portero.
    goalkeepers = [p for p in players if p.position == Position.GOALKEEPER]
    prob += pulp.lpSum(x[p.id] for p in goalkeepers) == 1

    # Cupos exactos por línea según la formación elegida.
    for position, count in requirements.items():
        in_position = [p for p in players if p.position == position]
        prob += pulp.lpSum(x[p.id] for p in in_position) == count

    # No alinear lesionados/sancionados.
    for p in players:
        if p.is_injured_or_suspended:
            prob += x[p.id] == 0

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        return None

    starters = [p for p in players if pulp.value(x[p.id]) == 1]
    total = sum(expected_points.get(p.id, 0.0) for p in starters)
    captain = max(starters, key=lambda p: expected_points.get(p.id, 0.0)) if starters else None
    return LineupResult(
        formation=formation,
        starters=starters,
        total_expected_points=round(total, 2),
        captain=captain,
    )


def best_lineup(players: list[Player]) -> LineupResult:
    """Prueba todas las formaciones soportadas y devuelve la de mayor puntuación."""
    my_players = [p for p in players if p.is_owned_by_me]
    if len(my_players) < 11:
        raise ValueError(f"Solo hay {len(my_players)} jugadores propios, se necesitan 11")

    expected_points = predict_all(my_players)

    candidates = [
        result
        for formation in FORMATIONS
        if (result := _solve_for_formation(my_players, expected_points, formation)) is not None
    ]
    if not candidates:
        raise RuntimeError("No se pudo resolver ninguna formación con la plantilla actual")

    return max(candidates, key=lambda r: r.total_expected_points)
