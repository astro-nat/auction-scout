"""query_variants anchors on the word that names the item.

Pure - no network, no database.

The generator shortens a title progressively and always keeps one anchor
word so a statue is comped against statues, not against 'vintage african'.
It already skipped trailing dimensions when choosing that anchor. It did not
skip trailing qualifiers, and the AI enrichment step appends those freely.
Observed on production: "DeWalt DCF825 18V Cordless Impact Driver Tool
Only" produced four variants ending in "Only" - the shortest was "DeWalt
DCF825 Only", a phrase no eBay seller has ever typed. Four empty queries,
four API requests burned, the empties cached for a week, and the lot fell
back to asking prices.
"""

from app.services.pricing import query_variants


def _shortened(title):
    """Every variant after the near-full first one."""
    return query_variants(title)[1:]


def test_a_trailing_qualifier_is_not_the_anchor():
    """The production case. 'Only' must never be what a query hangs on."""
    variants = query_variants("DeWalt DCF825 18V Cordless Impact Driver Tool Only")
    assert not any(v.lower().endswith(" only") for v in _shortened(
        "DeWalt DCF825 18V Cordless Impact Driver Tool Only"))
    assert "DeWalt DCF825 Tool" in variants


def test_the_shortest_variant_still_names_the_item():
    """'Works' is a claim about the listing. 'Drill' is what is for sale."""
    assert query_variants("Milwaukee M18 Hammer Drill Works")[-1] == "Milwaukee M18 Drill"


def test_several_trailing_qualifiers_are_all_skipped():
    """'Pair NIB' - two qualifiers deep, and the anchor still has to be the
    noun behind them."""
    for v in _shortened("Lenox Crystal Candlestick Pair NIB"):
        assert v.endswith("Candlestick"), v


def test_the_documented_intent_is_preserved():
    """The docstring's own example: the item noun survives every cut."""
    for v in _shortened("Vintage Ironwood 18 Head Statue"):
        assert v.endswith("Statue"), v


def test_a_trailing_dimension_is_still_skipped():
    """The original skip rule, kept: a measurement is not the item."""
    for v in _shortened("Griswold No 8 Cast Iron Skillet 10.5"):
        assert v.endswith("Skillet"), v


def test_a_title_made_only_of_qualifiers_does_not_underflow():
    """Nothing to anchor on must not crash or return nothing."""
    variants = query_variants("Tool Only")
    assert variants
    assert variants[0] == "Tool Only"


def test_the_full_title_is_still_tried_first():
    """Most specific match wins: the untouched title stays variant zero,
    qualifiers and all. Only the SHORTENED forms re-anchor."""
    assert query_variants("DeWalt DCF825 18V Cordless Impact Driver Tool Only")[0] == \
        "DeWalt DCF825 18V Cordless Impact Driver Tool Only"
