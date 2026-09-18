"""What a lot costs to move: freight in from the house, packing on the way out.

The old model charged a flat $15/$25/$60 "shipping penalty" to every lot
regardless of whether anything shipped, while the real per-auction figure it
had already read off the terms page went unused.
"""

from app import models
from app.workers.enrich import (
    DEFAULT_INBOUND_SHIP, FREIGHT_FLOOR, INBOUND_TIER_MULT, LOGISTICS_COST,
    _inbound_shipping,
)


def _lot(ease="EASY", source=None, auction=None):
    return models.Lot(lot_id="x", title="t", logistics_ease=ease,
                      source=source, auction=auction)


def _auction(source="Ship", ship_cost=None):
    return models.Auction(name="a", source=source, ship_cost_estimate=ship_cost)


def test_local_pickup_pays_no_freight():
    lot = _lot(source="Local Pickup", auction=_auction(source="Local Pickup"))
    assert _inbound_shipping(lot) == 0.0


def test_per_lot_source_beats_the_auction():
    """A pickup-only lot inside a shipping auction still costs nothing to get."""
    lot = _lot(source="Local Pickup", auction=_auction(source="Ship", ship_cost=15.99))
    assert _inbound_shipping(lot) == 0.0


def test_uses_the_auctions_own_quote():
    lot = _lot(ease="EASY", source="Ship", auction=_auction(ship_cost=15.99))
    assert _inbound_shipping(lot) == 15.99


def test_bulky_lots_scale_the_quote():
    """A quote above the freight floor still scales by bulk."""
    house = _auction(ship_cost=60.0)
    easy = _inbound_shipping(_lot("EASY", "Ship", house))
    hard = _inbound_shipping(_lot("HARD", "Ship", house))
    assert easy == 60.0
    assert hard == round(60.0 * INBOUND_TIER_MULT["HARD"], 2)
    assert hard > easy


def test_falls_back_when_terms_unread():
    lot = _lot(ease="EASY", source="Ship", auction=_auction(ship_cost=None))
    assert _inbound_shipping(lot) == DEFAULT_INBOUND_SHIP


def test_lot_with_no_auction_does_not_explode():
    assert _inbound_shipping(_lot(source="Ship", auction=None)) == DEFAULT_INBOUND_SHIP


def test_outbound_cost_excludes_postage_the_buyer_pays():
    """Calculated shipping means the carrier bill isn't the seller's, so the
    tier only covers packing and eBay's fee on the buyer-paid postage."""
    assert LOGISTICS_COST["EASY"] < 5
    assert LOGISTICS_COST["EASY"] < LOGISTICS_COST["NEUTRAL"] < LOGISTICS_COST["HARD"]


def test_hard_ship_pays_freight_not_a_scaled_parcel_quote():
    """4x a $12 small-parcel quote is $48; a pallet is not $48."""
    lot = _lot("HARD", "Ship", _auction(ship_cost=12.0))
    assert _inbound_shipping(lot) == FREIGHT_FLOOR["HARD"]


def test_a_generous_house_quote_still_wins_when_higher():
    """The floor is a floor, not a flat rate."""
    lot = _lot("HARD", "Ship", _auction(ship_cost=200.0))
    assert _inbound_shipping(lot) == 800.0        # 200 x 4.0


def test_the_floor_does_not_apply_to_local_pickup():
    """You drive out and collect it — there is no freight to pay."""
    lot = _lot("HARD", "Local Pickup", _auction(source="Local Pickup"))
    assert _inbound_shipping(lot) == 0.0


def test_lighter_tiers_are_untouched_by_the_floor():
    house = _auction(ship_cost=12.0)
    assert _inbound_shipping(_lot("EASY", "Ship", house)) == 12.0
    assert _inbound_shipping(_lot("NEUTRAL", "Ship", house)) == 18.0
