"""Predicción de puntos esperados por jugador para la próxima jornada.

V2: combina forma reciente (o baseline de la temporada pasada al inicio de liga)
con la dificultad del próximo partido que calcula Biwenger (mezcla clasificación
del rival, local/visitante, forma y diferencia de goles). Sigue respetando la
firma `predict(player) -> float` para poder mejorarse sin tocar el optimizador.
"""
from __future__ import annotations

from core.models import Player

# Pesos para las últimas N jornadas de la temporada en curso (más peso a lo reciente).
DEFAULT_WEIGHTS = [0.35, 0.25, 0.18, 0.12, 0.10]

# Nº de jornadas de una temporada de LaLiga, para convertir puntos totales del año
# pasado en una media por partido (baseline cuando aún no hay datos de la actual).
SEASON_GAMES = 38

# Cuánto pesa la dificultad del partido. La dificultad de Biwenger es 0-100
# (~50 neutral, mayor = más difícil). Con sensibilidad 1.0, un partido muy fácil
# (0) multiplica ~x1.5 y uno muy difícil (100) ~x0.5. Lo dejamos algo amortiguado
# para no sobre-reaccionar a un solo dato.
FIXTURE_SENSITIVITY = 0.6


def _base_per_game(player: Player, weights: list[float] | None = None) -> float:
    """Puntos por partido esperados por forma: temporada actual si hay datos,
    si no, media de la temporada pasada."""
    weights = weights or DEFAULT_WEIGHTS
    recent = player.fitness[-len(weights):] if player.fitness else []
    if recent:
        used = weights[-len(recent):]
        norm = sum(used) or 1.0
        return sum(pts * w for pts, w in zip(reversed(recent), used)) / norm
    if player.points_last_season:
        return player.points_last_season / SEASON_GAMES
    return 0.0


# Alias público para que el sistema de aprendizaje guarde el 'base' del jugador.
base_per_game = _base_per_game


def _tuned_params() -> tuple[float, float]:
    """Lee los parámetros que el bot ha aprendido (sensibilidad, calibración).
    Si aún no ha aprendido nada, usa los valores por defecto."""
    try:
        from data.db import get_setting
        sensitivity = float(get_setting("fixture_sensitivity") or FIXTURE_SENSITIVITY)
        calib = float(get_setting("calib_factor") or 1.0)
        return sensitivity, calib
    except Exception:  # noqa: BLE001
        return FIXTURE_SENSITIVITY, 1.0


def _fixture_factor(player: Player, sensitivity: float) -> float:
    """Factor multiplicador según lo fácil/difícil del próximo partido."""
    if player.fixture_difficulty is None:
        return 1.0
    return 1.0 + (50.0 - player.fixture_difficulty) / 100.0 * sensitivity


def _starter_factor(player: Player) -> float:
    """Penaliza a los que no son titulares fijos. Un titular (1.0) no se toca;
    un suplente (0.0) baja al 60% de su esperado. Neutral al inicio de liga."""
    return 0.6 + 0.4 * max(0.0, min(1.0, player.starter_rate))


def predict(player: Player, weights: list[float] | None = None) -> float:
    """Puntos esperados de un jugador para la próxima jornada.

    Aplica los parámetros que el bot ha aprendido de sus propios aciertos
    (sensibilidad a la dificultad y factor de calibración)."""
    if player.is_injured_or_suspended:
        return 0.0
    weights = weights or DEFAULT_WEIGHTS
    sensitivity, calib = _tuned_params()
    expected = (
        _base_per_game(player, weights)
        * _fixture_factor(player, sensitivity)
        * _starter_factor(player)
        * calib
    )
    return round(max(expected, 0.0), 2)


def predict_all(players: list[Player]) -> dict[int, float]:
    return {p.id: predict(p) for p in players}
