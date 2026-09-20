"""Single-auction import queues on the worker instead of blocking the
request — the change that lets a 1,200-lot catalog import without a
timeout, a lost connection, or a closed tab killing it mid-save.

jobs is faked at the router's seam, so nothing real is enqueued and the
local worker can't race the assertions.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import models
from app.routers import auctions as auctions_router
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)

TEST_HIBID = 999999995


@pytest.fixture()
def auction_id():
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="PYTEST queue-import sale",
                       closing_date=datetime.now() + timedelta(days=2),
                       imported_at=datetime.now())
    db.add(a)
    db.commit()
    aid = a.id
    db.close()
    yield aid
    db2 = SessionLocal()
    db2.query(models.Auction).filter(
        models.Auction.hibid_id == TEST_HIBID).delete(synchronize_session=False)
    db2.commit()
    db2.close()


def test_import_enqueues_a_bulk_of_one(auction_id, monkeypatch):
    enqueued = []
    monkeypatch.setattr(auctions_router.jobs, "has_pending", lambda kind: False)
    monkeypatch.setattr(auctions_router.jobs, "enqueue",
                        lambda kind, label, total=None, payload=None:
                        enqueued.append({"kind": kind, "payload": payload}))
    r = client.post(f"/auctions/{auction_id}/import?category_id=40252")
    assert r.status_code == 202
    assert r.json()["queued"] is True
    assert enqueued == [{"kind": "import-all",
                         "payload": {"auction_ids": [auction_id],
                                     "category_id": 40252}}]


def test_import_defers_to_a_running_bulk(auction_id, monkeypatch):
    monkeypatch.setattr(auctions_router.jobs, "has_pending", lambda kind: True)
    monkeypatch.setattr(auctions_router.jobs, "enqueue",
                        lambda *a, **k: pytest.fail("enqueued under a running bulk"))
    r = client.post(f"/auctions/{auction_id}/import")
    assert r.json() == {"auction_id": auction_id, "queued": False,
                        "already_running": True}


def test_unknown_auction_is_a_404(monkeypatch):
    monkeypatch.setattr(auctions_router.jobs, "has_pending", lambda kind: False)
    assert client.post("/auctions/99999999/import").status_code == 404
