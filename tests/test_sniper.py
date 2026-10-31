"""Tests del sniping pre-autorizado: marca objetivo y puja en la ventana de cierre,
solo en el mismo anuncio en que se marcó."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from core.models import MarketListing
from data.db import active_snipe_targets, add_snipe_target, cancel_snipe, init_db, list_snipe_targets
from bidding.sniper import process_snipes, SNIPE_WINDOW_SECONDS

# Un vendedor cualquiera: lo que importa es que NO es None (lo vende un compañero).
TEAMMATE_ID = 123456


class _Client:
    """Cliente falso: devuelve un mercado controlado y registra las pujas."""
    def __init__(self, listings, balance=50_000_000, balance_error=None):
        self._listings = listings
        self._balance = balance
        self._balance_error = balance_error
        self.bids = []

    def get_market(self):
        return self._listings

    def get_balance(self):
        if self._balance_error:
            raise self._balance_error
        return self._balance

    def place_bid(self, pid, amount, seller_id=None):
        self.bids.append((pid, amount))
        return None


def _until(secs_to_close):
    return int(time.time() + secs_to_close)


def _listing(pid, until, seller_id=None, price=150000):
    return MarketListing(
        player_id=pid, price=price,
        until=datetime.fromtimestamp(until, tz=timezone.utc),
        seller_id=seller_id,
    )


def _status(pid):
    return next(t.status for t in list_snipe_targets() if t.player_id == str(pid))


def test_snipe_fires_only_near_close():
    init_db()
    until = _until(600)
    add_snipe_target(555, "Chollo", 200000, listing_until=until)

    # Aún lejos del cierre: no puja.
    c1 = _Client([_listing(555, until)])
    process_snipes(c1)
    assert c1.bids == []
    assert len(active_snipe_targets()) == 1  # sigue pendiente

    # Cierre inminente (el mismo anuncio): puja el tope autorizado.
    soon = _until(SNIPE_WINDOW_SECONDS - 30)
    add_snipe_target(555, "Chollo", 200000, listing_until=soon)
    c2 = _Client([_listing(555, soon)])
    process_snipes(c2)
    assert c2.bids == [(555, 200000)]
    assert active_snipe_targets() == []  # ya no está pendiente


def test_snipe_ignores_teammate_listings():
    init_db()
    until = _until(10)
    add_snipe_target(777, "DeCompañero", 200000, listing_until=until)
    # Está en el mercado pero lo vende un compañero -> el filtro lo excluye, no puja.
    c = _Client([_listing(777, until, seller_id=TEAMMATE_ID)])
    process_snipes(c)
    assert c.bids == []


def test_cancel_snipe():
    init_db()
    add_snipe_target(888, "Quitar", 100000, listing_until=_until(600))
    assert cancel_snipe(888) is True
    assert all(t.player_id != "888" for t in active_snipe_targets())


def test_snipe_only_bids_on_the_listing_it_was_marked_on():
    # Marcado en el anuncio que cerraba ayer; hoy el jugador ha vuelto a salir: no se puja.
    init_db()
    add_snipe_target(901, "Repetido", 200000, listing_until=_until(-86_400))
    c = _Client([_listing(901, _until(30))])
    messages = process_snipes(c)
    assert c.bids == []
    assert _status(901) == "expired"
    assert messages and "caducado" in messages[0]


def test_snipe_expires_when_its_market_closed():
    init_db()
    add_snipe_target(902, "Cerrado", 200000, listing_until=_until(-60))
    c = _Client([])  # ya no está en el mercado
    process_snipes(c)
    assert c.bids == [] and _status(902) == "expired"


def test_snipe_waits_if_listing_missing_before_close():
    init_db()
    add_snipe_target(903, "Raro", 200000, listing_until=_until(3600))
    process_snipes(_Client([]))
    assert _status(903) == "pending"


def test_legacy_snipe_without_listing_is_never_bid():
    # Objetivos marcados antes de este cambio (sin anuncio asociado): se quitan, no se pujan.
    init_db()
    from data.db import SnipeTarget, get_session
    with get_session() as s:
        s.add(SnipeTarget(player_id="904", player_name="Viejo", max_bid=200000))
        s.commit()
    c = _Client([_listing(904, _until(30))])
    messages = process_snipes(c)
    assert c.bids == [] and _status(904) == "expired"
    assert "versión anterior" in messages[0]


if __name__ == "__main__":
    test_snipe_fires_only_near_close()
    test_snipe_ignores_teammate_listings()
    test_cancel_snipe()
    print("OK: sniping pre-autorizado correcto")
