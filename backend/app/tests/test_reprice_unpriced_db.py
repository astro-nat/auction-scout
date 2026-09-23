"""POST /lots/reprice?unpriced_only=true - the comps-only first pass (`pytest -m db`).

Pricing 2,400 lots costs about $12 of AI. Nearly all of that is the model
reading each lot to write a title and judge condition; the comp search
itself is on a plan with no per-call charge. The re-price job already
searches a lot's raw title whenever it has no AI one, so the only thing
keeping never-priced lots out of a free comps pass was this endpoint's
filter.

The scope: never priced, open auction, not hidden - and no requirement of
an AI title, which every other path here has. Closed auctions are out (a
price on a lot you cannot bid on buys nothing) and so are lots that have
a value already. The plain re-price keeps its old contract.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.routers import enrichment as router_mod

pytestmark = pytest.mark.db

client = TestClient(app)
OPEN_HIBID, CLOSED_HIBID = 999999982, 999999983


@pytest.fixture
def seeded(monkeypatch):
    enqueued = []
    monkeypatch.setattr(router_mod.jobs, "has_pending", lambda kind: False)
    monkeypatch.setattr(router_mod.jobs, "enqueue",
                        lambda *a, **k: enqueued.append((a, k)))
    db = SessionLocal()
    open_a = models.Auction(hibid_id=OPEN_HIBID, name="unpriced-open",
                            closing_date=datetime.now() + timedelta(days=3),
                            imported_at=datetime.now(timezone.utc))
    closed_a = models.Auction(hibid_id=CLOSED_HIBID, name="unpriced-closed",
                              closing_date=datetime.now() - timedelta(days=1),
                              imported_at=datetime.now(timezone.utc))
    db.add_all([open_a, closed_a])
    db.flush()
    unpriced = models.Lot(lot_id="up-1", title="DeWalt DCF825 Impact Driver",
                          auction_id=open_a.id, current_bid=2)
    priced = models.Lot(lot_id="up-2", title="Craftsman Rotary Tool",
                        auction_id=open_a.id, current_bid=2)
    closed = models.Lot(lot_id="up-3", title="Makita Drill", auction_id=closed_a.id,
                        current_bid=2)
    db.add_all([unpriced, priced, closed])
    db.flush()
    db.add_all([
        models.Enrichment(lot_id=unpriced.id, status="pending"),
        models.Enrichment(lot_id=priced.id, status="success", est_resale=41.73,
                          enriched_title="Craftsman Rotary Tool",
                          price_source="sold (SoldComps)"),
        models.Enrichment(lot_id=closed.id, status="pending"),
    ])
    db.commit()
    ids = {"unpriced": unpriced.id, "priced": priced.id, "closed": closed.id}
    yield ids, enqueued
    db.rollback()
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(list(ids.values()))).delete(synchronize_session=False)
    db.query(models.Lot).filter(
        models.Lot.id.in_(list(ids.values()))).delete(synchronize_session=False)
    db.query(models.Auction).filter(
        models.Auction.id.in_([open_a.id, closed_a.id])).delete(synchronize_session=False)
    db.commit()
    db.close()


def _queued(enqueued):
    return enqueued[0][1]["payload"]["lot_ids"] if enqueued else []


def test_unpriced_only_queues_never_priced_lots_in_open_auctions(seeded):
    ids, enqueued = seeded
    r = client.post("/lots/reprice?unpriced_only=true")
    assert r.status_code == 202, r.text
    queued = _queued(enqueued)
    assert ids["unpriced"] in queued
    assert ids["priced"] not in queued, "a lot with a value is not unpriced"
    assert ids["closed"] not in queued, "a price on a closed lot buys nothing"


def test_unpriced_only_needs_no_ai_title(seeded):
    """The whole point: the lot has never been looked at by the model."""
    ids, enqueued = seeded
    client.post("/lots/reprice?unpriced_only=true")
    assert ids["unpriced"] in _queued(enqueued)


def test_dry_run_reports_the_count_and_the_request_cost(seeded):
    ids, enqueued = seeded
    body = client.post("/lots/reprice?unpriced_only=true&dry_run=true").json()
    assert body["dry_run"] is True
    assert body["repricing"] >= 1
    assert body["requests_estimate"] == body["repricing"]
    assert enqueued == []


def test_the_plain_reprice_still_requires_an_ai_title(seeded):
    """Existing contract, kept: without the flag, never-priced lots stay out."""
    ids, enqueued = seeded
    client.post("/lots/reprice")
    queued = _queued(enqueued)
    assert ids["priced"] in queued
    assert ids["unpriced"] not in queued
