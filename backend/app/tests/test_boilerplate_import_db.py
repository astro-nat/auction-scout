"""The house's notices never become lots (`pytest -m db`).

"**RETURNS**" was imported, comped and valued at $56.70; "Pickup Process &
Hours" at $43.11. Dropped at import, they never exist, never cost a lookup
and never need hiding. Rows already on file are left alone - the cleanup
route hides those, because the enrichment rows are real history.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.workers.import_all import save_lots

pytestmark = pytest.mark.db

client = TestClient(app)
TEST_HIBID = 999999921


def _lot(lot_id, title, bid=0):
    return {"lot_id": lot_id, "title": title, "category": "General",
            "description": "", "current_bid": bid, "next_bid": bid + 1,
            "bid_count": 0, "est_cost": 0, "status": "OPEN", "time_left": "2d",
            "closes_at": None, "lot_number": "1", "estimate_low": None,
            "estimate_high": None, "source": "Ship", "logistics_ease": "EASY",
            "unreachable_pickup": False, "lot_link": None,
            "thumbnail_url": None, "hd_thumbnail_url": None,
            "fullsize_url": None, "image_count": 0}


@pytest.fixture
def auction():
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="boilerplate-test", source="Ship",
                       buyer_premium_mult=1.15,
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.commit()
    yield db, a
    ids = [r[0] for r in db.query(models.Lot.id)
                           .filter(models.Lot.auction_id == a.id).all()]
    if ids:
        db.query(models.Enrichment).filter(
            models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.auction_id == a.id).delete(
        synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == a.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _titles(db, a):
    db.expire_all()
    return {r.title for r in db.query(models.Lot)
                               .filter(models.Lot.auction_id == a.id).all()}


def test_a_notice_is_never_created(auction):
    db, a = auction
    created, _, _ = save_lots(db, a, [
        _lot("bp-1", "**RETURNS**"),
        _lot("bp-2", "Pickup Process & Hours"),
        _lot("bp-3", "Griswold No 8 Cast Iron Skillet", bid=5),
    ])
    assert created == 1
    assert _titles(db, a) == {"Griswold No 8 Cast Iron Skillet"}


def test_a_real_lot_sharing_the_words_is_kept(auction):
    """"Master Mystery Box - Please Read" took a $65 bid."""
    db, a = auction
    created, _, _ = save_lots(db, a, [
        _lot("bp-4", "Master Mystery Box - Please Read .........", bid=65),
        _lot("bp-5", "Pickup **PLEASE READ**"),
    ])
    assert created == 1
    assert _titles(db, a) == {"Master Mystery Box - Please Read ........."}


def test_no_enrichment_row_is_made_for_a_notice(auction):
    """The enrichment row is what a pricing pass picks up, so the lookup is
    what this actually saves."""
    db, a = auction
    save_lots(db, a, [_lot("bp-6", "Shipping  **PLEASE READ**")])
    rows = (db.query(models.Enrichment)
              .join(models.Lot, models.Lot.id == models.Enrichment.lot_id)
              .filter(models.Lot.auction_id == a.id).count())
    assert rows == 0


def test_a_notice_already_on_file_is_left_alone_by_import(auction):
    """Import does not reach back and change rows it did not create."""
    db, a = auction
    lot = models.Lot(lot_id="bp-7", title="**RETURNS**", auction_id=a.id,
                     current_bid=0, next_bid=1, status="OPEN",
                     logistics_ease="EASY", source="Ship", hidden=False)
    db.add(lot)
    db.commit()
    save_lots(db, a, [_lot("bp-7", "**RETURNS**")])
    db.expire_all()
    assert db.query(models.Lot).filter(models.Lot.lot_id == "bp-7").one().hidden in (False, None)


def test_the_cleanup_route_hides_the_ones_already_here(auction):
    db, a = auction
    for lid, title in (("bp-8", "**RETURNS**"),
                       ("bp-9", "Payment Options, Credit Card Policy Update"),
                       ("bp-10", "Griswold No 8 Cast Iron Skillet")):
        db.add(models.Lot(lot_id=lid, title=title, auction_id=a.id,
                          current_bid=0, next_bid=1, status="OPEN",
                          logistics_ease="EASY", source="Ship", hidden=False))
    db.commit()

    peek = client.post("/lots/hide-boilerplate", params={"dry_run": True}).json()
    assert peek["changed"] >= 2
    db.expire_all()
    assert not db.query(models.Lot).filter(models.Lot.lot_id == "bp-8").one().hidden

    client.post("/lots/hide-boilerplate")
    db.expire_all()
    rows = {r.lot_id: r.hidden for r in db.query(models.Lot)
                                          .filter(models.Lot.auction_id == a.id).all()}
    assert rows["bp-8"] is True
    assert rows["bp-9"] is True
    assert rows["bp-10"] in (False, None)      # a real lot is untouched
