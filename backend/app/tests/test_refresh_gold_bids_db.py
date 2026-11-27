"""POST /auctions/refresh-bids?gold_only=true (`pytest -m db`).

The user's call (2026-11-27): the Refresh bids button refreshes the gold
mines only - fetched lot by lot, not whole catalogues.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.routers import auctions as router_mod

pytestmark = pytest.mark.db
client = TestClient(app)
SALE = 999999861


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
    a = models.Auction(hibid_id=SALE, name="gold refresh", imported_at=datetime.now(timezone.utc),
                       closing_date=datetime.now() + timedelta(days=2))
    db.add(a)
    db.flush()
    soon = datetime.now() + timedelta(hours=5)
    for lid, status, hidden, closes in (
            ("gr-gold", "GOLD MINE", False, soon),
            ("gr-pass", "PASS", False, soon),
            ("gr-hidden", "GOLD MINE", True, soon),
            ("gr-closed", "GOLD MINE", False, datetime.now() - timedelta(hours=1))):
        lot = models.Lot(lot_id=lid, auction_id=a.id, title="Widget", current_bid=2,
                         closes_at=closes, hidden=hidden, lot_number=lid)
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, status="success", roi_status=status))
    db.commit()
    yield a.id, enqueued
    clean()
    db.close()


def test_only_open_visible_gold_mines_are_refreshed(lots):
    auction_id, enqueued = lots
    r = client.post("/auctions/refresh-bids?gold_only=true")
    assert r.status_code == 202 and r.json()["queued"] is True
    payload = enqueued[0]["payload"]
    assert "gr-gold" in payload["lot_ids"]
    assert not {"gr-pass", "gr-hidden", "gr-closed"} & set(payload["lot_ids"])
    assert auction_id in payload["auction_ids"]
