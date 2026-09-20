"""Boilerplate rows are not items: no model call, no comp lookup, no number.

Two real incidents drive this: "More Lots Loading" priced at $474.19 off a
motorcycle part and a stamp album, and "Pick Up Location" hallucinated into
a $7,734 metal building. The guard's hard part is the negative space — a
"Preview Dresser" is furniture and a "Pickup Truck Bed Liner" is a truck
part, and neither may be silenced.
"""

import pytest

from app import models
from app.services import pricing
from app.workers import enrich


@pytest.mark.parametrize("title", [
    "More Lots Loading..................",
    "MORE LOTS COMING",
    "more lots being added!!!",
    "Pick Up Location",
    "Pickup Information",
    "Pick-up instructions.",
    "PREVIEW",
    "Preview Night",
    "Payment Information",
    "Shipping info",
    "Terms and Conditions",
    "Terms of Sale",
    "Do Not Bid - info lot",
    "Test Lot",
    "Removal Times",
    "Welcome to our September sale!",
    "Thank you for bidding",
    "2026 PICK UP POLICY UPDATE - PLEASE READ!!!",
    "2026 Overview of Payment Options & Policy Changes",
    "Updated Shipping Policy",
    "Bidding options and schedule",
])
def test_boilerplate_is_recognized(title):
    assert pricing.is_placeholder_title(title)


@pytest.mark.parametrize("title", [
    "Preview Dresser Mid Century Walnut",
    "Pickup Truck Bed Liner Ford F-150",
    "Payment Informational Booklet 1950s Bank",
    "Lot of 3 Loading Ramps Steel",
    "Welcome Mat Coir 30x18",
    "Test Equipment Fluke 87V Multimeter",
    "Sample Case Vintage Salesman Leather",
    "Terms Of Endearment DVD",
    "$120 Widget Pro 3000 Cordless Drill",
    "1943 Payment Options Ledger Book Antique Bank",   # trailing words = item
    "Auction Catalog 1962 Christie's Bound Volume",
    "",
    None,
])
def test_real_items_are_left_alone(title):
    assert not pricing.is_placeholder_title(title)


def test_lookup_comps_refuses_boilerplate(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("searched comps for a placeholder title")
    monkeypatch.setattr(pricing, "_soldcomps_lookup", boom)
    monkeypatch.setattr(pricing, "_active_lookup", boom)
    r = pricing.lookup_comps("More Lots Loading........")
    assert r["est_resale"] is None
    assert r["comps"] == []


class _FakeDB:
    def commit(self):
        pass


def test_enrich_skips_boilerplate_without_spending(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("spent money on a placeholder lot")
    monkeypatch.setattr(enrich, "_call_text", boom)
    monkeypatch.setattr(enrich, "_call_vision", boom)
    monkeypatch.setattr(enrich, "_download_image", boom)
    monkeypatch.setattr(pricing, "lookup_comps", boom)

    lot = models.Lot(lot_id="1", title="More Lots Loading..........",
                     logistics_ease="EASY", source="Ship",
                     current_bid=0, next_bid=1,
                     auction=models.Auction(name="a", source="Ship"))
    e = models.Enrichment(lot_id=1, user_overrides=[])
    lot.enrichment = e
    enrich._enrich(lot, e, _FakeDB())

    assert e.est_resale is None
    assert e.roi_status is None
    assert e.ai_source == "none"
    assert "placeholder" in e.price_source
