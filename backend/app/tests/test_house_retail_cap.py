"""The house's printed retail estimate caps the comps. Pure.

The #660 audit found generic lots priced off the name-brand item: plastic
shelving at $40 against metal racks while the description itself said
"Estimated Retail Price: $15.00 - $25.00".
"""

import pytest

from app import models
from app.services.pricing import retail_from_description
from app.workers import enrich


@pytest.mark.parametrize("text,value", [
    ("-Estimated Retail Price: $15.00 – $25.00. -This lot features", 25.0),
    ("-Estimated Retail Value: Approximately $15 to $25. -This lot", 25.0),
    ("Estimated retail value ranges from $25 to $50 compared in the market.", 50.0),
    ("-Estimated Retail Price: $800 – $1,500 USD. -This lot", 1500.0),
    ("Estimated Retail Price: $40.00 – $50.00 USD.", 50.0),
    ("-Estimated Retail Price: Comparable value around $45.00.", 45.0),
    ("*Estimated Retail Value: $20–$40 *This lot", 40.0),
    ("-Estimated Retail Price: $30 - $60 (for the lot).", 60.0),
    ("This lot features a lantern. Retail was high.", None),
    ("", None),
    (None, None),
])
def test_reads_the_top_of_the_estimate(text, value):
    assert retail_from_description(text) == value


def _lot(description, overrides=()):
    lot = models.Lot(lot_id="1", title="4-Tier Storage Shelving Unit",
                     description=description, logistics_ease="EASY",
                     source="Ship", current_bid=2, next_bid=2,
                     unreachable_pickup=False,
                     auction=models.Auction(name="a", source="Ship",
                                            buyer_premium_mult=1.18))
    e = models.Enrichment(lot_id=1, user_overrides=list(overrides),
                          est_resale=39.92, comp_count=80,
                          verdict="normal wear and tear",
                          price_source="sold (SoldComps)")
    lot.enrichment = e
    return lot, e


def test_comps_above_the_house_estimate_are_capped():
    lot, e = _lot("-Estimated Retail Price: $15.00 – $25.00.")
    enrich._apply_roi(lot, e)
    assert float(e.est_resale) == 25.0
    assert "capped at house retail $25 (comps said $39.92)" in e.price_source
    enrich._apply_roi(lot, e)                  # a regrade changes nothing
    assert e.price_source.count("capped at house retail") == 1


def test_comps_under_the_estimate_are_left_alone():
    lot, e = _lot("-Estimated Retail Price: $40 – $60.")
    enrich._apply_roi(lot, e)
    assert float(e.est_resale) == 39.92
    assert "capped" not in e.price_source


def test_your_own_value_is_never_capped():
    lot, e = _lot("-Estimated Retail Price: $15.00 – $25.00.", overrides=["est_resale"])
    enrich._apply_roi(lot, e)
    assert float(e.est_resale) == 39.92
