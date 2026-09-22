"""Second sourcing sheet: the rows the first one did not already cover.

Most of sheet 2 repeats sheet 1. Every row was run against the live matcher
before anything was written, and these are the ones that came back with no
match at all - including two that should have matched already:

  - Canon AE-1 hit nothing despite a "Canon Cameras + Lenses" entry
    existing, because those aliases are EOS and DSLR shaped
  - Polaroid instant cameras were in sheet 1 and were missed on the first
    pass entirely
"""

import pytest

from app.services.bolo import BoloMatcher

matcher = BoloMatcher()


def _match(title):
    return matcher.match(title, "")


@pytest.mark.parametrize("title,expected", [
    ("Polaroid SX-70 Land Camera Leather", "Polaroid instant cameras"),
    ("Polaroid SLR 690 Instant Camera", "Polaroid instant cameras"),
    ("Herman Miller Aeron Chair Size B", "Mid-century and designer furniture"),
    ("Eames Lounge Chair and Ottoman Rosewood", "Mid-century and designer furniture"),
    ("Broyhill Brasilia Credenza Mid Century", "Mid-century and designer furniture"),
    ("Griswold No 8 Cast Iron Skillet Large Logo", "Collectible cast iron"),
    ("Wagner Ware Sidney O Skillet 1053", "Collectible cast iron"),
    ("Hallmark Keepsake Ornament 1995 Star Trek", "Collectible holiday ornaments"),
    ("Christopher Radko Glass Ornament Blown", "Collectible holiday ornaments"),
    ("Department 56 Snow Village House", "Collectible holiday ornaments"),
    ("Murad Retinol Youth Renewal Serum", "Discontinued skincare"),
    ("Dell UltraSharp U2720Q 27in 4K Monitor", "Premium monitors"),
])
def test_sheet_two_rows_now_match(title, expected):
    m = _match(title)
    assert m is not None, f"no match for {title!r}"
    assert m["brand"] == expected, f"{title!r} matched {m['brand']!r}"


def test_canon_film_slrs_were_slipping_past_the_canon_entry():
    """A Canon entry existed, but its aliases only knew EOS and DSLR bodies.
    The AE-1 is one of the most common film cameras in any estate lot."""
    for title in ("Canon AE-1 Program 35mm Film Camera",
                  "Canon A-1 Body with FD 50mm"):
        m = _match(title)
        assert m is not None, f"{title!r} still matches nothing"
        assert m["brand"] == "Canon Cameras + Lenses"


def test_high_value_oem_remotes_are_recognised():
    """Sheet 2 calls out Oppo specifically; a Blu-ray remote regularly
    clears $100 on its own."""
    m = _match("Oppo BDP-103 Blu-Ray Remote Control")
    assert m is not None
    assert m["brand"] == "Universal and OEM remote controls"


def test_dickies_sits_with_carhartt():
    m = _match("Dickies Work Jacket Duck Canvas XL")
    assert m is not None
    assert m["brand"] == "Carhartt"


def test_boomboxes_route_to_vintage_audio():
    m = _match("Sony CFS-W500 Boombox Vintage Dual Cassette")
    assert m is not None
    assert m["brand"] == "Vintage hi-fi separates"


@pytest.mark.parametrize("title,expected", [
    ("Poloroid SX70 Instant Camera", "Polaroid instant cameras"),
    ("Griswald Cast Iron Skillet No 10", "Collectible cast iron"),
    ("Hermann Miller Aeron Office Chair", "Mid-century and designer furniture"),
    ("Neutragena Facial Moisturizer Discontinued", "Discontinued skincare"),
    ("Christoper Radko Ornament Hand Blown", "Collectible holiday ornaments"),
])
def test_sheet_two_misspellings(title, expected):
    m = _match(title)
    assert m is not None, f"typo title matched nothing: {title!r}"
    assert m["brand"] == expected


@pytest.mark.parametrize("title,forbidden", [
    # Erie is a city and a lake long before it is a skillet.
    ("Lake Erie Landscape Oil Painting Framed", "Collectible cast iron"),
    # Philosophy is a school subject; only the skincare sense is aliased.
    ("Philosophy of Mind Hardcover Textbook", "Discontinued skincare"),
    # A snow village is also a Christmas decoration generally.
    ("Herman Melville Moby Dick First Edition", "Mid-century and designer furniture"),
])
def test_common_english_words_do_not_trigger_these(title, forbidden):
    m = _match(title)
    if m is not None:
        assert m["brand"] != forbidden, f"{title!r} was claimed by {forbidden!r}"


def test_lodge_is_still_the_non_collectible_cast_iron():
    """household_parts had Lodge, which is the one cast-iron brand that is
    NOT collectible. Adding Griswold must not disturb it."""
    m = _match("Lodge 10 inch Cast Iron Skillet Pre-Seasoned")
    assert m is not None
    assert m["brand"] != "Collectible cast iron"


def test_furniture_is_flagged_as_freight():
    """The value is real but it is a local-pickup play; the ship class is
    what stops the ROI maths pretending otherwise."""
    m = _match("Eames Lounge Chair and Ottoman Rosewood")
    assert m["ship_class"] == "freight"
    assert m["tier"] == 1
