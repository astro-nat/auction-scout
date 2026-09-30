"""An AI batch queues soonest-closing first (`pytest -m db`)."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app

pytestmark = pytest.mark.db
client = TestClient(app)
HIBID = 999999831


def _clean(db):
    for a in db.query(models.Auction).filter(models.Auction.hibid_id == HIBID).all():
        ids = [r[0] for r in db.query(models.Lot.id).filter(models.Lot.auction_id == a.id)]
        if ids:
            db.query(models.Enrichment).filter(models.Enrichment.lot_id.in_(ids)).delete(
                synchronize_session=False)
            db.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
        db.delete(a)
    db.commit()


@pytest.fixture
def db():
    s = SessionLocal()
    _clean(s)
    yield s
    _clean(s)
    s.close()


def test_the_batch_is_ranked_by_closing_time(db):
    now = datetime.utcnow()
    a = models.Auction(hibid_id=HIBID, name="Closing order test", closing_date=now + timedelta(days=1))
    db.add(a)
    db.flush()
    # Sent most valuable first (late, soon, middle); they should queue soon, middle, late.
    for lot_id, hours in (("co-late", 9), ("co-soon", 1), ("co-mid", 4)):
        lot = models.Lot(lot_id=lot_id, auction_id=a.id, title=lot_id, status="OPEN",
                         closes_at=now + timedelta(hours=hours))
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, status="pending"))
    db.commit()

    r = client.post("/lots/enrich-batch", json={"lot_ids": ["co-late", "co-soon", "co-mid"]})
    assert r.status_code == 202, r.text

    db.expire_all()
    ranked = (db.query(models.Lot.lot_id)
                .join(models.Enrichment, models.Enrichment.lot_id == models.Lot.id)
                .filter(models.Lot.auction_id == a.id)
                .order_by(models.Enrichment.queue_rank).all())
    assert [r[0] for r in ranked] == ["co-soon", "co-mid", "co-late"]
