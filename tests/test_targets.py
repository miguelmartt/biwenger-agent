"""Verifica la regla: nunca pujar por jugadores vendidos por compañeros de liga."""
from __future__ import annotations

from datetime import datetime, timezone

from core.models import MarketListing
from bidding.targets import eligible_targets, free_market_only


def _listing(player_id, seller_id=None):
    return MarketListing(
        player_id=player_id,
        price=1_000_000,
        until=datetime.now(tz=timezone.utc),
        seller_id=seller_id,
        seller_name="Compañero" if seller_id else None,
    )


def test_free_market_only_excludes_teammates():
    listings = [
        _listing(1, seller_id=None),        # mercado libre -> válido
        _listing(2, seller_id=12731629),    # lo vende un compañero -> excluido
        _listing(3, seller_id=None),        # mercado libre -> válido
    ]
    free = free_market_only(listings)
    ids = {l.player_id for l in free}
    assert ids == {1, 3}
    assert all(not l.is_from_teammate for l in free)


def test_eligible_targets_respects_wanted_list():
    listings = [_listing(1, None), _listing(2, None), _listing(3, 999)]
    targets = eligible_targets(listings, wanted_player_ids={1, 3})
    # el 3 lo vende un compañero (aunque esté en la lista deseada) -> fuera
    # el 2 es libre pero no está en la lista deseada -> fuera
    assert {l.player_id for l in targets} == {1}


if __name__ == "__main__":
    test_free_market_only_excludes_teammates()
    test_eligible_targets_respects_wanted_list()
    print("OK: la regla de no fichar a compañeros se respeta")
