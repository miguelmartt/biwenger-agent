"""Tests del sniping pre-autorizado: marca objetivo y puja en la ventana de cierre."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from core.models import MarketListing
from data.db import active_snipe_targets, add_snipe_target, cancel_snipe, init_db
from bidding.sniper import process_snipes, SNIPE_WINDOW_SECONDS


class _Client:
    """Cliente falso: devuelve un mercado controlado y registra las pujas."""
    def __init__(self, listings):
        self._listings = listings
        self.bids = []

    def get_market(self):
        return self._listings

    def place_bid(self, pid, amount, seller_id=None):
        self.bids.append((pid, amount))
        return None


def _listing(pid, secs_to_close, seller_id=None):
    return MarketListing(
        player_id=pid, price=150000,
        until=datetime.fromtimestamp(time.time() + secs_to_close, tz=timezone.utc),
        seller_id=seller_id,
    )


def test_snipe_fires_only_near_close():
    init_db()
    add_snipe_target(555, "Chollo", 200000)

    # Aún lejos del cierre: no puja.
    c1 = _Client([_listing(555, secs_to_close=600)])
    assert process_snipes(c1) and not c1.bids or c1.bids == []
    assert c1.bids == []
    assert len(active_snipe_targets()) == 1  # sigue pendiente

    # Cierre inminente: puja el tope autorizado.
    c2 = _Client([_listing(555, secs_to_close=SNIPE_WINDOW_SECONDS - 30)])
    process_snipes(c2)
    assert c2.bids == [(555, 200000)]
    assert active_snipe_targets() == []  # ya no está pendiente


def test_snipe_ignores_teammate_listings():
    init_db()
    add_snipe_target(777, "DeCompañero", 200000)
    # Está en el mercado pero lo vende un compañero -> el filtro lo excluye, no puja.
    c = _Client([_listing(777, secs_to_close=10, seller_id=12731629)])
    process_snipes(c)
    assert c.bids == []


def test_cancel_snipe():
    init_db()
    add_snipe_target(888, "Quitar", 100000)
    assert cancel_snipe(888) is True
    assert all(t.player_id != "888" for t in active_snipe_targets())


if __name__ == "__main__":
    test_snipe_fires_only_near_close()
    test_snipe_ignores_teammate_listings()
    test_cancel_snipe()
    print("OK: sniping pre-autorizado correcto")
