"""Telling a bargain from bait. Pure.

Every listing here is real. The bait rows are what the rule selects out of
1,702 priced Vinted listings; the bargain rows are the deepest discounts in
that same set, which are simply cheap.
"""

import pytest

from app.services.too_good import implausible, note

# (ask, comps value) — all 12 the rule selects
BAIT = [
    (15.00, 224.97),    # "Zotac graphics card, gpu."
    (232.00, 2010.50),  # "macbook pro m5 14-inch 24gb 1tb"
    (83.00, 669.99),    # "2021 Macbook Pro M1 Pro"
    (99.86, 600.00),    # "Sapphire Pulse AMD Radeon RX 7900 XT"
    (80.00, 475.00),    # "MSI Claw 8Ai+ Handheld"
    (213.00, 1200.00),  # "Apple Mac Studio 2022"
    (93.00, 475.00),    # "Xbox Series X 1TB SSD Console"
    (94.00, 464.99),    # "AMD Radeon RX 9060 XT"
    (98.56, 487.50),    # "Rx 6950xt red devil"
    (94.00, 439.99),    # "Sapphire Pulse Radeon RX 9060 XT"
    (96.00, 420.00),    # "Legion Go S 120hz gaming handheld"
    (65.00, 280.00),    # "2 x 4TB SSDs + 7 FREE Electronics"
]

# The deepest discounts in the inventory. Cheap, not bait: a dollar is a
# dollar whatever the comps say a used DVD bundle is worth.
BARGAINS = [
    (1.00, 24.99),   # "4 DvDs in 1"
    (1.25, 29.74),   # "Pure 70s"
    (1.00, 22.95),   # "Lot of 6 CDs"
    (1.00, 21.31),   # "Vintage Disney Black Diamond VHS Lot"
    (1.25, 23.50),   # "3 DVD Movie Lot"
    (1.50, 23.96),   # "Kids DVD Bundle"
    (2.00, 27.99),   # "Random 4 VHS tapes lot"
    (1.00, 12.80),   # "Sticker Fashionista"
]


@pytest.mark.parametrize("ask,value", BAIT)
def test_every_bait_listing_is_caught(ask, value):
    assert implausible(ask, value)


@pytest.mark.parametrize("ask,value", BARGAINS)
def test_a_cheap_listing_is_not_bait(ask, value):
    assert not implausible(ask, value)


def test_an_ordinary_good_deal_is_left_alone():
    """Half price on a $300 item is the app doing its job."""
    assert not implausible(150, 300)
    assert not implausible(90, 300)     # 30%, over the ratio line
    assert not implausible(20, 100)     # 20%, but only $80 at stake


def test_the_thresholds_are_not_a_knife_edge():
    """The same 12 listings come out at 30%/$100, 25%/$150 and 35%/$200,
    because the gap between deals and bait is wide. These two rows sit on
    either side of it."""
    assert implausible(65, 280)         # the SSD bundle
    assert not implausible(65, 200)     # same ask, ordinary discount

def test_nothing_is_read_from_a_missing_number():
    for ask, value in ((None, 300), (100, None), (0, 300), (100, 0), (None, None)):
        assert not implausible(ask, value)
        assert note(ask, value) is None


def test_the_note_names_the_money():
    n = note(65, 280)
    assert "$65" in n and "$280" in n and "23%" in n
    assert "$215" in n
