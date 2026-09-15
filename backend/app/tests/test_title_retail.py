"""Retail price printed at the front of a liquidation lot title.

Every title here is real, pulled from live Amazon returns/overstock
catalogues on HiBid.
"""

import pytest

from app.services import pricing


@pytest.mark.parametrize("title,expected", [
    ("$20 PJ Masks Catboy Halloween Costume Toddler...", 20.0),
    ("$110 Mambobaby Baby Pool Float with Sun Canopy", 110.0),
    ("New $30 Hagerty Flatware Silver Dip (16.9 fl oz)", 30.0),
    ("New $122 Suptek 3-Shelf Bracket, Tempered...", 122.0),
    ("$69 (20V) MAX Cordless 13 in. String Trimmer", 69.0),
    ("$6 Clorox Clean-Up Original Scent Cleaner with Ble", 6.0),
    ("$190 Charlotte Tilbury Magic Cream Forever Set", 190.0),
    ("$44.50 BERISSA Light Filtering Roman Shades", 44.50),
])
def test_reads_the_leading_retail_price(title, expected):
    assert pricing.retail_from_title(title) == expected


@pytest.mark.parametrize("title", [
    "Method Citron Scent Antibacterial Cleaner Liquid 2",   # no price at all
    "Get $5.00 Store Credit on shipping for order >100",    # mid-title, a promo
    "$1 Starts - No Reserve",                               # bidding language
    "12\" x 9.8\" Vintage Makeup Mirror",                   # dimensions
    "Baby Trend EZ-Lift Plus Infant Car Seat",
    "$50 Amazon Gift Card",                                 # face value, not retail
    "Lot of 3 $2 Bills Uncirculated",                       # currency
    "$1 Mystery Envelope",                                  # below the plausible floor
    "$99999 Typo Lot",                                      # above the ceiling
])
def test_ignores_dollar_signs_that_are_not_retail(title):
    assert pricing.retail_from_title(title) is None


def test_halves_the_sticker_for_resale():
    out = pricing.price_from_title("$30 Hagerty Flatware Silver Dip")
    assert out["est_resale"] == 15.0
    assert out["price_low"] < 15.0 < out["price_high"]
    assert out["comp_count"] == 1
    assert out["price_source"].startswith("retail $30")


def test_no_price_means_fall_through_to_comps():
    assert pricing.price_from_title("Baby Trend EZ-Lift Infant Car Seat") is None


def test_realization_factor_is_tunable(monkeypatch):
    monkeypatch.setattr(pricing, "MSRP_REALIZATION", 0.4)
    assert pricing.price_from_title("$100 Widget")["est_resale"] == 40.0


@pytest.mark.parametrize("title,expected", [
    ("OPENED $88 Rear Drum Brake Shoe NB-960B for GMC...", 88.0),
    ("SEALED $100 (4x2x2 FT) Raised Garden Bed...", 100.0),
    ('MISSING $178 (54"x57") VEVOR  Truck Bed Cover', 178.0),
    ("Open Box - Tested $55 Babyliss Pro Ceramix...", 55.0),
    ("OPENED $75 (Large) Calvin Klein Mens Dress...", 75.0),
])
def test_reads_through_stacked_condition_prefixes(title, expected):
    """Houses stack condition words ahead of the price: "Open Box - Tested $55"."""
    assert pricing.retail_from_title(title) == expected


def test_product_names_containing_off_are_not_bid_speak():
    """"Freeze Off" is part of the product, not a discount."""
    assert pricing.retail_from_title(
        "New $35 NEW Compound W Freeze Off Wart Remover") == 35.0


@pytest.mark.parametrize("title", [
    "$10 off your next purchase",
    "$1 starting bid",
    "$25 Gift Card to Local Diner",
])
def test_bid_speak_and_face_value_still_rejected(title):
    assert pricing.retail_from_title(title) is None


def test_thousands_separator():
    assert pricing.retail_from_title(
        "$1,200 LG 7.3 cu.ft. Smart wi-fi Enabled Gas Dryer") == 1200.0


def test_comma_value_above_the_ceiling_still_rejected():
    assert pricing.retail_from_title("$12,000 Tractor") is None
