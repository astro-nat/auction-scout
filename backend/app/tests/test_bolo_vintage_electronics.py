"""Vintage-electronics BOLO entries, added from the sourcing research sheet.

Two things need proving. The obvious one: these titles match at all. The
less obvious one: they do not steal lots from the files that were already
handling them. Half these brands are words that appear all over an auction
catalogue - Sony makes televisions and headphones, Casio makes G-Shocks,
Nintendo makes consoles - so every alias here is deliberately qualified
with a model number or a disambiguating word.
"""

import pytest

from app.services.bolo import BoloMatcher

matcher = BoloMatcher()


def _match(title, description=""):
    return matcher.match(title, description)


@pytest.mark.parametrize("title,expected_brand", [
    ("Sony Walkman WM-DC2 Portable Cassette Player", "Sony Walkman"),
    ("Vintage Sony TPS-L2 Walkman Blue Silver", "Sony Walkman"),
    ("Sony PVM-20L5 Trinitron Broadcast Monitor", "Sony Trinitron CRT"),
    ("HP 15C Scientific Calculator with Case", "HP scientific calculators"),
    ("Hewlett Packard HP-41CX Calculator", "HP scientific calculators"),
    ("Texas Instruments Speak and Spell 1978", "Texas Instruments calculators"),
    ("TI-84 Plus CE Graphing Calculator Teal", "Texas Instruments calculators"),
    ("Curta Type II Mechanical Calculator with Canister", "Curta mechanical calculator"),
    ("HP-01 Calculator Watch Stainless Steel", "Calculator watches"),
    ("Sharp Wizard OZ-7000 Electronic Organizer", "Personal organizers and PDAs"),
    ("Roland Juno-106 Analog Synthesizer", "Vintage synthesizers"),
    ("Roland Jupiter 6 Synthesizer Vintage", "Vintage synthesizers"),
    ("Logitech Harmony Elite Remote with Hub", "Universal and OEM remote controls"),
    ("GE Spacemaker Under Cabinet Can Opener Almond", "GE Spacemaker under-cabinet appliances"),
    ("ChargePoint Home Flex Level 2 EV Charger", "EV charging equipment"),
    ("Tesla Mobile Connector NEMA 14-50 Adapter", "EV charging equipment"),
])
def test_sheet_items_are_recognised(title, expected_brand):
    m = _match(title)
    assert m is not None, f"no BOLO match for {title!r}"
    assert m["brand"] == expected_brand, f"{title!r} matched {m['brand']!r}"


@pytest.mark.parametrize("title", [
    # Bare brand words that must NOT drag a lot into this file.
    "Sony Bravia 55 inch 4K Smart TV",
    "Sony WH-1000XM4 Noise Cancelling Headphones",
    "HP OfficeJet Pro 8600 Printer",
    "HP Pavilion Laptop 15 inch",
    "Casio G-Shock DW-5600 Watch",
    "Nintendo Switch OLED Console",
    "Sharp Microwave Oven Stainless",
])
def test_generic_brand_words_do_not_land_here(title):
    """A bare maker name is not evidence. These belong to other files, or
    to nothing at all."""
    m = _match(title)
    if m is not None:
        assert m.get("bolo_category") != "vintage_electronics", (
            f"{title!r} was pulled into vintage electronics as {m['brand']!r}")


def test_calculator_watch_does_not_outrank_a_real_watch():
    """audio_watches loads first for exactly this reason."""
    m = _match("Seiko Automatic Diver Watch SKX007")
    if m is not None:
        assert m["brand"] != "Calculator watches"


def test_a_databank_still_reaches_the_calculator_entry():
    """The specific model is what earns the routing, not the maker."""
    m = _match("Casio Databank Calculator Watch CA-53W")
    assert m is not None
    assert m["brand"] == "Calculator watches"


def test_console_keywords_stay_with_the_games_file():
    m = _match("Nintendo GameCube Console with Controllers")
    if m is not None:
        assert m["brand"] not in {"Calculator watches", "Personal organizers and PDAs"}


def test_heavy_entries_carry_a_shipping_warning():
    """CRTs and full-size synths are the two things in this file that the
    weight-to-value logic does not favour."""
    crt = _match("Sony PVM-20L5 Trinitron Broadcast Monitor")
    synth = _match("Roland Jupiter 6 Synthesizer Vintage")
    assert crt["ship_class"] == "freight"
    assert synth["ship_class"] == "large_box"


def test_the_light_high_value_entries_are_tier_one():
    """The whole point of the sheet: sub-1lb items with four-figure ceilings."""
    for title in ("HP 15C Scientific Calculator with Case",
                  "Curta Type II Mechanical Calculator with Canister",
                  "Sony Walkman WM-D6C Professional"):
        m = _match(title)
        assert m["tier"] == 1, f"{title!r} came back tier {m['tier']}"
