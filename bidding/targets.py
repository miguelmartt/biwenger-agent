"""Selección de objetivos de fichaje en el mercado.

REGLA DE NEGOCIO: el agente NUNCA puja por jugadores
que estén siendo vendidos por otros managers de la liga (compañeros). Solo
considera jugadores del mercado libre (los que salen automáticamente al
mercado, con vendedor `None`). Este filtro se aplica aquí, de forma que el
sniper nunca llega a ver siquiera un jugador de un compañero como candidato.
"""
from __future__ import annotations

from core.models import MarketListing


def free_market_only(listings: list[MarketListing]) -> list[MarketListing]:
    """Descarta los jugadores puestos a la venta por managers de la liga."""
    return [lst for lst in listings if not lst.is_from_teammate]


def eligible_targets(
    listings: list[MarketListing],
    wanted_player_ids: set[int] | None = None,
) -> list[MarketListing]:
    """Objetivos válidos: del mercado libre y, si se pasa, dentro de la lista deseada."""
    free = free_market_only(listings)
    if wanted_player_ids is None:
        return free
    return [lst for lst in free if lst.player_id in wanted_player_ids]
