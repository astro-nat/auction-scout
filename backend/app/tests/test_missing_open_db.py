"""'Import N missing' counts open lots not on file (`pytest -m db`)."""

import pytest

from app import models
from app.database import SessionLocal
from app.workers.import_all import open_missing

pytestmark = pytest.mark.db
SALE = 999999871


@pytest.fixture
def db():
    s = SessionLocal()

    def clean():
        for a in s.query(models.Auction).filter(models.Auction.hibid_id == SALE).all():
            s.query(models.Lot).filter(models.Lot.auction_id == a.id).delete()
            s.delete(a)
        s.commit()
    clean()
    yield s
    clean()
    s.close()


def test_counts_open_lots_not_on_file_and_skips_house_notices(db):
    a = models.Auction(hibid_id=SALE, name="Missing test", source="Local Pickup")
    db.add(a)
    db.commit()
    for lid in ("m-1", "m-2"):
        db.add(models.Lot(lot_id=lid, auction_id=a.id, title=lid, status="OPEN"))
    db.commit()
    fetched = [
        {"lot_id": "m-1", "title": "Drill"},              # on file
        {"lot_id": "m-2", "title": "Saw"},                # on file
        {"lot_id": "m-3", "title": "Lamp"},               # open, not on file
        {"lot_id": "m-4", "title": "Pickup Process & Hours"},  # a notice, never imported
    ]
    # Closed lots never reach this: fetch_lots returns open lots only, so
    # the hundreds that closed during a staggered sale don't count.
    assert open_missing(db, a, fetched) == 1
