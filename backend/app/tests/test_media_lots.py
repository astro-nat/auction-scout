"""Price per item on a bulk media lot. Pure.

Every title here is a real Vinted listing from the user's own inventory.
The wrong answers matter more than the right ones: this number sorts a
cheapest-first list, so a bad count does not make a slightly wrong row, it
puts a fake bargain at the top.
"""

import pytest

from app.services.media_lots import is_media_lot, item_count, per_item


# --- counts that are really there ----------------------------------------

@pytest.mark.parametrize("title,n", [
    ("Lot of 16 music cds", 16),
    ("Lot of 16 random music cds", 16),
    ("Bulk Lot of 9 Movies DVD & Blu Ray Mix Horror Action Drama", 9),
    ("Used Boxcar Children Books Lot of 20 books. Satisfactory condition.", 20),
    ("Dvd lot of 6", 6),
    ("19 CD Lot Christian Faith/Worship", 19),
    ("189 mixed CD lot with case", 189),
    ("Lot of 4 CDs: The Doors, Bob Dylan, Duffy, Double Trouble", 4),
    ("Bob Dylan, J.J. Cale, Chris Duarte Group CD Lot - 4 Albums", 4),
    ("Lot of 10 random star trek vhs tapes", 10),
    ("David Bowie 1983 Serious Moonlight live 2 Cd set", 2),
    ("Lot of 8 hip hop CDs notorious BIG Snoop NWA Brian McKnight", 8),
])
def test_the_count_comes_off_the_title(title, n):
    assert item_count(title) == n


# --- counts that are not counts ------------------------------------------

@pytest.mark.parametrize("title", [
    "Billboard Top Hits 1989 CD Richard Marx Bangles Tears For Fears",
    "Wrangler Cowboy Christmas Vol VI & VII cd Lot (1998/1999)",
    "Shania Twain The Woman In Me CD Album Mercury 1995 Country",
    "CD Daydream by Mariah Carey, a 1995 release",
])
def test_a_year_is_not_a_quantity(title):
    """Both of the first two sorted to the very top of a cheapest-per-item
    list at a fifth of a cent, as 1,989 and 1,998 CDs for four dollars."""
    n = item_count(title)
    assert n is None or n < 500, f"read a date as a count: {n}"
    assert per_item(4.00, title) is None or per_item(4.00, title) > 0.01


@pytest.mark.parametrize("title", [
    "Vinyl Lot",
    "How to train your dragon dvd",
    "Camila Cabello CD",
    "Huge DVD lot",
])
def test_no_stated_quantity_means_no_answer(title):
    """"Huge" is not a number. Guessing one prices the lot off a made-up
    denominator."""
    assert item_count(title) is None
    assert per_item(10.0, title) is None


# --- media, and things merely near media ---------------------------------

@pytest.mark.parametrize("title", [
    "Lot of 16 music cds",
    "Lot of 7 VHS",
    "Lot of 5 tween books",
    "Bulk Lot of 9 Movies DVD & Blu Ray Mix",
])
def test_these_are_media(title):
    assert is_media_lot(title)


@pytest.mark.parametrize("title", [
    "80 pcs holographic funny mental health stickers dark humor",
    "Lot of 46 Assorted Clear Vinyl Coin Sleeves & Challenge Coin holders",
    "Lot of 10 empty dvd cases",
    "Lot of 3 Book Sox book covers",
    "Sony BDP-BX320 Samsung BD-E5400 Blu-ray DVD Players Lot of 2",
    "Lot of 88 bumper stickers sarcastic funny novelty book",
])
def test_these_only_look_like_media(title):
    """Every one was a real match. "Vinyl" is a sticker material far more
    often than a record, and a case, a sleeve, a cover or a player is
    packaging or hardware - none of it resells by the disc."""
    assert not is_media_lot(title)
    assert per_item(5.0, title) is None


# --- the arithmetic ------------------------------------------------------

def test_the_price_is_divided_by_the_count():
    assert per_item(4.00, "Lot of 16 music cds") == 0.25
    assert per_item(1.00, "Bulk Lot of 9 Movies DVD & Blu Ray Mix") == 0.111
    assert per_item(3.50, "Books Lot of 20 books") == 0.175


def test_a_free_or_missing_price_gives_nothing():
    for bad in (None, 0, 0.0, -1, "", "abc"):
        assert per_item(bad, "Lot of 16 music cds") is None


def test_the_threshold_the_user_asked_about():
    """Under 40 cents an item is the line. The filter is a strict "under", so
    a lot landing exactly on the number is out - worth pinning, because a
    boundary nobody wrote down is one that drifts."""
    assert per_item(4.00, "Lot of 16 music cds") == 0.25
    assert per_item(2.00, "Lot of 5 CDs") == 0.40        # on the line, not under
    assert not per_item(2.00, "Lot of 5 CDs") < 0.40
    assert per_item(16.00, "Lot of 4 CDs: The Doors, Bob Dylan") == 4.0


def test_a_single_item_is_not_a_lot():
    """One disc has no per-piece story to tell, and dividing by one just
    restates the price."""
    assert item_count("Lot of 1 CD") is None
    assert item_count("1 DVD") is None


def test_an_absurd_count_is_refused():
    """Four digits in a title is a catalogue number or a year far more often
    than a quantity of discs."""
    assert item_count("CD lot 9999 discs") is None


def test_a_missing_title_is_handled():
    assert item_count(None) is None
    assert is_media_lot(None) is False
    assert per_item(10.0, None) is None
