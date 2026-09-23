"""POST /lots/reprice?weak_only=true (`pytest -m db`).

1,390 lots sat on asking-price numbers for a week. Three things put them
there - a query anchored on "Only", exactMatch=true stripping every
loosened match, and the 09-21 quota wall - and the durable cache then
served each empty answer for seven days without asking again. A plain
re-price could not recover them: it was handed the same empties back in
ten seconds and looked exactly like a genuine miss.

So the weak-only path does two things in order: purge every cached EMPTY
sold answer, then queue only the lots priced from asking listings. And
dry_run reports both counts and spends nothing, because the request cost
of a bulk re-price is real money and should be known before it is paid.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.routers import enrichment as router_mod
from app.services import pricing

pytestmark = pytest.mark.db

client = TestClient(app)
TEST_HIBID = 999999976
PREFIX = "reprice-weak-test "


@pytest.fixture
def seeded(monkeypatch):
    """One asking-priced lot, one sold-priced lot, and four cache rows:
    an empty sold answer, a NULL sold answer, a full sold answer, and an
    empty ACTIVE answer that must survive - it is not what went wrong."""
    enqueued = []
    monkeypatch.setattr(router_mod.jobs, "has_pending", lambda kind: False)
    monkeypatch.setattr(router_mod.jobs, "enqueue",
                        lambda *a, **k: enqueued.append((a, k)))
    pricing._cache.clear()

    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="reprice-weak-test",
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.flush()
    weak = models.Lot(lot_id="rw-weak", title="DeWalt DCF825 Impact Driver",
                      auction_id=a.id, current_bid=2)
    strong = models.Lot(lot_id="rw-strong", title="Craftsman Rotary Tool",
                        auction_id=a.id, current_bid=2)
    db.add_all([weak, strong])
    db.flush()
    db.add_all([
        models.Enrichment(lot_id=weak.id, status="success", est_resale=19.49,
                          enriched_title="DeWalt DCF825 Impact Driver",
                          price_source="active (eBay) x0.65 asking->sold"),
        models.Enrichment(lot_id=strong.id, status="success", est_resale=41.73,
                          enriched_title="Craftsman Rotary Tool",
                          price_source="sold (SoldComps)"),
        models.CompCache(query=PREFIX + "empty", source="soldcomps", payload=[]),
        models.CompCache(query=PREFIX + "null", source="soldcomps", payload=None),
        models.CompCache(query=PREFIX + "full", source="soldcomps",
                         payload=[[40.0, "a real sale"]]),
        models.CompCache(query=PREFIX + "active-empty", source="active", payload=[]),
    ])
    db.commit()
    ids = {"weak": weak.id, "strong": strong.id}
    yield db, ids, enqueued
    db.rollback()
    db.query(models.CompCache).filter(
        models.CompCache.query.like(PREFIX + "%")).delete(synchronize_session=False)
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(list(ids.values()))).delete(synchronize_session=False)
    db.query(models.Lot).filter(
        models.Lot.id.in_(list(ids.values()))).delete(synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == a.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _cache_queries(db):
    return {r.query for r in db.query(models.CompCache)
            .filter(models.CompCache.query.like(PREFIX + "%")).all()}


def test_weak_only_queues_just_the_asking_priced_lots(seeded):
    """The sold-priced lot has nothing to recover from. Re-pricing it would
    burn four requests to arrive at the same number."""
    db, ids, enqueued = seeded
    r = client.post("/lots/reprice?weak_only=true")
    assert r.status_code == 202, r.text
    assert len(enqueued) == 1
    queued_ids = enqueued[0][1]["payload"]["lot_ids"]
    assert ids["weak"] in queued_ids
    assert ids["strong"] not in queued_ids


def test_weak_only_purges_the_empty_sold_answers_first(seeded):
    """Empty and NULL sold rows go. The full sold row is the cache doing its
    job, and the empty ACTIVE row is a different source entirely - both
    stay. The response says how many were removed."""
    db, ids, enqueued = seeded
    r = client.post("/lots/reprice?weak_only=true")
    assert r.json()["cache_purged"] >= 2
    left = _cache_queries(db)
    assert PREFIX + "empty" not in left
    assert PREFIX + "null" not in left
    assert PREFIX + "full" in left
    assert PREFIX + "active-empty" in left


def test_a_plain_reprice_touches_neither_filter_nor_cache(seeded):
    """The existing contract, kept: no flag means every enriched lot, and
    the cache is left exactly as it was."""
    db, ids, enqueued = seeded
    before = _cache_queries(db)
    r = client.post("/lots/reprice")
    assert r.json().get("cache_purged", 0) == 0
    queued_ids = enqueued[0][1]["payload"]["lot_ids"]
    assert ids["weak"] in queued_ids and ids["strong"] in queued_ids
    assert _cache_queries(db) == before


def test_dry_run_reports_both_counts_and_spends_nothing(seeded):
    """The request cost of a bulk re-price is real money. Both numbers must
    be visible before anything is queued or deleted."""
    db, ids, enqueued = seeded
    before = _cache_queries(db)
    r = client.post("/lots/reprice?weak_only=true&dry_run=true")
    body = r.json()
    assert body["dry_run"] is True
    assert body["repricing"] >= 1
    assert body["cache_empties"] >= 2
    assert enqueued == [], "dry_run queued a job"
    assert _cache_queries(db) == before, "dry_run deleted cache rows"
