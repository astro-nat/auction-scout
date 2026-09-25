"""GET /queue (`pytest -m db`): what is left, job by job and lot by lot.

Every test row is stamped as already claimed (a fresh claimed_at and
heartbeat), so the dev worker running beside the suite never picks one up
and spends real API calls on it.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)
TEST_HIBID = 999999976
JOB_ID = "pytestqueue01"


@pytest.fixture
def setup():
    db = SessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    auction = models.Auction(hibid_id=TEST_HIBID, name="queue-test sale",
                             imported_at=now)
    db.add(auction)
    db.flush()
    lots = []
    for n in range(4):
        lot = models.Lot(lot_id=f"q-{n}", title=f"Queue Widget {n}",
                         auction_id=auction.id, current_bid=1)
        db.add(lot)
        db.flush()
        lots.append(lot)
    # Lots 0-2 waiting to be priced, in a deliberate claim order: lot 2 was
    # queued first, then 0 and 1 in one batch ranked 1, 0. Lot 3 is done.
    stamps = {2: (now - timedelta(minutes=5), 0), 1: (now, 0), 0: (now, 1)}
    for n, lot in enumerate(lots):
        if n in stamps:
            at, rank = stamps[n]
            db.add(models.Enrichment(lot_id=lot.id, status="queued", queued_task="enrich",
                                     queued_at=at, queue_rank=rank, claimed_at=now,
                                     progress="looking up comps" if n == 2 else None,
                                     user_overrides=[]))
        else:
            db.add(models.Enrichment(lot_id=lot.id, status="success", user_overrides=[]))
    ids = [l.id for l in lots]
    db.add(models.Job(id=JOB_ID, kind="reprice", label="Re-pricing lots",
                      current=1, total=4, state="running", claimed_by="pytest:0",
                      heartbeat_at=now, payload={"lot_ids": ids}))
    db.commit()
    yield ids
    db.rollback()
    db.query(models.Job).filter(models.Job.id == JOB_ID).delete(synchronize_session=False)
    db.query(models.Enrichment).filter(models.Enrichment.lot_id.in_(ids)).delete(
        synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == auction.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _read():
    r = client.get("/queue")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_job_says_how_much_is_left_and_what_is_next(setup):
    job = next(j for j in _read()["jobs"] if j["id"] == JOB_ID)
    assert (job["current"], job["total"], job["remaining"]) == (1, 4, 3)
    # The first lot is done; the rest are named in the job's own order.
    assert [n["name"] for n in job["next_up"]] == [
        "Queue Widget 1", "Queue Widget 2", "Queue Widget 3"]
    assert all(isinstance(n["id"], int) for n in job["next_up"])
    assert job["next_up_more"] == 0


def test_waiting_lots_are_listed_in_the_order_the_worker_takes_them(setup):
    lots = _read()["lots"]
    ours = [l for l in lots["items"] if l["lot_id"].startswith("q-")]
    assert [l["lot_id"] for l in ours] == ["q-2", "q-1", "q-0"]
    assert ours[0]["stage"] == "looking up comps"         # being worked now
    assert ours[1]["stage"] is None                        # waiting
    assert ours[0]["auction_name"] == "queue-test sale"
    assert "q-3" not in [l["lot_id"] for l in lots["items"]]   # done, not queued
    assert lots["total"] >= 3


# --- reordering ----------------------------------------------------------

def test_moved_puts_the_target_where_asked():
    from app.routers.queue import moved
    ids = [1, 2, 3, 4]
    assert moved(ids, 3, "top") == [3, 1, 2, 4]
    assert moved(ids, 3, "up") == [1, 3, 2, 4]
    assert moved(ids, 3, "down") == [1, 2, 4, 3]
    assert moved(ids, 3, "bottom") == [1, 2, 4, 3]
    assert moved(ids, 1, "up") == ids              # already first
    assert moved(ids, 4, "down") == ids            # already last
    assert moved(ids, 9, "top") == ids             # not in line


def test_a_waiting_job_moves_in_line(setup):
    """Jobs made pending and cancelled, so the dev worker never claims them;
    the order logic doesn't care about the flag."""
    db = SessionLocal()
    made = []
    try:
        for n in range(3):
            j = models.Job(id=f"pytestmove{n}", kind="reprice", label=f"move {n}",
                           state="pending", cancelled=True, total=1,
                           started_at=datetime.now() + timedelta(seconds=n))
            db.add(j)
            made.append(j.id)
        db.commit()
        r = client.post("/queue/jobs/pytestmove2/move", params={"to": "top"})
        assert r.status_code == 200, r.text
        order = [i for i in r.json()["order"] if i.startswith("pytestmove")]
        assert order == ["pytestmove2", "pytestmove0", "pytestmove1"]
        listed = [j["id"] for j in _read()["jobs"] if j["id"].startswith("pytestmove")]
        assert listed == order
        assert client.post(f"/queue/jobs/{JOB_ID}/move", params={"to": "top"}).status_code == 409
    finally:
        db.query(models.Job).filter(models.Job.id.in_(made)).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_a_waiting_lot_moves_to_the_top(setup):
    """setup's lots are stamped as claimed (in flight), which keeps the dev
    worker off them - and in-flight lots can't be moved. Unclaim two of
    them for the move, and hold their title twin in flight: the worker
    never takes a lot whose same-title twin it is already working."""
    ids = setup
    db = SessionLocal()
    try:
        rows = {e.lot_id: e for e in db.query(models.Enrichment)
                                         .filter(models.Enrichment.lot_id.in_(ids)).all()}
        lots = {l.id: l for l in db.query(models.Lot).filter(models.Lot.id.in_(ids)).all()}
        for i in ids:
            lots[i].title = "Queue Move Twin Widget Deluxe"
        rows[ids[0]].claimed_at = None
        rows[ids[1]].claimed_at = None
        db.commit()
        r = client.post(f"/queue/lots/{ids[0]}/move", params={"to": "top"})
        assert r.status_code == 200, r.text
        waiting = [l["lot_db_id"] for l in _read()["lots"]["items"]
                   if l["lot_db_id"] in (ids[0], ids[1])]
        assert waiting == [ids[0], ids[1]]
        r = client.post(f"/queue/lots/{ids[0]}/move", params={"to": "down"})
        waiting = [l["lot_db_id"] for l in _read()["lots"]["items"]
                   if l["lot_db_id"] in (ids[0], ids[1])]
        assert waiting == [ids[1], ids[0]]
        held = client.post(f"/queue/lots/{ids[2]}/move", params={"to": "top"})
        assert held.status_code == 409                 # in flight: not movable
    finally:
        db.close()
