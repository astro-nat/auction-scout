"""Gap-fill entries added from the sourcing research sheet.

These went into the files that already owned their domain rather than into
a new one, so the risk is different from a fresh file: an alias added
mid-chain can steal lots from entries that were matching fine before.
Several of these words are already spoken for elsewhere in the catalogue -
"Stanley" is a drinks tumbler, "Pioneer" is a vintage audio parts entry,
"Surface" is an ordinary noun - so every pattern is qualified.
"""

import pytest

from app.services.bolo import BoloMatcher

matcher = BoloMatcher()


def _match(title, description=""):
    return matcher.match(title, description)


@pytest.mark.parametrize("title,expected", [
    ("McIntosh MC275 Tube Amplifier", "Vintage hi-fi separates"),
    ("Technics SL-1200 MK2 Turntable", "Vintage hi-fi separates"),
    ("Starrett 12 inch Combination Square", "Premium hand tools"),
    ("Stanley Bailey No. 4 Hand Plane Type 11", "Premium hand tools"),
    ("Vacuum Hose Attachment Crevice Tool Set", "Small replacement parts"),
    ("Presser Foot and Bobbin Case Assortment", "Small replacement parts"),
    ("Sony PS5 Console Disc Edition", "Modern gaming consoles"),
    ("Xbox Series X 1TB Console", "Modern gaming consoles"),
    ("Nintendo Switch OLED White Joy-Con", "Modern gaming consoles"),
    ("Magic The Gathering Revised Dual Land", "Magic: The Gathering (vintage)"),
    ("MTG Beta Booster Pack Sealed", "Magic: The Gathering (vintage)"),
    ("Lenovo ThinkPad X1 Carbon Gen 9 i7", "Business and premium laptops"),
    ("Microsoft Surface Pro 7 128GB", "Business and premium laptops"),
    ("Creed Aventus 100ml Eau de Parfum", "Niche and luxury fragrances"),
    ("Chilton Manual Ford F-150 1997-2003", "Specialty books and manuals"),
    ("Machinery's Handbook 29th Edition", "Specialty books and manuals"),
])
def test_gap_items_now_match(title, expected):
    m = _match(title)
    assert m is not None, f"no BOLO match for {title!r}"
    assert m["brand"] == expected, f"{title!r} matched {m['brand']!r}"


def test_adidas_sneakers_were_missing_entirely():
    """The sheet lists Adidas beside Nike, Jordan and New Balance, but only
    Yeezy was covered."""
    for title in ("Adidas Samba OG White Black Size 10",
                  "Adidas Gazelle Indoor Suede"):
        m = _match(title)
        assert m is not None, f"no match for {title!r}"
        assert "Sneakers" in m["brand"]


@pytest.mark.parametrize("title,forbidden", [
    # Stanley is a drinks tumbler in lightweight_collectibles.
    ("Stanley 40oz Quencher Tumbler Pink", "Premium hand tools"),
    # Pioneer alone belongs to the vintage audio parts entry.
    ("Pioneer Car Stereo Head Unit", "Vintage hi-fi separates"),
    # Retro consoles must stay with the retro entry.
    ("Nintendo 64 Console with Controller", "Modern gaming consoles"),
    # A surface is a noun.
    ("Granite Surface Plate Inspection Grade", "Business and premium laptops"),
])
def test_qualified_aliases_do_not_steal_neighbouring_lots(title, forbidden):
    m = _match(title)
    if m is not None:
        assert m["brand"] != forbidden, f"{title!r} was taken by {forbidden!r}"


def test_a_branded_appliance_part_stays_with_its_brand():
    """The generic spares entry is for UNBRANDED parts. A GE knob is a GE
    Appliances part and should keep routing there."""
    m = _match("GE Range Knob Set OEM Replacement")
    assert m is not None
    assert m["brand"] == "GE Appliances"


def test_the_modern_console_entry_does_not_reach_retro_lots():
    """Retro consoles are handled (or deliberately not handled) by the
    existing entry, which only covers rare and sealed examples. Either way
    the modern entry must not pick them up."""
    for title in ("Super Nintendo SNES Console Bundle",
                  "Sega Genesis Model 1 Console"):
        m = _match(title)
        if m is not None:
            assert m["brand"] != "Modern gaming consoles"


def test_pokemon_still_routes_to_its_own_entry():
    """Adding MTG beside it must not disturb the existing TCG entries."""
    m = _match("Pokemon Base Set Shadowless Booster Pack")
    assert m is not None
    assert "Pokemon" in m["brand"]


def test_hifi_separates_outrank_the_generic_audio_parts_entry():
    """vintage_electronics loads before household_parts, so a McIntosh
    amplifier gets the four-figure read, not the parts-bin one."""
    m = _match("McIntosh MA6900 Integrated Amplifier")
    assert m["brand"] == "Vintage hi-fi separates"
    assert m["tier"] == 1


def test_media_mail_note_survives_on_books():
    """The only category in the catalogue with a shipping advantage."""
    m = _match("Chilton Manual Ford F-150 1997-2003")
    assert m["ship_class"] == "small_box"
