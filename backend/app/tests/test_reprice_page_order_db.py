"""POST /lots/reprice keeps the page's order when asked (`pytest -m db`).

The user's call (2026-11-10): price down the page, so each row fills in as
it's read. Without the flag a queued reprice is soonest-closing first - it
was meant to be all along, but only runs the worker started itself were
ever sorted; a queued job ran in whatever order the database returned.
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
SALE = 999999871


@pytest.fixture
def lots(monkeypatch):
    enqueued = []
    monkeypatch.setattr(router_mod.jobs, "enqueue", lambda *a, **k: enqueued.append(k))
    db = SessionLocal()

    def clean():
        for a in db.query(models.Auction).filter(models.Auction.hibid_id == SALE).all():
            ids = [r[0] for r in db.query(models.Lot.id).filter(models.Lot.auction_id == a.id)]
            if ids:
                db.query(models.Enrichment).filter(models.Enrichment.lot_id.in_(ids)).delete(
                    synchronize_session=False)
                db.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
            db.delete(a)
        db.commit()
    clean()
    a = models.Auction(hibid_id=SALE, name="page order", imported_at=datetime.now(timezone.utc),
                       closing_date=datetime.now() + timedelta(days=3))
    db.add(a)
    db.flush()
    now = datetime.now()
    # Closing order is c, a, b; the page shows b, c, a.
    closes = {"po-a": now + timedelta(hours=2), "po-b": now + timedelta(hours=3),
              "po-c": now + timedelta(hours=1)}
    ids = {}
    for lid, when in closes.items():
        lot = models.Lot(lot_id=lid, auction_id=a.id, title=f"Widget {lid}",
                         current_bid=2, closes_at=when)
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, status="pending"))
        ids[lid] = lot.id
    db.commit()
    yield ids, enqueued
    clean()
    db.close()


def _queued(enqueued):
    return enqueued[0]["payload"]["lot_ids"]


def test_the_page_order_is_kept(lots):
    ids, enqueued = lots
    client.post("/lots/reprice", json={"lot_ids": ["po-b", "po-c", "po-a"], "in_page_order": True})
    assert _queued(enqueued) == [ids["po-b"], ids["po-c"], ids["po-a"]]


def test_without_it_the_soonest_closing_go_first(lots):
    ids, enqueued = lots
    client.post("/lots/reprice", json={"lot_ids": ["po-b", "po-c", "po-a"]})
    assert _queued(enqueued) == [ids["po-c"], ids["po-a"], ids["po-b"]]
