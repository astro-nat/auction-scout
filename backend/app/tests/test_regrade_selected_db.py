"""Re-grade just the lots asked for (`pytest -m db`)."""

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app

pytestmark = pytest.mark.db
client = TestClient(app)
SALE = 999999891


@pytest.fixture
def db():
    s = SessionLocal()

    def clean():
        for a in s.query(models.Auction).filter(models.Auction.hibid_id == SALE).all():
            ids = [r[0] for r in s.query(models.Lot.id).filter(models.Lot.auction_id == a.id)]
            if ids:
                s.query(models.Enrichment).filter(models.Enrichment.lot_id.in_(ids)).delete(
                    synchronize_session=False)
                s.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
            s.delete(a)
        s.commit()
    clean()
    yield s
    clean()
    s.close()


def test_only_the_named_lots_are_regraded(db):
    a = models.Auction(hibid_id=SALE, name="Regrade test", source="Local Pickup")
    db.add(a)
    db.commit()
    for lid in ("rg-1", "rg-2"):
        lot = models.Lot(lot_id=lid, auction_id=a.id, title="Widget", status="OPEN",
                         current_bid=5, next_bid=6, logistics_ease="EASY")
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, status="success", est_resale=100,
                                 comp_count=5, roi_status=None))
    db.commit()
    r = client.post("/lots/regrade", json={"lot_ids": ["rg-1"]})
    assert r.status_code == 202 and r.json()["regraded"] == 1
    db.expire_all()
    got = {l.lot_id: l.enrichment.roi_status for l in
           db.query(models.Lot).filter(models.Lot.lot_id.in_(["rg-1", "rg-2"]))}
    assert got["rg-1"] is not None and got["rg-2"] is None
