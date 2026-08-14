"""Análisis de tendencia/momentum de precio a partir del histórico de Biwenger.

Clasifica a cada jugador en un estado (subiendo, techo, bajando, estable) y
proyecta el precio a muy corto plazo. Es la base para decidir cuándo comprar
y cuándo vender.
"""
from __future__ import annotations

from dataclasses import dataclass

# Umbral (%) para considerar que un precio se mueve de verdad y no es ruido.
MOVE_THRESHOLD_PCT = 1.5


@dataclass
class Trend:
    state: str            # 'subiendo' | 'techo' | 'bajando' | 'estable' | 'sin_datos'
    change_3d_pct: float  # variación % en las últimas ~3 muestras
    change_7d_pct: float  # variación % en las últimas ~7 muestras
    projected_next: int   # precio estimado para los próximos días

    @property
    def emoji(self) -> str:
        return {
            "subiendo": "📈",
            "techo": "⛰️",
            "bajando": "📉",
            "estable": "➖",
            "sin_datos": "❔",
        }[self.state]


def _pct(new: float, old: float) -> float:
    if not old:
        return 0.0
    return round((new - old) / old * 100, 2)


def analyze_trend(price_history: list[tuple[int, int]]) -> Trend:
    """Analiza el histórico [(YYMMDD, precio), ...] y devuelve la tendencia."""
    prices = [p for _, p in price_history]
    if len(prices) < 3:
        last = prices[-1] if prices else 0
        return Trend("sin_datos", 0.0, 0.0, last)

    last = prices[-1]
    change_3d = _pct(last, prices[-4] if len(prices) >= 4 else prices[0])
    change_7d = _pct(last, prices[-8] if len(prices) >= 8 else prices[0])

    # Pendiente reciente (últimas dos variaciones) para proyectar y detectar techo.
    recent_slope = prices[-1] - prices[-2]
    prev_slope = prices[-2] - prices[-3]

    if change_3d >= MOVE_THRESHOLD_PCT:
        state = "subiendo"
    elif change_3d <= -MOVE_THRESHOLD_PCT:
        state = "bajando"
    elif change_7d >= MOVE_THRESHOLD_PCT and recent_slope <= 0:
        # venía subiendo pero se ha frenado/gira -> ha hecho techo
        state = "techo"
    else:
        state = "estable"

    projected = int(last + recent_slope)
    return Trend(state=state, change_3d_pct=change_3d, change_7d_pct=change_7d, projected_next=projected)
