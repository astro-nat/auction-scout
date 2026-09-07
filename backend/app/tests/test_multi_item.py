"""Multi-item routing — piles get the itemized vision pass, products don't."""

import pytest

from app.workers.enrich import looks_multi_item


@pytest.mark.parametrize("title", [
    "Lot of Assorted Kitchen Items",
    "Vintage Hot Rod Related Magazines Bundle",
    "Box of Old Tools",
    "Assorted Bolts, Nuts & Washers",
    "Collection of Coins",
    "Lot 8 Britains Ltd Metal Soldier Miniatures",
    "Miscellaneous Garage Items",
    "Tote of Christmas Decor",
])
def test_piles_route_to_inspect(title):
    assert looks_multi_item(title)


@pytest.mark.parametrize("title", [
    # "set" and "pair" are single sellable units, not piles
    "Vintage Mikasa Classico Satin Flatware Set",
    "Pair of Clear Lead Crystal Ice Buckets",
    "14K Gold Signet Ring",
    "Theodore Alexander Boudoir Lamp Beryl Finish",
    "Antique EIH Horsman Porcelain Face Doll",
    "Camelot Slot Machine",
    # a word merely CONTAINING a trigger must not fire (word-boundaried)
    "Lottery Ticket Display Case",
    "Pilot Precise Rolling Ball Pens",
])
def test_products_stay_on_enrich(title):
    assert not looks_multi_item(title)


def test_blank_titles_are_safe():
    assert not looks_multi_item("")
    assert not looks_multi_item(None)
