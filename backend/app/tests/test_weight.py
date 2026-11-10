"""Shipping weight estimates. Pure."""

import pytest

from app import models
from app.services import weight
from app.workers import enrich


@pytest.mark.parametrize("title,tier,lb", [
    ("Vtg Coleman Model 220J Double Mantle Lantern", "NEUTRAL", 4),
    ("Pampered Chef 1-Quart Enameled Cast Iron Baker", "NEUTRAL", 7),
    ("Drive Medical Cruiser III Folding Wheelchair", "HARD", 40),
    ("Vintage Metal Torchiere Floor Lamp Glass Shade", "HARD", 15),   # not a 6 lb lamp
    ("Golfmate Jr. Youth Stand Golf Bag", "HARD", 8),                  # not a 1.5 lb bag
    ("Signed Kenneth Lane 36\" Faceted Necklace", "EASY", 0.2),
    # #660 spot-check misses.
    ("Framed Casablanca French Movie Poster", "NEUTRAL", 8),
    ("Framed NASA Space Shuttle Discovery Photograph", "NEUTRAL", 8),
    ("Casablanca Movie Poster", "EASY", 1),
    ("Pauluhn Marine 400W Hazardous Location Floodlight", "NEUTRAL", 35),
    ("Vintage Sun Country Cooler Beach Umbrella", "NEUTRAL", 5),
    ("Igloo 48 Qt Cooler", "NEUTRAL", 8),
    # Rule order: the specific item before the general word.
    ("Swarovski Kris Bear Lucky Charm Figurine", "NEUTRAL", 0.5),
    ("Swarovski Brown Bear Bag Charm", "NEUTRAL", 0.5),
    ("Greenlee 35713 6-3/8\" Recessed Light Hole Saw", "NEUTRAL", 1.5),
    ("DeWalt Circular Saw", "NEUTRAL", 6),
    ("West Virginia Mountaineers #44 Basketball Jersey", "NEUTRAL", 1.5),
    ("Evan Gattis #11 MLB Baseball in Display Case", "NEUTRAL", 1),
    # Counts multiply.
    ("Set of 4 Dog Man Series Books", "NEUTRAL", 6),
    ("Vintage Fishing Landing Nets Duo", "NEUTRAL", 4),
    # No item type, so the count can't scale anything: one parcel.
    ("Assorted Extension Cords (3)", "NEUTRAL", 5),
    # No item type: the ship tier's parcel.
    ("Mystery Box", "HARD", 40),
    ("Mystery Box", None, None),
])
def test_estimate(title, tier, lb):
    assert weight.estimate(title, tier) == lb


@pytest.mark.parametrize("value,lb", [
    (3.25, 3.2), ("12", 12.0), (0, None), (-1, None), (5000, None), ("heavy", None), (None, None),
])
def test_ai_weight_must_be_plausible(value, lb):
    assert weight.from_ai(value) == lb


def _lot(title):
    lot = models.Lot(lot_id="1", title=title, logistics_ease="NEUTRAL", source="Ship",
                     current_bid=2, next_bid=2, unreachable_pickup=False,
                     auction=models.Auction(name="a", source="Ship", buyer_premium_mult=1.18))
    e = models.Enrichment(lot_id=1, user_overrides=[], est_resale=40, comp_count=5,
                          verdict="normal wear and tear", price_source="sold (SoldComps)")
    lot.enrichment = e
    return lot, e


def test_grading_fills_in_an_estimate():
    lot, e = _lot("Coleman Green Metal Fuel Lantern")
    enrich._apply_roi(lot, e)
    assert (e.weight_lb, e.weight_source) == (4, "est")


def test_an_ai_weight_survives_regrading():
    lot, e = _lot("Coleman Green Metal Fuel Lantern")
    enrich._take_ai_weight(e, 5.5)
    enrich._apply_roi(lot, e)
    assert (e.weight_lb, e.weight_source) == (5.5, "ai")


def test_over_ten_pounds_is_never_a_gold_mine(monkeypatch):
    monkeypatch.setattr(enrich.mailer, "fits", lambda *a, **k: True)   # the weight gate alone
    lot, e = _lot("Drive Medical Cruiser III Folding Wheelchair")
    enrich._apply_roi(lot, e)
    assert e.roi_status == "PASS"
    assert e.roi_reason == "about 40 lb - over your 10 lb limit"


def test_ten_pounds_on_the_nose_is_fine():
    lot, e = _lot("Emerson NR303TT Wooden Record Player & Radio")   # 10 lb
    enrich._apply_roi(lot, e)
    assert e.weight_lb == 10
    assert "lb limit" not in (e.roi_reason or "")


def test_an_ai_weight_decides_it(monkeypatch):
    monkeypatch.setattr(enrich.mailer, "fits", lambda *a, **k: True)   # the weight gate alone
    lot, e = _lot("Coleman Green Metal Fuel Lantern")              # ~4 lb guess
    enrich._take_ai_weight(e, 12)
    enrich._apply_roi(lot, e)
    assert e.roi_reason == "12 lb - over your 10 lb limit"
