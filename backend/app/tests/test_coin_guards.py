"""Comp guards for coins and currency (services/coins.py).

The flag that started it: "2012 Proof Silver Eagle Coin & Currency Set"
drew 94 comps - loose bullion eagles, single proofs, other years - and was
priced at $188 for an item the flagger put near $150.
"""

import pytest

from app.services import coins

EAGLE_SET = "2012 Proof Silver Eagle Coin & Currency Set"


@pytest.mark.parametrize("title, expected", [
    (EAGLE_SET, True),
    ("1921-S Morgan Silver Dollar", True),
    ("1957 $1 Silver Certificate Star Note", True),
    ("Lot of 10 Wheat Pennies 1940s", True),
    ("1 oz Silver Bullion Round", True),
    ("Vintage leather coin purse", False),
    ("Coin operated arcade game", False),
    ("Coin purse with 1964 Kennedy half dollar inside", True),
    ("Quarter zip pullover", False),
    ("Dime store novelty lamp", False),
])
def test_what_counts_as_a_coin(title, expected):
    assert coins.is_coin(title) is expected


def test_the_eagle_set_rejects_what_priced_it_wrong():
    assert coins.comp_fits(EAGLE_SET, "2012 W Proof Silver Eagle Coin & Currency Set OGP")
    for wrong in ("2012 American Silver Eagle 1 oz BU",            # bullion, not proof
                  "2012-W Proof American Silver Eagle OGP",        # single, not a set
                  "2011 Proof Silver Eagle Coin & Currency Set",   # another year
                  "2012 Proof Silver Eagle Set PCGS PR70"):       # slabbed
        assert not coins.comp_fits(EAGLE_SET, wrong), wrong


def test_a_comp_silent_on_year_still_counts():
    assert coins.comp_fits("1921 Morgan Silver Dollar", "Morgan Silver Dollar circulated")


def test_mint_marks_must_agree_when_both_name_one():
    lot = "1921-S Morgan Silver Dollar"
    assert not coins.comp_fits(lot, "1921-D Morgan Silver Dollar")
    assert coins.comp_fits(lot, "1921 S Morgan Dollar")
    assert coins.comp_fits(lot, "1921 Morgan Dollar")          # silent on the mark


def test_proof_goes_both_ways():
    assert not coins.comp_fits("1964 Kennedy Half Dollar", "1964 Kennedy Half Proof")
    assert not coins.comp_fits("1964 Proof Kennedy Half", "1964 Kennedy Half BU")


def test_grading():
    assert not coins.comp_fits("1881-S Morgan Dollar", "1881-S Morgan Dollar NGC MS65")
    assert not coins.comp_fits("1881-S Morgan Dollar NGC MS65", "1881-S Morgan PCGS MS63")
    assert coins.comp_fits("1881-S Morgan Dollar NGC MS65", "1881-S Morgan PCGS MS65")
    assert coins.comp_fits("1881-S Morgan Dollar NGC MS65", "1881-S Morgan Dollar")


def test_set_versus_single():
    assert not coins.comp_fits("1999 US Mint Set", "1999 Lincoln Cent")
    assert not coins.comp_fits("1999 Lincoln Cent", "1999 US Mint Set")
    assert coins.comp_fits("Lot of 10 Wheat Pennies", "Lot of 50 wheat pennies")


def test_bullion_weight():
    assert not coins.comp_fits("2020 Gold Eagle 1/10 oz", "2020 Gold Eagle 1 oz")
    assert coins.comp_fits("2020 Gold Eagle 1/10 oz", "2020 1/10 oz Gold American Eagle BU")


def test_note_types():
    lot = "1957 $1 Silver Certificate"
    assert not coins.comp_fits(lot, "1957 $1 Federal Reserve Note")
    assert coins.comp_fits(lot, "1957 One Dollar Silver Certificate Blue Seal")


def test_everything_else_passes_untouched():
    assert coins.comp_fits("Pyrex Butterprint mixing bowl", "Pyrex 1964 bowl proof set")


@pytest.mark.parametrize("title", ["1999 Lincoln Cent", "1965 Washington Quarter",
                                   "1972 Eisenhower Dollar", "2000-P Sacagawea Dollar"])
def test_circulating_series_count_as_coins(title):
    assert coins.is_coin(title)


@pytest.mark.parametrize("title", ["Penny loafers size 9", "Quarter zip fleece"])
def test_everyday_words_do_not(title):
    assert not coins.is_coin(title)


@pytest.mark.parametrize("title", [
    # Real production titles the first version wrongly took for coins.
    "Loungefly Pooh Cosmetic Bag and Coin Bag Bundle",
    "Loungefly Minnie Daisy Hat Crossbody bag W/coin pouch",
    'Vintage 1983 Enesco Morgan Inc "I Love My Teacher" Mouse Figurine',
    "LeBron James Gold Banknote Foil Card Set & COA",
    "Lot of 46 Assorted Clear Vinyl Coin Sleeves & Challenge Coin",
])
def test_production_false_positives_are_not_coins(title):
    assert not coins.is_coin(title)
