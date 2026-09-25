"""Single-auction import queues on the worker instead of blocking the
request — the change that lets a 1,200-lot catalog import without a
timeout, a lost connection, or a closed tab killing it mid-save.

It also never refuses: a second import while one is already running
enqueues too, rather than popping "already running" and making the user
re-click once the first finishes. The worker serialises heavy jobs
itself, in order — see services/jobs.claim_pending — so queuing a second
one is simply queuing, not a race.

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
    monkeypatch.setattr(auctions_router.jobs, "enqueue",
                        lambda kind, label, total=None, payload=None:
                        enqueued.append({"kind": kind, "payload": payload}))
    r = client.post(f"/auctions/{auction_id}/import?category_id=40252")
    assert r.status_code == 202
    assert r.json()["queued"] is True
    assert enqueued == [{"kind": "import-all",
                         "payload": {"auction_ids": [auction_id],
                                     "category_id": 40252,
                                     "bolo_only": False,
                                     "search_text": ""}}]


def test_a_bolo_filtered_import_says_so_in_its_payload(auction_id, monkeypatch):
    """The worker dispatches with only the auction list and the job id, so
    anything else has to ride the payload or it resets to its default."""
    enqueued = []
    monkeypatch.setattr(auctions_router.jobs, "enqueue",
                        lambda kind, label, total=None, payload=None:
                        enqueued.append({"kind": kind, "payload": payload}))
    r = client.post(f"/auctions/{auction_id}/import?bolo_only=true")
    assert r.status_code == 202
    assert enqueued[0]["payload"]["bolo_only"] is True


def test_the_two_import_filters_compose(auction_id, monkeypatch):
    """"Every BOLO match in the antiques category" is both at once."""
    enqueued = []
    monkeypatch.setattr(auctions_router.jobs, "enqueue",
                        lambda kind, label, total=None, payload=None:
                        enqueued.append({"kind": kind, "payload": payload}))
    client.post(f"/auctions/{auction_id}/import?category_id=40252&bolo_only=true")
    assert enqueued[0]["payload"] == {"auction_ids": [auction_id],
                                      "category_id": 40252,
                                      "bolo_only": True,
                                      "search_text": ""}


def test_a_keyword_filtered_import_says_so_in_its_payload(auction_id, monkeypatch):
    enqueued = []
    monkeypatch.setattr(auctions_router.jobs, "enqueue",
                        lambda kind, label, total=None, payload=None:
                        enqueued.append({"kind": kind, "payload": payload}))
    r = client.post(f"/auctions/{auction_id}/import?search_text=pyrex")
    assert r.status_code == 202
    assert enqueued[0]["payload"]["search_text"] == "pyrex"


def test_all_three_import_filters_compose(auction_id, monkeypatch):
    enqueued = []
    monkeypatch.setattr(auctions_router.jobs, "enqueue",
                        lambda kind, label, total=None, payload=None:
                        enqueued.append({"kind": kind, "payload": payload}))
    client.post(f"/auctions/{auction_id}/import"
               "?category_id=40252&bolo_only=true&search_text=pyrex")
    assert enqueued[0]["payload"] == {"auction_ids": [auction_id],
                                      "category_id": 40252,
                                      "bolo_only": True,
                                      "search_text": "pyrex"}


def test_a_second_import_queues_behind_a_running_bulk_instead_of_refusing(
        auction_id, monkeypatch):
    """The old behaviour popped 'already running' and made the user
    re-click once the first import finished. The worker already
    serialises heavy jobs itself, in queue order (jobs.claim_pending) — so
    a second import while one is running just needs to be enqueued, not
    refused."""
    enqueued = []
    monkeypatch.setattr(auctions_router.jobs, "enqueue",
                        lambda kind, label, total=None, payload=None:
                        enqueued.append(kind))
    r = client.post(f"/auctions/{auction_id}/import")
    assert r.status_code == 202
    assert r.json()["queued"] is True
    assert enqueued == ["import-all"]


def test_unknown_auction_is_a_404():
    assert client.post("/auctions/99999999/import").status_code == 404
