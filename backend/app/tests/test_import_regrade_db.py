"""Re-importing must not leave a stale verdict behind (`pytest -m db`).

Found in the inventory: a Swarovski Kris Bear badged GOLD MINE at a $37 bid
against its own $10.58 max bid - three and a half times over the ceiling
the app had calculated. The number was not wrong when it was written; the
bid had moved since, and nothing recomputed.

refresh.py has always regraded after updating bids. import never did, and
import refreshes the same fields.
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.workers.import_all import save_lots

pytestmark = pytest.mark.db

TEST_HIBID = 999999972


def _lot(lot_id, title, bid):
    return {
        "lot_id": lot_id, "title": title, "category": "General",
        "description": "", "current_bid": bid, "next_bid": bid + 1,
        "bid_count": 1, "est_cost": round(bid * 1.15, 2), "status": "OPEN",
        "time_left": "2d", "closes_at": None, "lot_number": "1",
        "estimate_low": None, "estimate_high": None, "source": "Ship",
        "logistics_ease": "EASY", "unreachable_pickup": False,
        "lot_link": None, "thumbnail_url": None, "hd_thumbnail_url": None,
        "fullsize_url": None, "image_count": 0,
    }


@pytest.fixture
def seeded():
    """One lot, priced, cheap enough to be gold at a $2 bid."""
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="regrade-test",
                       buyer_premium_mult=1.15,
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.commit()
    save_lots(db, a, [_lot("rg-1", "Collectible Widget", 2)])
    lot = db.query(models.Lot).filter(models.Lot.lot_id == "rg-1").one()
    lot.enrichment.est_resale = 60
    lot.enrichment.comp_count = 8
    lot.enrichment.price_source = "sold (SoldComps)"
    db.commit()
    from app.workers.enrich import _apply_roi
    _apply_roi(lot, lot.enrichment)
    db.commit()
    yield db, a, lot
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


def test_the_setup_really_is_gold_at_the_low_bid(seeded):
    db, a, lot = seeded
    db.refresh(lot.enrichment)
    assert lot.enrichment.roi_status == "GOLD MINE"
    assert float(lot.enrichment.max_bid) > 2


def test_a_bid_that_blows_past_the_ceiling_loses_the_badge(seeded):
    """The actual failure: the bid moved, the verdict did not."""
    db, a, lot = seeded
    ceiling = float(lot.enrichment.max_bid)
    save_lots(db, a, [_lot("rg-1", "Collectible Widget", ceiling * 4)])
    db.refresh(lot.enrichment)
    assert float(lot.current_bid) > ceiling
    assert lot.enrichment.roi_status == "PASS", (
        "re-import refreshed the bid but left the verdict at the old one")


def test_profit_is_recomputed_not_just_the_verdict(seeded):
    db, a, lot = seeded
    before = float(lot.enrichment.profit)
    save_lots(db, a, [_lot("rg-1", "Collectible Widget", 25)])
    db.refresh(lot.enrichment)
    assert float(lot.enrichment.profit) < before


def test_a_bid_that_does_not_move_is_left_alone(seeded):
    """Regrading is cheap but not free, and an unchanged bid cannot change
    the verdict."""
    db, a, lot = seeded
    db.refresh(lot.enrichment)
    before = (lot.enrichment.roi_status, float(lot.enrichment.profit))
    save_lots(db, a, [_lot("rg-1", "Collectible Widget", 2)])
    db.refresh(lot.enrichment)
    assert (lot.enrichment.roi_status, float(lot.enrichment.profit)) == before


def test_an_unpriced_lot_survives_a_re_import(seeded):
    """No resale value means nothing to regrade; it must not raise."""
    db, a, _ = seeded
    save_lots(db, a, [_lot("rg-2", "Unpriced Thing", 1)])
    save_lots(db, a, [_lot("rg-2", "Unpriced Thing", 9)])
    row = db.query(models.Lot).filter(models.Lot.lot_id == "rg-2").one()
    assert float(row.current_bid) == 9
