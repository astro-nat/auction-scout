"""Finding one lot by its number, instead of paging a catalogue. Pure.

lotSearch offers no by-id lookup - searchText is the only filter - so the
lot NUMBER stands in for one. Measured against a live 385-lot sale:
searching "1" returned 4 results, "1a" returned 1, "2" returned 20, "250"
returned 2, and the exact match was on the first page every time.

What matters here is the matching, because lot numbers are text with
letters in them and a near-miss would silently refresh the wrong lot.
"""

import asyncio

import pytest

from app.services import hibid
from app.services.hibid import _same_lot_number


def _raw(lot_number, lot_id, bid=0.0):
    """A lotSearch result as HiBid returns it."""
    return {"id": lot_id, "lotNumber": lot_number, "lead": f"Lot {lot_number}",
            "description": "", "estimate": "", "category": {"categoryName": "x"},
            "lotState": {"highBid": bid, "minBid": bid + 1, "bidCount": 0,
                         "status": "OPEN", "timeLeft": "2d"},
            "pictures": [], "shippingOffered": True}


class _FakeClient:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


@pytest.fixture
def stub(monkeypatch):
    """Answer LotSearch from a fixed catalogue, and record what was asked."""
    catalogue = [_raw("1", "a1"), _raw("1a", "a2"), _raw("162w", "a3"),
                 _raw("250", "a4")]
    asked = []

    async def _graphql(client, op, query, variables):
        term = str(variables["searchText"]).lower()
        asked.append(term)
        hits = [r for r in catalogue if term in str(r["lotNumber"]).lower()]
        return {"lotSearch": {"pagedResults": {"totalCount": len(hits),
                                               "pageNumber": 1, "results": hits}}}

    async def _exists(client, hibid_id):
        return None

    monkeypatch.setattr(hibid, "_graphql", _graphql)
    monkeypatch.setattr(hibid, "_assert_event_exists", _exists)
    monkeypatch.setattr(hibid.httpx, "AsyncClient", lambda *a, **k: _FakeClient())
    return asked


def _run(numbers):
    return asyncio.run(hibid.fetch_lots_by_number(1, numbers, {"source": "Ship"}))


def test_it_finds_the_exact_lot(stub):
    found, missing = _run(["250"])
    assert missing == []
    assert [f["lot_number"] for f in found] == ["250"]


def test_a_number_that_is_a_prefix_of_others_still_resolves(stub):
    """Searching "1" also returns 1a and 162w. Taking the first result would
    refresh the wrong lot; only the exact number counts."""
    found, missing = _run(["1"])
    assert missing == []
    assert [f["lot_number"] for f in found] == ["1"]


def test_letters_in_a_lot_number_are_kept(stub):
    found, _ = _run(["1a"])
    assert [f["lot_number"] for f in found] == ["1a"]


def test_a_lot_that_is_not_there_is_reported_missing(stub):
    found, missing = _run(["999"])
    assert found == []
    assert missing == ["999"]


def test_a_lot_with_no_number_is_missing_not_searched(stub):
    """Nothing to search on. Searching an empty string would return the whole
    catalogue and match nothing."""
    found, missing = _run([None, ""])
    assert found == []
    assert missing == [None, ""]
    assert stub == [], "searched on an empty lot number"


def test_one_request_per_lot(stub):
    _run(["1", "250"])
    assert stub == ["1", "250"]


def test_the_same_lot_twice_is_returned_once(stub):
    found, _ = _run(["250", "250"])
    assert len(found) == 1


def test_a_cancelled_run_stops_asking(monkeypatch, stub):
    found, _ = asyncio.run(hibid.fetch_lots_by_number(
        1, ["1", "250"], {"source": "Ship"}, should_cancel=lambda: True))
    assert found == []
    assert stub == []


@pytest.mark.parametrize("a,b,same", [
    ("1", "1", True),
    ("1a", "1A", True),
    (" 250 ", "250", True),
    ("1", "1a", False),
    ("1", "162w", False),
    ("", "1", False),
    (None, None, True),
])
def test_lot_numbers_compare_as_text(a, b, same):
    """They are not integers: "1a" and "162w" are real lot numbers."""
    assert _same_lot_number(a, b) is same
