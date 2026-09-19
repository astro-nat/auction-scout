"""fetch_lots must refuse a nonexistent HiBid event id.

LotSearch on a bad auctionId doesn't error — HiBid silently serves its
global/unfiltered lot feed, paged up to the 10k cap. On 2026-09-18 a test
import with a fake event id saved 9 junk lots before it was cancelled.
The guard: fetch_lots confirms the event exists via auctionMap (EventExists)
before touching LotSearch, and raises HibidEventNotFound otherwise.

No network here — _graphql is monkeypatched with canned response shapes.
"""

import asyncio

import pytest

from app.services import hibid

EVENT_ID = 999999901

# Response shapes as HiBid serves them (trimmed to the queried fields).
_EXISTS = {"auctionMap": {"mapMarkers": [{"auction": {"id": EVENT_ID}}]}}
_NOT_FOUND = {"auctionMap": {"mapMarkers": []}}


def _lot(lot_id: int) -> dict:
    return {
        "id": lot_id, "lotNumber": str(lot_id), "lead": f"Lot {lot_id}",
        "description": "", "estimate": None, "category": None,
        "lotState": {"highBid": 5.0, "minBid": 6.0, "bidCount": 1,
                     "status": "OPEN", "timeLeft": "2d 6h 30m"},
        "pictures": [], "shippingOffered": True,
    }


def _lot_page(lots: list[dict]) -> dict:
    return {"lotSearch": {"pagedResults": {
        "totalCount": len(lots), "pageNumber": 1, "results": lots}}}


def _fake_graphql(responses: dict, calls: list):
    """Dispatch by operation name, recording every call."""
    async def fake(client, operation, query, variables):
        calls.append(operation)
        result = responses[operation]
        if isinstance(result, Exception):
            raise result
        return result
    return fake


def test_nonexistent_event_refuses_before_any_lot_fetch(monkeypatch):
    calls: list = []
    monkeypatch.setattr(hibid, "_graphql", _fake_graphql({
        "EventExists": _NOT_FOUND,
        # If the guard is broken, this is the global-feed junk it would save.
        "LotSearch": _lot_page([_lot(1), _lot(2)]),
    }, calls))

    with pytest.raises(hibid.HibidEventNotFound):
        asyncio.run(hibid.fetch_lots(EVENT_ID))
    assert "LotSearch" not in calls, "must not page lots for a dead event id"


def test_existing_event_fetches_normally(monkeypatch):
    calls: list = []
    monkeypatch.setattr(hibid, "_graphql", _fake_graphql({
        "EventExists": _EXISTS,
        "LotSearch": _lot_page([_lot(1), _lot(2)]),
    }, calls))

    lots = asyncio.run(hibid.fetch_lots(EVENT_ID))
    assert calls[0] == "EventExists"
    assert [l["lot_id"] for l in lots] == ["1", "2"]


def test_transient_check_failure_aborts_rather_than_imports(monkeypatch):
    """If the existence check itself dies (network flake after retries), the
    fetch must abort loudly — NOT fall through to LotSearch, and NOT return
    an empty list that a caller would mistake for a lot-less auction."""
    calls: list = []
    monkeypatch.setattr(hibid, "_graphql", _fake_graphql({
        "EventExists": RuntimeError("HiBid EventExists failed after 3 attempts"),
        "LotSearch": _lot_page([_lot(1)]),
    }, calls))

    with pytest.raises(RuntimeError):
        asyncio.run(hibid.fetch_lots(EVENT_ID))
    assert "LotSearch" not in calls
