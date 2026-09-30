"""The recorded-media BOLO entries. Pure.

Scoped to labels, formats and pressing markers - never the bare words.
"cd" and "dvd" appear in a large share of the inventory; measured, a broad
bulk-lot entry would have badged 394 lots, 9% of everything on file, which
makes the badge mean nothing. Bulk media is the per-item price filter's job.
"""

import pytest

from app.services.bolo import BoloMatcher

m = BoloMatcher()


def _brand(title):
    return (m.match(title, "") or {}).get("brand")


@pytest.mark.parametrize("title,brand", [
    ("Criterion Collection Seven Samurai Blu-ray", "Criterion Collection"),
    ("Funimation Fullmetal Alchemist Complete Series Box Set", "Out-of-print anime on disc"),
    ("Vintage Dragon Ball Z VHS Lot of 10 Funimation DBZ Anime Tapes", "Out-of-print anime on disc"),
    ("Scream Factory Halloween II Blu-ray slipcover", "Boutique restoration labels"),
    ("Vinegar Syndrome Blu-ray limited slipcover", "Boutique restoration labels"),
    ("Disney Black Diamond VHS Lot of 5 - Mermaid Beauty Beast Bambi", "Discontinued Disney video"),
    ("Blue Note Records Miles Davis first pressing LP", "Collectible vinyl records"),
    ("Garth Brooks The Ultimate Collection 10 CD Box Set", "CDs with real scarcity"),
    ("Akira laserdisc Criterion", "Laserdisc (collector titles)"),
])
def test_the_labels_and_formats_that_resell(title, brand):
    assert _brand(title) == brand


@pytest.mark.parametrize("title", [
    "Lot of 16 music cds",
    "Bulk Lot of 9 Movies DVD & Blu Ray Mix",
    "How to train your dragon dvd",
    "Camila Cabello CD",
    # "Original Pressing" on a CD is marketing copy, not a vinyl first press
    "Madonna - Ray of Light CD Album Original Pressing",
])
def test_ordinary_media_is_not_badged(title):
    """A badge on every disc is a badge on nothing."""
    assert _brand(title) is None


def test_a_game_disc_keeps_its_own_entry():
    """Media loads after the games list, so a rare PS1 title stays a game."""
    b = _brand("Vintage PlayStation 1 Suikoden II complete CIB")
    assert b is None or "game" in b.lower()


def test_pyrex_is_not_disturbed():
    assert _brand("Vintage Pyrex Butterfly Gold Bowl") == "Pyrex vintage"
