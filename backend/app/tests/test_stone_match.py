"""A jewelry comp has to name the lot's stone. Pure.

The 10K jade band ring at Rare Estate was priced at $599 off seven diamond,
emerald and tanzanite rings that shared six of its seven title words.
"""

import pytest

from app.services.pricing import _stone_match

JADE = "10K Yellow Gold Fine Natural Jade Band Ring"


@pytest.mark.parametrize("comp", [
    "10k Yellow Gold Natural Diamond Band Ring 1.5 Carats Size 10",
    "Natural Emerald and Diamond Band Ring in 10K Yellow Gold (Size 9.0) 1 ctw",
    "Solid 10k Yellow Gold Natural Tanzanite & Opal Womens Band Ring",
])
def test_a_different_stone_is_not_a_comp(comp):
    assert not _stone_match(JADE, comp)


@pytest.mark.parametrize("comp", [
    "10K Yellow Gold Natural Green Jade Chinese Longevity Double Happiness Ring",
    "VTG 10k yellow gold Jade Oval Thick Band Ornate Size 6 ring",
    "Vintage Jadeite Carved Bamboo 10K Ring",
])
def test_the_same_stone_is(comp):
    assert _stone_match(JADE, comp)


def test_no_stone_named_constrains_nothing():
    assert _stone_match("14K Yellow Gold Rope Chain 20 inch", "14K Gold Diamond Pendant")


def test_diamond_cut_is_a_chain_style_not_a_stone():
    assert _stone_match("14K Yellow Gold Diamond Cut Rope Chain", "14K Yellow Gold Rope Chain 22in")


def test_any_of_the_lots_stones_will_do():
    assert _stone_match("18K Ruby & Diamond Earrings", "18K Gold Diamond Stud Earrings")
    assert _stone_match("Tiger's Eye Spinner Fob 14K", "Antique 14K Tigers Eye Watch Fob")
