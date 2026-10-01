"""Live Auction mode's switch and its self-shutoff (`pytest -m db`)."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.workers import live

pytestmark = pytest.mark.db
client = TestClient(app)

OPEN_SALE, OVER_SALE = 999999841, 999999842


def _clean(db):
    for a in db.query(models.Auction).filter(
            models.Auction.hibid_id.in_((OPEN_SALE, OVER_SALE))).all():
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


def _sale(db, hibid_id, closes_in_hours):
    a = models.Auction(hibid_id=hibid_id, name=f"Live test {hibid_id}", source="Local Pickup",
                       closing_date=datetime.utcnow() + timedelta(hours=closes_in_hours))
    db.add(a)
    db.commit()
    return a


def test_switch_on_and_off(db):
    a = _sale(db, OPEN_SALE, 2)
    r = client.post(f"/auctions/{a.id}/live", params={"live": True})
    assert r.status_code == 200, r.text
    assert r.json()["live"] is True
    r = client.post(f"/auctions/{a.id}/live", params={"live": False})
    assert r.json()["live"] is False


def test_a_closed_sale_cannot_go_live(db):
    a = _sale(db, OVER_SALE, -3)
    r = client.post(f"/auctions/{a.id}/live", params={"live": True})
    assert r.status_code == 422
    assert "closed" in r.json()["detail"]


def test_a_live_sale_switches_itself_off_once_over(db, monkeypatch):
    a = _sale(db, OVER_SALE, -3)
    a.live = True
    db.commit()
    called = []
    monkeypatch.setattr("app.workers.refresh.run_bid_refresh",
                        lambda ids, **kw: called.append(ids))
    live.run_once()
    db.expire_all()
    assert db.query(models.Auction).get(a.id).live is False
    assert called == [], "refreshed a sale that was already over"


def _lot(db, auction, lot_id, closes_in_minutes):
    lot = models.Lot(lot_id=lot_id, auction_id=auction.id, title=lot_id, status="OPEN",
                     closes_at=datetime.utcnow() + timedelta(minutes=closes_in_minutes))
    db.add(lot)
    db.flush()
    db.add(models.Enrichment(lot_id=lot.id, status="pending"))
    db.commit()


def test_a_lot_closing_within_the_hour_turns_the_auction_live(db):
    soon = _sale(db, OPEN_SALE, 2)
    later = _sale(db, OVER_SALE, 6)
    _lot(db, soon, "auto-soon", 30)
    _lot(db, later, "auto-later", 180)
    live.auto_start(db, datetime.utcnow())
    db.expire_all()
    assert (db.get(models.Auction, soon.id).live, db.get(models.Auction, soon.id).live_auto) == (True, True)
    assert db.get(models.Auction, later.id).live is not True


def test_switching_off_by_hand_keeps_it_off(db):
    a = _sale(db, OPEN_SALE, 2)
    _lot(db, a, "auto-optout", 30)
    live.auto_start(db, datetime.utcnow())
    r = client.post(f"/auctions/{a.id}/live", params={"live": False})
    assert r.status_code == 200 and r.json()["live"] is False
    live.auto_start(db, datetime.utcnow())
    db.expire_all()
    assert db.get(models.Auction, a.id).live is False, "the hour rule switched it back on"
    # Switching it on by hand again clears the opt-out.
    client.post(f"/auctions/{a.id}/live", params={"live": True})
    db.expire_all()
    got = db.get(models.Auction, a.id)
    assert (got.live, got.live_auto, got.live_opt_out) == (True, False, False)
