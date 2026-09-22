"""Recovering the auditor's own number when it writes it in prose.

From a real lot: a Swarovski SCS member gift priced at $370 by comps. The
auditor rejected it with "Swarovski SCS annual eggs typically sell for
$80-150 on eBay; the $370 comp likely represents a rare/retired year or
bundled lot, not this 2023 standard issue" - and left realistic_value
empty, so the code would not guess and the debunked $370 stayed on display
next to the note explaining it was wrong.

It does not have to guess. The number is in the sentence.
"""

import pytest

from app.workers.enrich import _audit_correction

REAL_NOTE = ("Swarovski SCS annual eggs typically sell for $80-150 on eBay; "
             "the $370 comp likely represents a rare/retired year or bundled "
             "lot, not this 2023 standard issue.")


def test_the_structured_field_wins_when_present():
    assert _audit_correction({"realistic_value": 90, "reason": "x"}, 370.0) == 90.0


def test_the_number_is_read_out_of_the_real_note():
    assert _audit_correction({"reason": REAL_NOTE}, 370.0) == 80.0


def test_a_range_takes_its_low_end():
    """An auditor rejecting a value for being too high should not have its
    own correction rounded up."""
    assert _audit_correction({"reason": "these sell for $40-$75"}, 300.0) == 40.0


def test_a_single_figure_is_taken_as_is():
    assert _audit_correction({"reason": "around $45 used"}, 300.0) == 45.0


def test_thousands_separators_parse():
    assert _audit_correction({"reason": "closer to $1,200 at auction"}, 5000.0) == 1200.0


def test_restating_the_rejected_figure_is_not_a_correction():
    """The reason usually quotes the number it is rejecting. Taking that
    would 'correct' the value to itself."""
    assert _audit_correction({"reason": "the $370 comp is a bundle"}, 370.0) is None


def test_no_number_at_all_means_a_plain_demotion():
    assert _audit_correction({"reason": "no meaningful resale value"}, 370.0) is None


@pytest.mark.parametrize("payload", [
    {"realistic_value": 0, "reason": "worthless"},
    {"realistic_value": -5, "reason": "x"},
    {"realistic_value": 500, "reason": "x"},
    {"realistic_value": 370, "reason": "x"},
])
def test_incoherent_values_are_refused(payload):
    """Zero is better said by a plain demotion, and a value at or above the
    claim contradicts having called the claim implausible."""
    assert _audit_correction(payload, 370.0) is None


def test_prose_is_only_a_fallback_never_an_override():
    """A usable structured value is not second-guessed by the text."""
    out = _audit_correction(
        {"realistic_value": 120, "reason": "similar pieces fetch $30"}, 370.0)
    assert out == 120.0
