"""Pronóstico de resultados de la jornada (1X2) para la quiniela.

Usa la dificultad que Biwenger calcula para cada equipo en su partido (menor
dificultad = favorito). Si las dos dificultades están muy parejas, se pronostica
empate. La confianza depende de lo grande que sea la diferencia.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.models import Fixture

# Diferencia de dificultad por debajo de la cual el partido se considera parejo (empate).
DRAW_MARGIN = 8.0

# Pesos base con los que Biwenger compone su dificultad. Subimos el de la FORMA
# reciente (racha de resultados) por encima del 25% original, como pidió el usuario.
BASE_WEIGHTS = {"standings": 0.35, "homeAway": 0.20, "form": 0.25, "goalDiff": 0.20}
FORM_BOOST = 0.20  # forma pasa de 0.25 a 0.45 del total (racha reciente manda más)


def _adjusted_difficulty(rating: float | None, components: dict[str, float]) -> float | None:
    """Recalcula la dificultad dando más peso a la forma reciente.

    Si no hay desglose de componentes, usa el rating tal cual de Biwenger.
    """
    if rating is None:
        return None
    if not all(k in components for k in BASE_WEIGHTS):
        return rating
    weights = dict(BASE_WEIGHTS)
    weights["form"] += FORM_BOOST
    total = sum(weights.values())
    return sum(components[k] * weights[k] for k in weights) / total


@dataclass
class Prediction:
    fixture: Fixture
    pick: str          # '1' (gana local) | 'X' (empate) | '2' (gana visitante)
    confidence: str    # 'alta' | 'media' | 'baja'

    @property
    def confidence_emoji(self) -> str:
        return {"alta": "🟢", "media": "🟡", "baja": "🔴"}[self.confidence]


def predict_result(fx: Fixture) -> Prediction:
    # Dificultad reponderada dando más peso a la racha reciente.
    hd = _adjusted_difficulty(fx.home_difficulty, fx.home_components)
    ad = _adjusted_difficulty(fx.away_difficulty, fx.away_components)
    if hd is None or ad is None:
        return Prediction(fx, "X", "baja")

    # diff > 0 => el local tiene el partido más fácil (es favorito).
    diff = ad - hd
    if diff > DRAW_MARGIN:
        pick = "1"
    elif diff < -DRAW_MARGIN:
        pick = "2"
    else:
        pick = "X"

    magnitude = abs(diff)
    if magnitude >= 25:
        confidence = "alta"
    elif magnitude >= 12:
        confidence = "media"
    else:
        confidence = "baja"

    return Prediction(fixture=fx, pick=pick, confidence=confidence)


def quiniela(fixtures: list[Fixture]) -> list[Prediction]:
    return [predict_result(f) for f in fixtures]
