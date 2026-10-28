"""BOLO false matches from the #660 audit (2026-10-26). Pure.

Each lot matched a brand on words from its description that never named
the brand: a two-word alias assembled across a list of colors or items,
or a brand name used as an adjective, a surname or a song title.
"""

import pytest

from app.services.bolo import BoloMatcher

m = BoloMatcher()


def _brand(title, description=""):
    return (m.match(title, description) or {}).get("brand")


@pytest.mark.parametrize("title,description", [
    ("Drive Medical Folding Steel Commode Chair",
     "A Drive Medical folding steel bedside commode chair with a snap-on "
     "toilet seat, lid, commode bucket with splash guard."),
    ("ROOKIE CARD 1985 DONRUSS TERRY PENDLETON", ""),
    ("Pilot Automotive Palm Tree License Plate Frame",
     "This lot consists of (1) Pilot Automotive license plate frame "
     "featuring decorative palm tree designs."),
    ("Flex Tape, Gorilla Epoxy & WD-40 Lubricant Lot",
     "Brand: WD-40, Flex Tape, Gorilla, Camco / Blue, black, white."),
    ("Assorted Extension Cords With Cord Holders",
     "(3) extension cords in red, green, & dark green, orange, & black.\r\n"
     "-Ideal use: power distribution."),
    ("Boots Randolph Yakety Sax Monument Vinyl Record",
     "Boots Randolph Yakety Sax vinyl record. Saxophone instrumental."),
])
def test_no_false_match(title, description):
    assert _brand(title, description) is None


@pytest.mark.parametrize("title,brand", [
    ("Snap-On 3/8 Drive Ratchet", "Snap-On hand tools"),
    ("Pendleton Wool Blanket Beaver State", "Pendleton"),
    ("Palm Pilot m505 Handheld", "Personal organizers and PDAs"),
    ("Yamaha YAS-23 Alto Saxophone with Case", None),
    ("Bosch SHX863WD55N Dishwasher Drain Pump", None),
])
def test_real_items_still_match(title, brand):
    got = _brand(title)
    if brand is None:
        assert got is not None
    else:
        assert got == brand
