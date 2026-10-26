"""The Watched tab's count matches what the view can show (`pytest -m db`).

"My Watched Items (6)" sat over an empty list: six watched lots the user had
hidden, from sales that closed weeks earlier.
"""

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app

pytestmark = pytest.mark.db
client = TestClient(app)
SALE = 999999893


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


def test_hidden_and_closed_watched_lots_are_not_counted(db):
    a = models.Auction(hibid_id=SALE, name="Count test", source="Local Pickup")
    db.add(a)
    db.commit()
    rows = [("wc-open", "OPEN", False), ("wc-hidden", "OPEN", True),
            ("wc-closed", "CLOSED", False), ("wc-gone", "CLOSED_SHOW_STATUS", True)]
    for lid, status, hidden in rows:
        db.add(models.Lot(lot_id=lid, auction_id=a.id, title=lid, status=status,
                          hidden=hidden, watched=True))
    db.commit()
    r = client.get("/lots/count", params={"watched_only": True, "auction_id": a.id})
    assert r.json()["total"] == 1
    r = client.get("/lots/count", params={"watched_only": True, "auction_id": a.id,
                                          "include_hidden": True, "include_closed": True})
    assert r.json()["total"] == 4
