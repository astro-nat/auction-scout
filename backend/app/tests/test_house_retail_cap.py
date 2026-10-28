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
    ("-Estimated Retail Price: $15.00 – $25.00. -This lot features", 15.0),
    ("-Estimated Retail Value: Approximately $15 to $25. -This lot", 15.0),
    ("Estimated retail value ranges from $25 to $50 compared in the market.", 25.0),
    ("-Estimated Retail Price: $800 – $1,500 USD. -This lot", 800.0),
    ("Estimated Retail Price: $40.00 – $50.00 USD.", 40.0),
    ("-Estimated Retail Price: Comparable value around $45.00.", 45.0),
    ("*Estimated Retail Value: $20–$40 *This lot", 20.0),
    ("-Estimated Retail Price: $30 - $60 (for the lot).", 30.0),
    ("This lot features a lantern. Retail was high.", None),
    ("", None),
    (None, None),
])
def test_reads_the_bottom_of_the_estimate(text, value):
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
    assert float(e.est_resale) == 15.0
    assert "capped at house retail $15 (comps said $39.92)" in e.price_source
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


def test_a_recap_keeps_what_the_comps_first_said():
    """Lots capped at the old high-end rule carry the high end as their value;
    moving to the low end must not report that as the comps figure."""
    lot, e = _lot("-Estimated Retail Price: $15.00 – $25.00.")
    e.est_resale = 25.0
    e.price_source = "sold (SoldComps) · capped at house retail $25 (comps said $39.92)"
    enrich._apply_roi(lot, e)
    assert float(e.est_resale) == 15.0
    assert e.price_source == "sold (SoldComps) · capped at house retail $15 (comps said $39.92)"


_220J = [{"title": "Vintage Coleman Double Mantle Lantern 220J195 w/Box", "price": 40},
         {"title": "Vintage Coleman Green Double Mantle Camping Lantern Model 220J", "price": 42},
         {"title": "Coleman 228H Green Gas Lantern", "price": 30}]


@pytest.mark.parametrize("title,comps,close", [
    ("Vtg Coleman Model 220J Double Mantle Lantern", _220J, True),
    # Fewer than half the comps name the model.
    ("Vtg Coleman Model 220J Double Mantle Lantern", _220J[1:] + _220J[2:] * 2, False),
    # Sizes, counts and decades are not models.
    ("4-Tier Fabric & Plastic Storage Shelving Unit",
     [{"title": "BLACK & YELLOW, 4-Tier Heavy Duty Plastic Storage Shelving Unit"}] * 3, False),
    ("Vigoro 50FT Landscape Edging", [{"title": "Vigoro 50FT Edging"}] * 3, False),
    ("Round Frame Drum", [{"title": "Meinl Frame Drum"}] * 3, False),
    ("Vtg Coleman Model 220J Double Mantle Lantern", _220J[:1], False),   # one comp proves nothing
    # An all-digit part number counts; a year does not.
    ("Greenlee 35713 6-3/8\" Recessed Light Hole Saw",
     [{"title": "Greenlee 35713 Recessed Light Hole Saw, 6-3/8-Inch"}] * 2, True),
    ("1985 Donruss Baseball Card", [{"title": "1985 Donruss Set"}] * 3, False),
])
def test_comps_name_the_model(title, comps, close):
    from app.services.pricing import comps_name_the_model
    assert comps_name_the_model(title, comps) is close


def test_comps_naming_the_model_outrank_the_house_estimate():
    lot, e = _lot("Estimated retail value ranges from $25 to $50 compared in the market.")
    lot.title = "Vtg Coleman Model 220J Double Mantle Lantern"
    e.comps = _220J
    e.est_resale = 39.88
    enrich._apply_roi(lot, e)
    assert float(e.est_resale) == 39.88
    assert "capped" not in e.price_source


def test_a_lot_capped_before_gets_its_comps_back_when_they_name_the_model():
    lot, e = _lot("Estimated retail value ranges from $25 to $50 compared in the market.")
    lot.title = "Vtg Coleman Model 220J Double Mantle Lantern"
    e.comps = _220J
    e.est_resale = 25.0
    e.price_source = "sold (SoldComps) · capped at house retail $25 (comps said $39.88)"
    enrich._apply_roi(lot, e)
    assert float(e.est_resale) == 39.88
    assert e.price_source == "sold (SoldComps)"
