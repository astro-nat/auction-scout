"""Whether you'd pick a lot up decides its freight. Pure.

Found on production: lots at sales 24 minutes away carried HiBid's "Ship"
tag and were charged freight you would never pay, while a sale tagged
Local Pickup said "Shipping only." in its own pickup text.
"""

import pytest

from app import models
from app.services.pickup import is_ship_only, will_pick_up
from app.workers import enrich


@pytest.mark.parametrize("text,expected", [
    ("Shipping only.", True),
    ("No local pickup - all items ship", True),
    ("We do not offer pickup", True),
    ("Local pick up anytime by appointment\nDelivery extra charge", False),
    ("Auction closes 10/04, pickup Tuesday 10/06", False),
    ("", False),
    (None, False),
])
def test_ship_only_is_read_from_the_pickup_text(text, expected):
    assert is_ship_only(text) is expected


def _lot(minutes, ship_only=False, source="Ship"):
    a = models.Auction(name="t", source="Local Pickup", drive_minutes=minutes,
                       ship_only=ship_only, ship_cost_estimate=None)
    return models.Lot(lot_id="x", title="t", source=source, logistics_ease="EASY", auction=a)


def test_a_ship_tagged_lot_within_reach_costs_no_freight():
    lot = _lot(24)
    assert will_pick_up(lot.auction)
    assert enrich._inbound_shipping(lot) == 0.0


def test_a_ship_only_sale_is_charged_freight_even_nearby():
    lot = _lot(33, ship_only=True, source="Local Pickup")
    assert not will_pick_up(lot.auction)
    assert enrich._inbound_shipping(lot) > 0


def test_beyond_45_minutes_a_ship_lot_still_pays_freight():
    assert enrich._inbound_shipping(_lot(60)) > 0
