"""A re-import backfills a lot's photo list (`pytest -m db`).

The photo list started being kept after thousands of lots were already on
file. Those lots had no way to gain one: a re-import refreshes only the
FRESH fields, and image_urls cannot join that list, because a source that
reports no photos would then wipe what another pass fetched. So the
photos are copied across only when the import actually carries them - and
that is what lets an existing lot gain them.

Found on lot #341, "Zippered CD Storage Case With Music CDs": the AI was
shown one low-resolution thumbnail and could not read a single disc.
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.workers.import_all import save_lots

pytestmark = pytest.mark.db

TEST_HIBID = 999999953
SHOTS = ["https://example.com/a.jpg", "https://example.com/b.jpg",
         "https://example.com/c.jpg"]


def _lot(lot_id="ph-1", bid=2, **over):
    data = {
        "lot_id": lot_id, "title": "Zippered CD Storage Case With Music CDs",
        "category": "General", "description": "", "current_bid": bid,
        "next_bid": bid + 1, "bid_count": 1, "est_cost": round(bid * 1.15, 2),
        "status": "OPEN", "time_left": "2d", "closes_at": None,
        "lot_number": "341", "estimate_low": None, "estimate_high": None,
        "source": "Ship", "logistics_ease": "EASY", "unreachable_pickup": False,
        "lot_link": None, "thumbnail_url": "https://example.com/t.jpg",
        "hd_thumbnail_url": None, "fullsize_url": None, "image_count": 0,
    }
    data.update(over)
    return data


@pytest.fixture
def seeded():
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="photo-backfill-test",
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


def _row(db, lot_id="ph-1"):
    db.expire_all()
    return db.query(models.Lot).filter(models.Lot.lot_id == lot_id).one()


def test_a_lot_imported_without_photos_gains_them_on_re_import(seeded):
    db, a = seeded
    save_lots(db, a, [_lot()])
    assert _row(db).image_urls is None

    save_lots(db, a, [_lot(image_urls=SHOTS, image_count=len(SHOTS))])
    row = _row(db)
    assert row.image_urls == SHOTS
    assert row.image_count == 3


def test_an_import_without_photos_does_not_wipe_the_ones_on_file(seeded):
    """PublicSurplus descriptions and photos are fetched by their own pass;
    a later refresh that carries none must not undo it."""
    db, a = seeded
    save_lots(db, a, [_lot(image_urls=SHOTS, image_count=3)])
    save_lots(db, a, [_lot()])                      # no image_urls key value
    row = _row(db)
    assert row.image_urls == SHOTS
    assert row.image_count == 3


def test_a_brand_new_lot_still_gets_its_photos(seeded):
    db, a = seeded
    save_lots(db, a, [_lot(image_urls=SHOTS, image_count=3)])
    assert _row(db).image_urls == SHOTS


def test_a_shorter_list_replaces_a_longer_one(seeded):
    """The seller deleted photos: what the source reports now is the truth."""
    db, a = seeded
    save_lots(db, a, [_lot(image_urls=SHOTS, image_count=3)])
    save_lots(db, a, [_lot(image_urls=SHOTS[:1], image_count=1)])
    assert _row(db).image_urls == SHOTS[:1]


def test_bids_are_still_refreshed_alongside(seeded):
    db, a = seeded
    save_lots(db, a, [_lot(bid=2)])
    save_lots(db, a, [_lot(bid=9, image_urls=SHOTS, image_count=3)])
    row = _row(db)
    assert float(row.current_bid) == 9
    assert row.image_urls == SHOTS
