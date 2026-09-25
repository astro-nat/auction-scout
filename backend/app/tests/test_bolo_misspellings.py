"""Misspelling aliases for the sourcing-sheet BOLO entries.

Aliases are exact whole-word patterns; there is no fuzzy matching. A typo
in a lot title therefore means no match at all, and auction listings are
typed fast by people reading a label from across a warehouse, so the typos
are predictable.

The second half of this file is the part that matters more: a misspelling
is often somebody's correct spelling. "kurta" is a garment, "technic" is
LEGO. Those are deliberately absent, and these tests pin that.
"""

import pytest

from app.services.bolo import BoloMatcher

matcher = BoloMatcher()


def _match(title):
    return matcher.match(title, "")


@pytest.mark.parametrize("title,expected", [
    ("Sony Walk Man Cassette Player Vintage", "Sony Walkman"),
    ("Sony Trinatron 20in Color TV Monitor", "Sony Trinitron CRT"),
    ("Hewlitt Packard HP35 Calculator Red LED", "HP scientific calculators"),
    ("Texas Instrument Calculator Speak N Spell", "Texas Instruments calculators"),
    ("Casio Data Bank Watch Vintage", "Calculator watches"),
    ("PalmPilot Personal Digital Assistant", "Personal organizers and PDAs"),
    ("Roland Juno106 Vintage Synth", "Vintage synthesizers"),
    ("Logitec Harmony Remote Controll", "Universal and OEM remote controls"),
    ("GE Spacemaster Under Cabinet Radio", "GE Spacemaker under-cabinet appliances"),
    ("Starret Machinist Square 6 inch", "Premium hand tools"),
    ("Stanley Baily No 5 Hand Plane", "Premium hand tools"),
    ("Vaccum Attachment Set Hose Tools", "Small replacement parts"),
    ("Play Station 5 Console Bundle", "Modern gaming consoles"),
    ("Nintendo Swich OLED Console", "Modern gaming consoles"),
    ("Magic The Gathring Card Lot Vintage", "Magic: The Gathering (vintage)"),
    ("Lenovo Think Pad X1 Carbon", "Business and premium laptops"),
    ("Dell Lattitude 7420 Laptop", "Business and premium laptops"),
    ("Creed Aventis 100ml Mens Cologne", "Niche and luxury fragrances"),
    ("Chiltons Manual Chevy Silverado", "Specialty books and manuals"),
    ("Macintosh Amplifier MC240 Tube", "Vintage hi-fi separates"),
    ("Maranz Stereo Reciever Vintage Silver", "Vintage hi-fi separates"),
])
def test_typo_titles_still_match(title, expected):
    m = _match(title)
    assert m is not None, f"typo title matched nothing: {title!r}"
    assert m["brand"] == expected, f"{title!r} matched {m['brand']!r}"


def test_addidas_is_the_most_common_typo_in_the_catalogue():
    m = _match("Addidas Samba OG Mens Size 11")
    assert m is not None
    assert "Sneakers" in m["brand"]


@pytest.mark.parametrize("title", [
    # A kurta is a garment. Curta must not claim it.
    "Cotton Kurta Tunic Womens Large",
    # Technic is LEGO, not Technics.
    "LEGO Technic Excavator Set 42121",
    # Macintosh the computer, not McIntosh the amplifier. Only the
    # qualified forms (macintosh amplifier/receiver/tuner) are aliases.
    "Apple Macintosh SE30 Vintage Computer",
])
def test_real_words_are_not_treated_as_typos(title):
    """The risk of adding misspellings is that somebody else spells their
    product that way on purpose."""
    m = _match(title)
    if m is not None:
        assert m["brand"] not in {
            "Curta mechanical calculator",
            "Vintage hi-fi separates",
            "Premium hand tools",
        }, f"{title!r} was claimed by {m['brand']!r}"


def test_a_macintosh_computer_does_not_become_an_amplifier():
    """The single riskiest alias in the batch: McIntosh audio and Apple
    Macintosh differ by two letters and both appear in estate lots."""
    m = _match("Apple Macintosh Classic II Computer")
    if m is not None:
        assert m["brand"] != "Vintage hi-fi separates"


def test_the_correct_spellings_still_work():
    """Misspellings are additive; they must not disturb the originals."""
    for title, brand in [
        ("McIntosh MC275 Tube Amplifier", "Vintage hi-fi separates"),
        ("Starrett 12 inch Combination Square", "Premium hand tools"),
        ("Sony Walkman WM-DC2", "Sony Walkman"),
        ("Adidas Samba OG White", None),
    ]:
        m = _match(title)
        assert m is not None, f"regression: {title!r} no longer matches"
        if brand:
            assert m["brand"] == brand
