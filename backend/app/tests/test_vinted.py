"""Vinted catalog parser — no network.

The card shape is a real fragment (captured 2026-09-21): everything rides
the image's alt text, parsed from its anchored tail because titles can
contain commas. A reshuffled alt logs and skips instead of importing
titles with prices glued on.
"""

from app.services import vinted

CARD = ('<img src="https://images1.vinted.net/t/05_0092b_x/310x430/57a509b1.webp?s=abc" '
        'alt="Vintage Pyrex Flameware 6-Cup Percolator, Pot &amp; Lid, '
        'Brand: Pyrex, Condition: Good, 1,048.00 $, 1,101.10 $" '
        'class="web_ui__Image__content" '
        'data-testid="product-item-id-10079121744--image--img"/>')

NO_PROTECTION = ('<img src="https://images1.vinted.net/t/x.webp" '
                 'alt="Plain Mug, Brand: , Condition: New with tags, 12.00 $" '
                 'data-testid="product-item-id-555--image--img"/>')

GARBAGE_ALT = ('<img src="https://images1.vinted.net/t/y.webp" '
               'alt="Just a picture caption" '
               'data-testid="product-item-id-777--image--img"/>')


def test_a_card_parses_with_commas_in_the_title():
    items = vinted.parse_catalog(CARD)
    assert len(items) == 1
    it = items[0]
    assert it["lot_id"] == "vt-10079121744"
    # The title keeps its own commas; brand/condition/price come off the tail.
    assert it["title"] == "Vintage Pyrex Flameware 6-Cup Percolator, Pot & Lid"
    assert it["brand"] == "Pyrex"
    assert it["condition"] == "Good"
    assert it["price"] == 1048.00                  # ask, not protection price
    assert it["thumbnail_url"].startswith("https://images1.vinted.net/")
    assert it["lot_link"] == "https://www.vinted.com/items/10079121744"


def test_missing_brand_and_single_price_still_parse():
    it = vinted.parse_catalog(NO_PROTECTION)[0]
    assert it["brand"] is None
    assert it["condition"] == "New with tags"
    assert it["price"] == 12.0


def test_media_cards_have_no_brand_segment():
    """CDs and books go straight to Condition — the pyrex shape with an
    optional-brand regex would let a greedy title swallow the brand."""
    card = ('<img src="https://images1.vinted.net/t/z.webp" '
            'alt="Tom Petty &amp; The Heartbreakers - Hard Promises CD 1981, '
            'Condition: Very good, 21.47 $, 23.62 $" '
            'data-testid="product-item-id-888--image--img"/>')
    it = vinted.parse_catalog(card)[0]
    assert it["title"] == "Tom Petty & The Heartbreakers - Hard Promises CD 1981"
    assert it["brand"] is None
    assert it["condition"] == "Very good"
    assert it["price"] == 21.47


def test_an_unrecognized_alt_is_skipped_not_mangled():
    assert vinted.parse_catalog(GARBAGE_ALT) == []


def test_empty_html_parses_to_nothing():
    assert vinted.parse_catalog("<html></html>") == []
