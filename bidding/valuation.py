"""Cálculo de puja ideal / puja máxima para un jugador objetivo.

Combina: precio de mercado, tendencia reciente, cuánto lo necesitas
(¿hueco real en tu plantilla o capricho?) y presupuesto disponible tras
reservar un colchón de seguridad. No pretende ser tan sofisticado como los
modelos de sitios como Analítica Fantasy/Biwinner, pero es transparente y
fácil de ajustar — a diferencia de esos, aquí ves y controlas cada peso.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.models import Player
from config.settings import settings

# Cuánto por encima del valor de mercado estamos dispuestos a pagar según
# lo urgente que sea el fichaje. Son puntos de partida razonables, ajusta a gusto.
NEED_MULTIPLIER = {
    "urgent": 1.25,   # hueco real en la posición, sin alternativas en plantilla
    "upgrade": 1.12,  # mejora clara sobre lo que ya tienes
    "depth": 1.03,    # solo para tener banquillo/rotación
}

# Márgenes sobre el precio de mercado para los tres niveles de puja que se
# ofrecen al fichar. Pujar el mínimo (mercado) se pierde si hay competencia;
# 'competitiva' y 'fuerte' suben para ganar la subasta a ciegas.
BID_LEVELS = {"minima": 1.0, "competitiva": 1.15, "fuerte": 1.35}


def bid_levels(market_price: int, available_budget: int) -> dict[str, int]:
    """Devuelve {nivel: importe} para pujar, capado por el presupuesto disponible."""
    cap = max(available_budget - settings.budget_safety_margin, 0)
    result: dict[str, int] = {}
    for name, mult in BID_LEVELS.items():
        amount = round(market_price * mult / 1000) * 1000  # redondeo a miles
        result[name] = min(amount, cap) if cap else amount
    return result


@dataclass
class BidRecommendation:
    ideal_bid: int
    max_bid: int
    reasoning: str


def recommend_bid(
    player: Player,
    need_level: str,
    available_budget: int,
) -> BidRecommendation:
    if need_level not in NEED_MULTIPLIER:
        raise ValueError(f"need_level debe ser uno de {list(NEED_MULTIPLIER)}")

    base = player.price

    # Si el precio lleva varios días subiendo, hay que ofrecer más para
    # asegurar que ganamos la puja (otros managers también lo habrán notado).
    trend_adjustment = 1 + max(player.price_trend_pct, 0) / 100

    multiplier = NEED_MULTIPLIER[need_level] * trend_adjustment

    ideal = round(base * multiplier)
    # La puja máxima añade un margen extra de seguridad para ganar la subasta
    # in extremis, pero nunca por encima del presupuesto disponible menos el colchón.
    hard_cap = available_budget - settings.budget_safety_margin
    max_bid = min(round(ideal * 1.08), max(hard_cap, 0))

    reasoning = (
        f"precio_base={base}, tendencia={player.price_trend_pct:+.1f}%, "
        f"nivel_necesidad={need_level} (x{NEED_MULTIPLIER[need_level]}), "
        f"presupuesto_disponible={available_budget}, colchon={settings.budget_safety_margin}"
    )

    return BidRecommendation(ideal_bid=ideal, max_bid=max_bid, reasoning=reasoning)
