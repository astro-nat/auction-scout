"""Live-sale catch-up — infer a webcast's progress from hammered lots.

A webcast crier works the catalog in order, but passed/skipped lots keep an
OPEN status on HiBid forever. The highest hammered catalog number marks the
sale's progress; the bid refresh closes everything numerically below it.
"""

import pytest

from app.workers.refresh import _lot_num, live_hammered_through


def _lot(num, status="OPEN", closes_at=None):
    return {"lot_number": num, "status": status, "closes_at": closes_at}


@pytest.mark.parametrize("value,expected", [
    ("214", 214), ("214A", 214), (" 7 ", 7), ("A214", None),
    ("", None), (None, None),
])
def test_lot_num(value, expected):
    assert _lot_num(value) == expected


def test_progress_is_the_highest_hammered_number():
    fresh = [_lot("1", "SOLD"), _lot("2", "OPEN"), _lot("3", "SOLD"),
             _lot("4", "OPEN"), _lot("5", "OPEN")]
    # lot 2 was passed (still OPEN) but lot 3 hammered — the sale is past 3
    assert live_hammered_through(fresh) == 3


def test_no_hammered_lots_means_no_inference():
    fresh = [_lot("1"), _lot("2")]
    assert live_hammered_through(fresh) is None


def test_timed_sales_are_never_inferred():
    """Any per-lot close time = a timed sale, where lots close on their own
    clocks and catalog order says nothing."""
    fresh = [_lot("1", "SOLD"), _lot("2", closes_at="2026-09-20T01:00:00")]
    assert live_hammered_through(fresh) is None


def test_empty_fetch_is_safe():
    assert live_hammered_through([]) is None


def test_unnumbered_lots_are_ignored():
    fresh = [_lot(None, "SOLD"), _lot("12", "SOLD"), _lot("13", "OPEN")]
    assert live_hammered_through(fresh) == 12
