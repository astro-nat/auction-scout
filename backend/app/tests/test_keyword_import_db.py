"""Keyword-scoped import (`pytest -m db`): "Import N of 'query'" alongside
"Import all N auction items".

A scan by keyword already narrowed which AUCTIONS show up; nothing narrowed
which LOTS within them got imported until now. These three layers have to
agree end to end: the scan counts matches per auction and remembers what
keyword produced the count, the import endpoints carry the keyword into
the job payload, and the worker actually passes it to HiBid's lot search.
"""

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import models
from app.main import app
from app.routers import auctions as auctions_router
from app.workers import import_all as import_all_module

pytestmark = pytest.mark.db

client = TestClient(app)

TEST_HIBID = 999999980


def _fake_auction(hibid_id, **over):
    d = dict(hibid_id=hibid_id, name=f"Auction {hibid_id}", auctioneer="Test House",
            auctioneer_id=1, lot_count=50, city="Houston", state="TX", zip="77058",
            closing_date=datetime.now() + timedelta(days=2),
            source="Local Pickup", source_url=f"https://hibid.com/auction/{hibid_id}")
    d.update(over)
    return d


@pytest.fixture()
def cleanup():
    yield
    db = SessionLocal()
    db.query(models.Auction).filter(
        models.Auction.hibid_id == TEST_HIBID).delete(synchronize_session=False)
    db.commit()
    db.close()


# --- scan records which keyword the count belongs to ----------------------

def test_a_keyword_only_scan_counts_and_remembers_it(monkeypatch, cleanup):
    async def fake_discover(**kw):
        return [_fake_auction(TEST_HIBID)]

    async def fake_count(hibid_ids, category_id, search_text=""):
        assert search_text == "pyrex"
        assert category_id == -1
        return {TEST_HIBID: 4}

    monkeypatch.setattr(auctions_router.hibid, "discover_auctions", fake_discover)
    monkeypatch.setattr(auctions_router.hibid, "count_matching_lots", fake_count)

    r = client.post("/auctions/scan", json={"search_text": "pyrex"})
    assert r.status_code == 200
    a = next(x for x in r.json() if x["hibid_id"] == TEST_HIBID)
    assert a["category_lot_count"] == 4
    assert a["category_count_for"] == -1
    assert a["category_count_search"] == "pyrex"


def test_an_unfiltered_scan_never_calls_the_counter(monkeypatch, cleanup):
    async def fake_discover(**kw):
        return [_fake_auction(TEST_HIBID)]

    def boom(*a, **k):
        pytest.fail("count_matching_lots called with no active filter")

    monkeypatch.setattr(auctions_router.hibid, "discover_auctions", fake_discover)
    monkeypatch.setattr(auctions_router.hibid, "count_matching_lots", boom)

    r = client.post("/auctions/scan", json={})
    assert r.status_code == 200
    a = next(x for x in r.json() if x["hibid_id"] == TEST_HIBID)
    assert a["category_lot_count"] is None
    assert a["category_count_search"] is None


def test_category_only_scan_keeps_working_and_records_no_keyword(monkeypatch, cleanup):
    """Regression guard: adding the keyword path must not disturb the
    pre-existing category-only behaviour."""
    async def fake_discover(**kw):
        return [_fake_auction(TEST_HIBID)]

    async def fake_count(hibid_ids, category_id, search_text=""):
        assert search_text == ""
        assert category_id == 40252
        return {TEST_HIBID: 9}

    monkeypatch.setattr(auctions_router.hibid, "discover_auctions", fake_discover)
    monkeypatch.setattr(auctions_router.hibid, "count_matching_lots", fake_count)

    r = client.post("/auctions/scan", json={"category_id": 40252})
    a = next(x for x in r.json() if x["hibid_id"] == TEST_HIBID)
    assert a["category_lot_count"] == 9
    assert a["category_count_for"] == 40252
    assert a["category_count_search"] is None


def test_category_and_keyword_compose_in_one_scan(monkeypatch, cleanup):
    async def fake_discover(**kw):
        return [_fake_auction(TEST_HIBID)]

    async def fake_count(hibid_ids, category_id, search_text=""):
        assert (category_id, search_text) == (40252, "pyrex")
        return {TEST_HIBID: 2}

    monkeypatch.setattr(auctions_router.hibid, "discover_auctions", fake_discover)
    monkeypatch.setattr(auctions_router.hibid, "count_matching_lots", fake_count)

    r = client.post("/auctions/scan",
                    json={"category_id": 40252, "search_text": "pyrex"})
    a = next(x for x in r.json() if x["hibid_id"] == TEST_HIBID)
    assert a["category_lot_count"] == 2
    assert a["category_count_for"] == 40252
    assert a["category_count_search"] == "pyrex"


# --- the bulk import endpoint carries the keyword into the job payload ----

def test_bulk_import_all_carries_the_keyword(monkeypatch, cleanup):
    db = SessionLocal()
    a = models.Auction(**_fake_auction(TEST_HIBID))
    db.add(a)
    db.commit()
    aid = a.id
    db.close()

    enqueued = []
    monkeypatch.setattr(auctions_router.jobs, "has_pending", lambda kind: False)
    monkeypatch.setattr(auctions_router.jobs, "enqueue",
                        lambda kind, label, total=None, payload=None:
                        enqueued.append(payload))
    r = client.post("/auctions/import-all",
                    json={"auction_ids": [aid], "search_text": "pyrex"})
    assert r.status_code == 202
    assert enqueued[0] == {"auction_ids": [aid], "category_id": -1,
                           "bolo_only": False, "search_text": "pyrex"}


# --- the worker actually asks HiBid's lot search for the keyword ----------

def test_the_worker_passes_the_keyword_to_fetch_lots(monkeypatch, cleanup):
    db = SessionLocal()
    a = models.Auction(**_fake_auction(TEST_HIBID))
    db.add(a)
    db.commit()
    aid = a.id
    db.close()

    calls = []

    async def fake_meta(client, hibid_ids):
        return {}

    async def fake_fetch_lots(hibid_id, **kw):
        calls.append(kw.get("search_text"))
        return []

    monkeypatch.setattr(import_all_module.hibid, "fetch_auction_meta", fake_meta)
    monkeypatch.setattr(import_all_module.hibid, "fetch_lots", fake_fetch_lots)

    import_all_module.run_import_all([aid], search_text="pyrex")
    assert calls == ["pyrex"]


def test_a_resumed_worker_recovers_the_keyword_from_the_payload(monkeypatch, cleanup):
    """The worker dispatches a resume with only the auction list and the job
    id — anything else has to ride the payload or it resets to its default,
    exactly like category_id and bolo_only already do."""
    from app.services import jobs as jobs_service

    db = SessionLocal()
    a = models.Auction(**_fake_auction(TEST_HIBID))
    db.add(a)
    db.commit()
    aid = a.id
    db.close()

    job_id = jobs_service.enqueue("import-all", "test", total=1,
                                  payload={"auction_ids": [aid],
                                           "category_id": -1,
                                           "bolo_only": False,
                                           "search_text": "pyrex"})

    calls = []

    async def fake_meta(client, hibid_ids):
        return {}

    async def fake_fetch_lots(hibid_id, **kw):
        calls.append(kw.get("search_text"))
        return []

    monkeypatch.setattr(import_all_module.hibid, "fetch_auction_meta", fake_meta)
    monkeypatch.setattr(import_all_module.hibid, "fetch_lots", fake_fetch_lots)

    # No search_text argument here — a resume must pull it from the row.
    import_all_module.run_import_all([aid], resume_job_id=job_id)
    assert calls == ["pyrex"]
