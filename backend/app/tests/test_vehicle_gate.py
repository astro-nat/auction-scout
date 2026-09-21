"""Titled vehicles never earn the badge — no matter how right the math is.

The school bus that started this priced out at +$6,776 with the audit's
blessing; the numbers were fine, the business wasn't. Detection is
category-first, then title evidence, with toys/models/parts excluded on
either path. The gate is a setting, on by default.
"""

import pytest

from app import models
from app.services import pricing
from app.workers import enrich


# --- detection -----------------------------------------------------------

VEHICLES = [
    ("2016 Saf-T-Liner Bus", None),
    ("2013 Ford Escape SE 4WD", None),
    ("2018 Aston Martin DB11 Coupe", None),
    ("Ford Cargo Van, runs and drives", None),
    ("Utility Tractor", "Motor Pool"),
    ("Crown Victoria, 154,201 miles", None),
    ("Impala, salvage title", None),
    ("Anything at all", "Automobiles/Cars"),
    ("Anything at all", "SUV"),
    ("Yamaha Jet Ski w/ trailer", None),
]

NOT_VEHICLES = [
    ("Hot Wheels 1998 Ford Mustang Lot", None),
    ("1:24 Scale Model 2015 Chevy Camaro Diecast", None),
    ("2019 Ford F-150 Tailgate — parts only", None),
    ("Truck Bed Liner", None),
    ("RC Monster Truck w/ Remote", None),
    ("Vintage Pedal Car", None),
    ("Anything at all", "Motor Pool Parts"),
    ("Anything at all", "Heavy Equipment Parts"),
    ("Vintage Cast Iron Doorstop", "General Merchandise"),
    ("Pots & Pans", None),
    ("Sterling Silver Cuff Bracelet", "Jewelry"),
]


@pytest.mark.parametrize("title,category", VEHICLES)
def test_vehicles_are_detected(title, category):
    assert pricing.is_titled_vehicle(title, category), (title, category)


@pytest.mark.parametrize("title,category", NOT_VEHICLES)
def test_non_vehicles_are_not(title, category):
    assert not pricing.is_titled_vehicle(title, category), (title, category)


# --- the gate ------------------------------------------------------------

def _lot(title="2016 Saf-T-Liner Bus", category=None):
    lot = models.Lot(lot_id="1", title=title, category=category,
                     logistics_ease="HARD", source="Local Pickup",
                     current_bid=100, next_bid=110, unreachable_pickup=False,
                     auction=models.Auction(name="a", source="Local Pickup",
                                            buyer_premium_mult=1.15))
    e = models.Enrichment(lot_id=1, user_overrides=[], est_resale=12500,
                          comp_count=5,
                          verdict="mint condition or working perfectly",
                          price_source="sold (SoldComps)")
    lot.enrichment = e
    return lot, e


def test_a_profitable_bus_is_gated_not_golded(monkeypatch):
    monkeypatch.setattr(enrich.settings_store, "flag",
                        lambda key, default: True)
    lot, e = _lot()
    enrich._apply_roi(lot, e)
    assert e.roi_status == "PASS"
    assert e.roi_reason == "titled vehicle — excluded by your settings"
    assert float(e.max_bid) > 0        # the math stays visible, just gated


def test_the_gate_respects_the_setting(monkeypatch):
    monkeypatch.setattr(enrich.settings_store, "flag",
                        lambda key, default: False)
    lot, e = _lot()
    enrich._apply_roi(lot, e)
    assert e.roi_status == "GOLD MINE"
    assert e.roi_reason is None


def test_non_vehicles_pass_through_unchanged(monkeypatch):
    monkeypatch.setattr(enrich.settings_store, "flag",
                        lambda key, default: True)
    lot, e = _lot(title="Sterling Silver Cuff Bracelet", category="Jewelry")
    e.est_resale = 100
    lot.current_bid, lot.next_bid = 5, 6
    enrich._apply_roi(lot, e)
    assert e.roi_status == "GOLD MINE"


def test_the_gate_defaults_on():
    from app.services import settings as settings_store
    assert settings_store.EXCLUDE_VEHICLES_DEFAULT is True
    # flag() falls back to the default when no row exists.
    assert settings_store.flag("pytest-no-such-key",
                               settings_store.EXCLUDE_VEHICLES_DEFAULT) is True
