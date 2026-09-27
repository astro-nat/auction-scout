"""Telling a burner account from a small seller. Pure.

Every row here is a real Vinted account behind a lot on file. The gold ones
are the sellers whose listings earned a GOLD MINE badge; the controls were
drawn at random from the same inventory.
"""

import pytest

from app.services.seller import note, untrusted

# (feedback_count, item_count, bought_count) — the 13 sellers behind a gold badge
GOLD_BURNERS = [
    (None, 0, 0),   # maryalexandria33w41
    (None, 1, 0),   # agesadmin9
    (None, 1, 0),   # benedicttrantowgqu0
    (None, 1, 0),   # dusatkotsch
    (None, 1, 0),   # elizabet281
    (None, 1, 0),   # balduu5
    (None, 0, 0),   # csdl321
    (None, 2, 0),   # joaquine58
    (None, 0, 0),   # rileyl263
]
GOLD_REAL = [
    (3, 12, 0),     # ziweiw6 — the SSD bundle seller; a real closet
    (9, 35, 1),     # paws414
    (14, 20, 19),   # madisona493
    (27, 51, 26),   # hondagirl1976
]
CONTROLS = [
    (None, 4, 0), (None, 27, 0), (None, 16, 1), (None, 2, 4), (None, 5, 1),
    (2, 834, 0), (10, 134, 0), (15, 107, 7), (15, 55, 27), (17, 15, 81),
    (18, 226, 12), (143, 379, 36), (208, 314, 198), (505, 442, 113),
]


@pytest.mark.parametrize("row", GOLD_BURNERS)
def test_every_burner_behind_a_gold_badge_is_caught(row):
    assert untrusted(*row), row


@pytest.mark.parametrize("row", CONTROLS)
def test_no_ordinary_seller_is_caught(row):
    assert not untrusted(*row), row


@pytest.mark.parametrize("row", GOLD_REAL)
def test_a_real_seller_keeps_their_gold(row):
    """Four of the thirteen gold sellers have genuine histories. The SSD
    bundle was a bad listing from a real person - that is a pricing
    question, not a seller one."""
    assert not untrusted(*row), row


def test_each_part_alone_is_innocent():
    assert not untrusted(0, 40, 0), "a big closet with no ratings yet"
    assert not untrusted(5, 1, 0), "one listing, but rated"
    assert not untrusted(0, 1, 6), "tiny and unrated, but has bought things"


def test_a_seller_nobody_looked_up_is_not_condemned():
    """item_count is None only when the lookup never ran or failed. Every
    auction-house lot in the database is in that state, and so is a Vinted
    seller whose stats would not load - the rule read all-None as "no
    history at all" and failed them."""
    assert not untrusted(None, None, None)
    assert note(None, None, None, None) is None


def test_the_note_says_what_was_seen():
    assert note(None, None, 1, 0) == (
        "seller has no ratings, 1 listing and has never bought anything")
    assert note(None, None, 2, 0).startswith("seller has no ratings, 2 listings")
    assert note(5.0, 27, 51, 26) is None
