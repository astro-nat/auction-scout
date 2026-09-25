"""A container named with its contents is a pile. Pure.

"Zippered CD Storage Case With Music CDs" came back at $8 - priced as an
empty organiser, because none of the pile words (lot, bundle, assorted,
box of) appear in the title. The case was full of discs. Requiring the
contents to be plural is what keeps a guitar case with a strap out.
"""

from app.workers.enrich import looks_multi_item

PILES = [
    "Zippered CD Storage Case With Music CDs",
    "Case Full Of CDs",
    "Toolbox with Tools",
    "Binder of Baseball Cards",
    "Tote filled with Christmas Decorations",
    "Bag of Clothes",
    "Album of Stamps",
    "Drawer with assorted Screwdrivers",
    "Cabinet containing Dishes",
    "Briefcase w/ Documents",
    "Storage Bin Full of Toys",
]

SINGLES = [
    "Guitar Case with Strap",
    "Pelican Case with Foam",
    "Hard Case With Wheels",
    "Rolling Suitcase with Handles",
    "Camera Bag with Shoulder Strap",
    "Jewelry Box with Mirror",
    "Case of Motor Oil",
    "Dell Precision 7750 Laptop",
    "Zippered Case with Zippers",
    "Kodak Carousel Slide Tray",
]


def test_a_container_named_with_its_contents_is_a_pile():
    for title in PILES:
        assert looks_multi_item(title), title


def test_a_container_with_one_of_its_own_parts_is_not():
    for title in SINGLES:
        assert not looks_multi_item(title), title


def test_the_original_pile_words_still_work():
    for title in ("Lot of 10 CDs", "Box of assorted tools",
                  "Miscellaneous Kitchen Items", "Bundle of Cables"):
        assert looks_multi_item(title), title


def test_an_empty_title_is_not_a_pile():
    assert not looks_multi_item("")
    assert not looks_multi_item(None)
