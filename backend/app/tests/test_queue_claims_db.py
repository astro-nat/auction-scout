"""Claiming work out of Postgres (`pytest -m db` inside the container).

Phase 1 puts the workers in their own process, so the database is now the
only thing standing between two processes and the same lot. Both queues are
claimed with SELECT … FOR UPDATE SKIP LOCKED.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services import jobs

pytestmark = pytest.mark.db

TEST_HIBID = 999999951


@pytest.fixture
def seeded():
    """One auction, five lots queued for enrichment, newest batch last."""
    db = SessionLocal()
    db.query(models.Job).filter(models.Job.label == "queue-test").delete()
    auction = models.Auction(hibid_id=TEST_HIBID, name="queue-test")
    db.add(auction)
    db.flush()
    now = datetime.now(timezone.utc)
    ids = []
    for rank in range(5):
        lot = models.Lot(lot_id=f"qt-{rank}", title=f"lot {rank}",
                         auction_id=auction.id)
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, status="queued",
                                 queued_task="enrich", queued_at=now,
                                 queue_rank=rank))
        ids.append(lot.id)
    db.commit()
    yield ids
    lot_ids = [r[0] for r in db.query(models.Lot.id)
                              .filter(models.Lot.auction_id == auction.id).all()]
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(lot_ids)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.auction_id == auction.id).delete(
        synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == auction.id).delete(
        synchronize_session=False)
    db.query(models.Job).filter(models.Job.label == "queue-test").delete()
    db.commit()
    db.close()


def test_lots_are_claimed_in_the_order_the_user_saw_them(seeded):
    """enrich-batch sends the visible rows top-first; that has to survive
    the trip through the database now that another process does the picking."""
    claimed = jobs.claim_lots(3)
    assert [lot_id for lot_id, _ in claimed] == seeded[:3]


def test_a_claimed_lot_is_not_handed_out_twice(seeded):
    first = jobs.claim_lots(3)
    second = jobs.claim_lots(3)
    assert set(i for i, _ in first).isdisjoint(i for i, _ in second)


def test_two_workers_racing_never_share_a_lot(seeded):
    """The whole reason for SKIP LOCKED."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        batches = list(pool.map(lambda _: jobs.claim_lots(2), range(4)))
    taken = [i for b in batches for i, _ in b]
    assert len(taken) == len(set(taken)), "a lot was claimed by two workers"
    assert len(taken) == 5, "every queued lot should be claimed exactly once"


def test_the_task_kind_rides_along(seeded):
    db = SessionLocal()
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id == seeded[0]).update({"queued_task": "inspect"})
    db.commit()
    db.close()
    claimed = dict(jobs.claim_lots(5))
    assert claimed[seeded[0]] == "inspect"
    assert claimed[seeded[1]] == "enrich"


def test_an_abandoned_claim_is_picked_up_again(seeded):
    """A worker killed mid-lot must not strand it forever."""
    jobs.claim_lots(5)
    assert jobs.claim_lots(5) == []
    stale = datetime.now(timezone.utc) - timedelta(seconds=jobs.STALE_JOB_SECONDS + 60)
    db = SessionLocal()
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(seeded)).update(
        {"claimed_at": stale}, synchronize_session=False)
    db.commit()
    db.close()
    assert len(jobs.claim_lots(5)) == 5


def test_claiming_does_not_change_what_the_ui_counts(seeded):
    """/status and the table both count status == 'queued'. A lot being
    worked is still queued from the user's point of view."""
    jobs.claim_lots(5)
    db = SessionLocal()
    still = (db.query(models.Enrichment)
               .filter(models.Enrichment.lot_id.in_(seeded),
                       models.Enrichment.status == "queued").count())
    db.close()
    assert still == 5


def test_enqueue_leaves_the_job_for_someone_else_to_claim():
    jid = jobs.enqueue("reprice", "queue-test", total=3, payload={"lot_ids": [1, 2, 3]})
    db = SessionLocal()
    row = db.query(models.Job).filter(models.Job.id == jid).one()
    assert row.state == "pending" and row.claimed_by is None
    db.close()
    got = jobs.claim_pending()
    assert got["id"] == jid
    assert jobs.claim_pending() is None, "claimed twice"


def test_two_workers_never_claim_the_same_job():
    """Light kinds, so the one-heavy-job-at-a-time limit isn't what's being
    measured here — this is purely about two workers taking the same row."""
    ids = [jobs.enqueue("regrade", "queue-test") for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        got = list(pool.map(lambda _: jobs.claim_pending(), range(4)))
    claimed = [g["id"] for g in got if g]
    assert sorted(claimed) == sorted(ids)
    assert len(claimed) == len(set(claimed))


def _clear_jobs():
    db = SessionLocal()
    db.query(models.Job).filter(models.Job.label == "queue-test").delete()
    db.commit()
    db.close()


@pytest.fixture
def clean_jobs():
    _clear_jobs()
    yield
    _clear_jobs()


def test_only_one_heavy_job_runs_at_a_time(clean_jobs):
    """The limit lives in the claim query, not in a check beside it."""
    jobs.enqueue("reprice", "queue-test", payload={"lot_ids": [1]})
    jobs.enqueue("ship-analysis", "queue-test", payload={"auction_ids": [1]})
    assert jobs.claim_pending() is not None
    assert jobs.claim_pending() is None, "two heavy jobs claimed at once"


def test_a_light_job_is_not_blocked_by_a_heavy_one(clean_jobs):
    """A regrade is seconds of arithmetic with no network — it should never
    wait behind a half-hour reprice."""
    jobs.enqueue("reprice", "queue-test", payload={"lot_ids": [1]})
    jobs.enqueue("regrade", "queue-test")
    assert jobs.claim_pending()["kind"] == "reprice"
    assert jobs.claim_pending()["kind"] == "regrade"


def test_a_heavy_job_whose_worker_died_does_not_hold_the_slot(clean_jobs):
    """Otherwise one dead job blocks every heavy job until the reaper runs."""
    jobs.enqueue("reprice", "queue-test", payload={"lot_ids": [1]})
    first = jobs.claim_pending()
    jobs.enqueue("bid-refresh", "queue-test", payload={"auction_ids": [1]})
    assert jobs.claim_pending() is None          # first one still reporting
    db = SessionLocal()
    db.query(models.Job).filter(models.Job.id == first["id"]).update(
        {"heartbeat_at": None})
    db.commit()
    db.close()
    assert jobs.claim_pending() is not None, "dead job held the slot shut"


def test_concurrent_workers_cannot_both_start_a_heavy_job(clean_jobs):
    for _ in range(4):
        jobs.enqueue("reprice", "queue-test", payload={"lot_ids": [1]})
    with ThreadPoolExecutor(max_workers=4) as pool:
        got = [g for g in pool.map(lambda _: jobs.claim_pending(), range(4)) if g]
    assert len(got) == 1, f"{len(got)} heavy jobs started together"


def test_duplicate_requests_are_refused_but_unrelated_ones_are_not(clean_jobs):
    jobs.enqueue("reprice", "queue-test", payload={"lot_ids": [1]})
    assert jobs.has_pending("reprice") is True
    assert jobs.has_pending("ship-analysis") is False
