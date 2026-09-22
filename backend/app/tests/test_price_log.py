"""Evidence tiering for the kept price trail.

est_resale shows one number - the best-evidenced one. Every other figure
the app produced for that lot, including the ones it threw out, is kept,
because the wrong numbers are the only data that says where the algorithm
fails. A comp set that overshot tenfold is the evidence that a filter is
missing, and overwriting in place discarded it every time.
"""

import pytest

from app.services.price_log import EVIDENCE_RANK, classify, rank


@pytest.mark.parametrize("source,comps,expected", [
    ("sold (SoldComps)", 20, "sold"),
    ("sold (SoldComps)", 3, "sold"),
    ("sold (thin comps - vintage lamp)", 2, "sold_thin"),
    ("sold (SoldComps) x0.7 condition", 1, "sold_thin"),
    ("retail $729 in title x0.5", 1, "retail"),
    ("audit-corrected (comps said $370)", 0, "audit"),
    ("itemized vision (3 from comps of 8 worth listing)", 3, "itemized"),
    ("active (eBay) x0.65 asking->sold", 6, "active"),
])
def test_a_price_source_maps_to_its_tier(source, comps, expected):
    assert classify(source, comps) == expected


def test_completed_sales_outrank_asking_prices():
    """The distinction the whole hierarchy exists for."""
    assert rank("sold") > rank("active")


def test_three_agreeing_sales_outrank_one():
    """One sale is an anecdote. Every wrong gold in the Watermark audit
    traced back to a single generic comp."""
    assert rank("sold") > rank("sold_thin")


def test_a_printed_retail_price_is_strong_evidence():
    """It is one data point, but it is what the item actually sells for
    new - stronger than a handful of hopeful listings."""
    assert rank("retail") > rank("active")
    assert rank("retail") > rank("itemized")


def test_an_audit_correction_beats_the_asking_prices_it_replaced():
    assert rank("audit") > rank("active")
    assert rank("audit") > rank("ai")


def test_the_house_estimate_ranks_last():
    """Promotional, not market data."""
    assert rank("house") == min(EVIDENCE_RANK.values())


def test_an_unknown_source_ranks_below_everything_known():
    assert rank("something new") == 0
    assert rank(None) == 0
    assert all(v > 0 for v in EVIDENCE_RANK.values())


def test_classify_survives_missing_input():
    """Old rows predate some of these strings; none of them should raise."""
    assert classify(None, None) == ""
    assert classify("", 0) == ""
