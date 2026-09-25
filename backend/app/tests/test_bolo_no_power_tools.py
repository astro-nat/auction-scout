"""Power tools are off the BOLO list. Pure.

Heavy, awkward to ship, and worth less per pound than almost anything else
in an estate lot - the same reason the Dewalt and Milwaukee entries have
always been batteries only. Makita and Festool are now gone with them.

Hand tools stay: a machinist square, a bench plane and a hammer are light,
flat and hold their value.
"""

from app.services import bolo
from app.services.bolo import BoloMatcher

matcher = BoloMatcher()


def _brand(title, description=""):
    m = matcher.match(title, description)
    return m["brand"] if m else None


POWER = [
    "Makita XPH12Z Hammer Driver Drill",
    "Mikita Cordless Drill 18v",
    "Festool TS 55 Track Saw with Rail",
    "Dewalt DCS570 Circular Saw",
    "Milwaukee M18 Fuel Impact Wrench",
    "Ryobi 18V Cordless Sander",
]

HAND = [
    ("Starrett 12 inch Combination Square", "Premium hand tools"),
    ("Starret Machinist Square 6 inch", "Premium hand tools"),
    ("Stanley Bailey No. 4 Hand Plane Type 11", "Premium hand tools"),
    ("Lie-Nielsen No. 4 Smoothing Plane", "Premium hand tools"),
    ("Estwing 16oz Claw Hammer", "Premium hand tools"),
]


def test_no_power_tool_is_flagged():
    for title in POWER:
        assert _brand(title) is None, f"{title!r} still matched"


def test_hand_tools_are_still_flagged():
    for title, brand in HAND:
        assert _brand(title) == brand, f"{title!r} -> {_brand(title)!r}"


def test_the_category_no_longer_names_power_tools():
    assert "Premium hand and power tools" not in bolo._BRAND_ALIASES
    assert "Premium hand tools" in bolo._BRAND_ALIASES
    joined = " ".join(bolo._BRAND_ALIASES["Premium hand tools"])
    for alias in ("makita", "festool", "mikita", "fes tool"):
        assert alias not in joined


def test_the_batteries_are_still_wanted():
    """A battery is not a power tool - light, and it is the expensive part."""
    assert _brand("Dewalt DCB205 20V Max Battery 5Ah") == "Dewalt batteries (OEM only)"
    assert _brand("Milwaukee M18 Battery 5.0") == "Milwaukee batteries (OEM only)"


def test_a_box_lot_of_power_tools_is_no_longer_worth_opening():
    """category_hint is what makes an unbranded pile worth a look under the
    BOLO-only filter. Both piles reduce to the token "tools", so dropping
    that token would have lost the planes with the drills - the power-tool
    wording is refused by name instead."""
    for title in ("Lot of Assorted Power Tools", "Box of Cordless Tools",
                  "Large Power Tool Lot"):
        assert bolo.category_hint(title) is None, title
    assert bolo.category_hint("Lot of Assorted Hand Tools")
    assert bolo.category_hint("Box of Machinist Tools")
