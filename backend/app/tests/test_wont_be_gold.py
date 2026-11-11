"""Lots skipped before pricing because they can never be gold. Pure."""

import pytest

from app.services import mailer

SIZE = (14.5, 20.0)


@pytest.mark.parametrize("title,description,tier,reason", [
    # Sure: the words, the size, the ship tier, or a known heavy item.
    ("Ashley Furniture Leather Sofa", "", "HARD", "about 100 lb - over your 10 lb limit"),
    ("Framed Casablanca French Movie Poster", "", "NEUTRAL", "won't fit your 14.5 x 20 in mailer"),
    ("Crayola Kids Double-Sided Standing Art Easel",
     "-Dimensions: Approximately 44\" H. -Material: wood", "NEUTRAL",
     "won't fit your 14.5 x 20 in mailer"),
    ("Pampered Chef 1-Quart Enameled Cast Iron Baker", "", "NEUTRAL",
     "won't fit your 14.5 x 20 in mailer"),
    ("Drive Medical Cruiser III Folding Wheelchair", "", "HARD", "about 40 lb - over your 10 lb limit"),
    # Not sure: an unrecognised title could still turn out small.
    ("Vtg 50's Sears Bark Edge Trinket Box", "", "NEUTRAL", None),
    ("Mystery Item", "", "NEUTRAL", None),
    # Fits.
    ("Signed Kenneth Lane 36\" Faceted Necklace", "", "NEUTRAL", None),
    ("Reebok Dwyane Wade Miami Heat #3 Jersey 3XL", "", "NEUTRAL", None),
    ("Swarovski Kris Bear Lucky Charm Figurine", "", "NEUTRAL", None),
])
def test_wont_be_gold(title, description, tier, reason):
    assert mailer.wont_be_gold(title, description, tier, SIZE) == reason
