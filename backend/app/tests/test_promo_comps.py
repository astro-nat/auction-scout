"""Giveaways must not be priced against retail stock.

From a real lot: a Swarovski SCS renewal gift - the piece Swarovski posts
free to members who renew, in stock at a specialist dealer for GBP 18.20 -
came back at $370 from twenty genuine sold comps. The comps were real. They
were for actual Swarovski crystal eggs. Nothing in the pipeline
distinguished a retailed collectible from a members' freebie carrying the
same brand and roughly the same shape.
"""

import pytest

from app.services.pricing import _promo_match

GIVEAWAY = "Swarovski 2023 SCS Crystal Egg Gift"
RETAIL = "Swarovski Crystal Swan Figurine Large 7658"


@pytest.mark.parametrize("comp", [
    "Swarovski Crystal Egg Paperweight Golden Topaz",
    "Swarovski Crystal Egg Limited Edition",
    "Swarovski Kris Bear Figurine Retired",
])
def test_retail_comps_are_refused_for_a_giveaway(comp):
    """This is the failure: real comps for a different, retailed product."""
    assert _promo_match(GIVEAWAY, comp) is False


@pytest.mark.parametrize("comp", [
    "Swarovski SCS Member Renewal Gift 2023 Egg",
    "Swarovski Crystal Society Egg 2023",
    "Swarovski SCS Loyalty Gift Cheetah",
])
def test_other_giveaways_are_the_right_comparison(comp):
    assert _promo_match(GIVEAWAY, comp) is True


@pytest.mark.parametrize("comp", [
    "Swarovski SCS member gift swan",
    "Swarovski Swan 7658 Retired Figurine",
])
def test_a_retail_lot_is_not_constrained(comp):
    """Deliberately one-directional. A giveaway comped against retail is
    inflated tenfold; a retail item comped against a giveaway is dragged
    down, which is the safe direction to err in - so the rule only fires
    when the LOT is the giveaway."""
    assert _promo_match(RETAIL, comp) is True


@pytest.mark.parametrize("title", [
    "Coca-Cola Gift With Purchase Tumbler Set",
    "Estee Lauder GWP Cosmetic Bag",
    "Promotional Item Not For Resale Sample",
    "Hard Rock Cafe Staff Giveaway Pin",
    "Franklin Mint Club Exclusive Plate",
])
def test_the_other_giveaway_vocabularies_are_recognised(title):
    """SCS is one club among many; the pattern has to reach the general
    case or it only ever fixes Swarovski."""
    assert _promo_match(title, "retail version of the same thing") is False


@pytest.mark.parametrize("title", [
    "Vintage Gift Box Set Sealed",
    "Members Only Jacket Size L",
    "Scissor Lift Manual",
])
def test_ordinary_titles_are_not_mistaken_for_giveaways(title):
    """"Gift", "members" and words containing scs must not trip it, or the
    filter throws away good comps across the catalogue."""
    assert _promo_match(title, "anything at all") is True


def test_an_empty_comp_title_fails_a_giveaway_lot():
    """No evidence is not evidence of a match."""
    assert _promo_match(GIVEAWAY, "") is False
    assert _promo_match(GIVEAWAY, None) is False
