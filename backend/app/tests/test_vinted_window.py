"""What a Vinted search actually sees, and what absence from it proves. Pure.

Two bugs, both measured against live searches on 2026-09-29.

1. The card parser knew about Brand and Condition only. A card carrying a
   Size matched neither pattern and was dropped with a warning nobody reads.
   "cd lots" lost nothing, because media has no size. "nike shoes" lost 95 of
   96 cards.

2. The search read two pages and the caller treated anything it did not see
   again as sold. "cd lots" returns 960 listings over 10 pages, so two pages
   was 20% of it - wrong in both directions: live lots closed for scrolling
   out of the window, sold ones kept alive because the window never reached
   them. Vinted states its own total_pages on every page, so the scan can
   now tell whether it read everything, and only a complete read is evidence.
"""

import pytest

from app.services.vinted import (_ALT_FIELD_RE, _ALT_RE, PAGES_MAX,
                                 parse_catalog, result_size)


def _card(item_id, alt, thumb="https://images1.vinted.net/t/x/1.webp"):
    """One catalog card in the shape the page actually ships."""
    return (f'<img src="{thumb}" alt="{alt}" '
            f'data-testid="product-item-id-{item_id}--image--img">')


def _parsed(alt):
    return parse_catalog(_card(1, alt))


# --- the cards that used to be dropped -----------------------------------

REAL_ALTS = [
    # media: no size, which is why "cd lots" never showed the bug
    ("189 mixed CD lot with case, Condition: Good, 150.00 $, 158.20 $",
     "189 mixed CD lot with case", None, "Good", 150.0),
    ("Vintage Barbie cd and cassette, Brand: Barbie, Condition: Very good, "
     "Size: One size, 25.00 $, 26.95 $",
     "Vintage Barbie cd and cassette", "Barbie", "Very good", 25.0),
    # the shape that lost 99% of a clothing search
    ("Lot of 4 T shirts. Size M, Brand: Gildan, Condition: New without tags, "
     "Size: M, 10.00 $, 11.20 $",
     "Lot of 4 T shirts. Size M", "Gildan", "New without tags", 10.0),
    ("Shirt bundle, Brand: Miscellaneous, Condition: Very good, Size: M, "
     "7.00 $, 8.05 $",
     "Shirt bundle", "Miscellaneous", "Very good", 7.0),
    ("Vintage Christian Dior Black Velvet Maxi Dress, Brand: Dior, "
     "Condition: Very good, Size: S / US 4-6, 80.00 $, 84.70 $",
     "Vintage Christian Dior Black Velvet Maxi Dress", "Dior", "Very good", 80.0),
    ("American Girl Doll Lot Julie, Brand: American Girl, Condition: Very good, "
     "Size: One size, 200.00 $, 210.70 $",
     "American Girl Doll Lot Julie", "American Girl", "Very good", 200.0),
]


@pytest.mark.parametrize("alt,title,brand,cond,price", REAL_ALTS)
def test_real_cards_parse(alt, title, brand, cond, price):
    got = _parsed(alt)
    assert len(got) == 1, f"card dropped: {alt}"
    assert got[0]["title"] == title
    assert got[0]["brand"] == brand
    assert got[0]["condition"] == cond
    assert got[0]["price"] == price


def test_a_size_in_the_title_is_not_mistaken_for_the_size_field():
    """"Lot of 4 T shirts. Size M" is the seller's own words. The Size FIELD
    is the one Vinted appends at the end, and the title keeps its own."""
    got = _parsed("Lot of 4 T shirts. Size M, Brand: Gildan, "
                  "Condition: New without tags, Size: M, 10.00 $, 11.20 $")[0]
    assert got["title"] == "Lot of 4 T shirts. Size M"


def test_the_item_price_is_taken_not_the_total():
    """Cards carry "item $, item+fee $". Bidding against the fee-inclusive
    number would quietly raise every max bid."""
    got = _parsed("CD lot, Condition: Good, 150.00 $, 158.20 $")[0]
    assert got["price"] == 150.0


def test_a_price_with_a_thousands_comma():
    got = _parsed("Rare box set, Condition: Good, 1,250.00 $, 1,320.00 $")[0]
    assert got["price"] == 1250.0


def test_a_title_containing_a_comma_survives():
    got = _parsed("CDs, tapes and vinyl, Condition: Good, 20.00 $")[0]
    assert got["title"] == "CDs, tapes and vinyl"


def test_an_unparseable_card_is_dropped_not_guessed():
    """Importing a title with the price glued on is worse than importing
    nothing - it prices a lot off a string."""
    assert parse_catalog(_card(1, "no labelled fields at all here")) == []


# --- how much of the result set was read ---------------------------------

def _page(entries, pages, current=1):
    return ('"pagination":{"current_page":%d,"per_page":96,"time":1790733794,'
            '"total_entries":%d,"total_pages":%d}' % (current, entries, pages))


def test_the_result_size_is_read_from_the_page():
    assert result_size(_page(960, 10)) == (960, 10)


def test_an_escaped_payload_is_read_too():
    """The hydration JSON arrives backslash-escaped inside the HTML."""
    assert result_size(_page(960, 10).replace('"', '\\"')) == (960, 10)


def test_a_page_without_pagination_reports_nothing_rather_than_guessing():
    assert result_size("<html>no pagination here</html>") == (0, 0)


def test_the_window_reaches_a_ten_page_search():
    """"cd lots" is 960 listings over 10 pages. A cap under that would leave
    the close-out unable to ever run on it."""
    assert PAGES_MAX >= 10
