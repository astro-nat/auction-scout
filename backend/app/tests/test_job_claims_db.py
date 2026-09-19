"""Job claiming against real Postgres (`pytest -m db` inside the container).

A job row that outlives its worker is the failure that cost us most: the
status bar shows progress that never advances, the kind stays blocked
against restarting, and the only cure was a redeploy. A lapsed heartbeat is
what tells a dead job from a merely slow one, and claiming has to be atomic
because phase 1 puts several processes in front of the same rows.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services import jobs

pytestmark = pytest.mark.db


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.query(models.Job).filter(models.Job.label == "claim-test").delete()
    s.commit()
    s.close()


@pytest.fixture
def dead_job(db):
    """A job whose worker never came back."""
    jid = jobs.start("reprice", "claim-test", total=10, payload={"lot_ids": [1, 2, 3]})
    db.query(models.Job).filter(models.Job.id == jid).update({"heartbeat_at": None})
    db.commit()
    return jid


@pytest.fixture
def clean_jobs():
    def _wipe():
        s = SessionLocal()
        s.query(models.Job).filter(models.Job.label == "queue-test").delete()
        s.commit()
        s.close()
    _wipe()
    yield
    _wipe()


def _stale_ids():
    return {r["id"] for r in jobs.stale()}


def test_a_running_job_is_not_mistaken_for_a_dead_one(db):
    jid = jobs.start("reprice", "claim-test", payload={"lot_ids": [1]})
    assert jid not in _stale_ids()


def test_a_job_that_stopped_reporting_is_found(dead_job):
    assert dead_job in _stale_ids()


def test_only_one_process_can_claim_the_same_job(dead_job):
    """The whole point: phase 1 has several processes seeing this row."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        wins = list(pool.map(lambda _: jobs.claim(dead_job), range(8)))
    assert sum(wins) == 1


def test_claiming_records_the_owner_and_revives_the_row(dead_job, db):
    assert jobs.claim(dead_job) is True
    row = db.query(models.Job).filter(models.Job.id == dead_job).one()
    db.refresh(row)
    assert row.claimed_by == jobs.WORKER_ID
    assert row.state == "running"
    assert dead_job not in _stale_ids()


def test_a_live_job_cannot_be_stolen(dead_job):
    assert jobs.claim(dead_job) is True
    assert jobs.claim(dead_job) is False


def test_reporting_progress_counts_as_a_heartbeat(dead_job):
    """run_reprice calls update() per lot, so liveness is free."""
    assert dead_job in _stale_ids()
    jobs.update(dead_job, current=5)
    assert dead_job not in _stale_ids()


def test_an_empty_update_still_proves_liveness(dead_job):
    """A caller with nothing to report is still saying it's alive."""
    assert dead_job in _stale_ids()
    jobs.update(dead_job)
    assert dead_job not in _stale_ids()


def test_non_resumable_kinds_are_not_restartable():
    """scan/import ran inside a request handler that is long gone."""
    assert "scan" not in jobs.RESUMABLE_KINDS
    assert "import" not in jobs.RESUMABLE_KINDS
    assert set(jobs.RESUMABLE_KINDS) == {"reprice", "ship-analysis",
                                         "bid-refresh", "import-all"}


def _backdate(job_id, seconds, db):
    """Pretend this job last reported `seconds` ago."""
    db.query(models.Job).filter(models.Job.id == job_id).update(
        {"heartbeat_at": datetime.now(timezone.utc) - timedelta(seconds=seconds)})
    db.commit()


def test_a_quiet_bid_refresh_is_declared_dead_sooner_than_a_reprice(clean_jobs):
    """One threshold for everything meant 15 minutes for all of them, pinned
    to the slowest. A bid refresh heartbeats per auction and takes seconds."""
    db = SessionLocal()
    quick = jobs.enqueue("bid-refresh", "queue-test", payload={"auction_ids": [1]})
    slow = jobs.enqueue("reprice", "queue-test", payload={"lot_ids": [1]})
    for jid in (quick, slow):
        db.query(models.Job).filter(models.Job.id == jid).update({"state": "running"})
    db.commit()
    # Six minutes of silence: past the bid-refresh threshold, well inside
    # the reprice one.
    _backdate(quick, 360, db)
    _backdate(slow, 360, db)
    stale_ids = {r["id"] for r in jobs.stale()}
    db.close()
    assert quick in stale_ids, "bid refresh still looked alive after 6 minutes"
    assert slow not in stale_ids, "reprice reaped while legitimately slow"


def test_a_slow_reprice_is_left_alone(clean_jobs):
    """run_reprice heartbeats once per lot, and one lot can sit through
    several 40s comp lookups across four query variants."""
    db = SessionLocal()
    jid = jobs.enqueue("reprice", "queue-test", payload={"lot_ids": [1]})
    db.query(models.Job).filter(models.Job.id == jid).update({"state": "running"})
    db.commit()
    _backdate(jid, 240, db)          # four minutes mid-lot
    stale_ids = {r["id"] for r in jobs.stale()}
    db.close()
    assert jid not in stale_ids


def test_a_dead_heavy_job_stops_blocking_new_ones_at_its_own_threshold(clean_jobs):
    """The heavy slot is held only while the holder is actually alive."""
    db = SessionLocal()
    dead = jobs.enqueue("bid-refresh", "queue-test", payload={"auction_ids": [1]})
    db.query(models.Job).filter(models.Job.id == dead).update({"state": "running"})
    db.commit()
    _backdate(dead, 360, db)
    db.close()
    jobs.enqueue("reprice", "queue-test", payload={"lot_ids": [1]})
    assert jobs.claim_pending() is not None, "dead bid-refresh held the slot shut"


def test_a_job_with_no_heartbeat_at_all_is_not_counted_as_alive(clean_jobs):
    """NULL is neither fresh nor stale-by-age; it must not read as alive."""
    db = SessionLocal()
    jid = jobs.enqueue("bid-refresh", "queue-test", payload={"auction_ids": [1]})
    db.query(models.Job).filter(models.Job.id == jid).update(
        {"state": "running", "heartbeat_at": None})
    db.commit()
    db.close()
    assert jid in {r["id"] for r in jobs.stale()}
    jobs.enqueue("reprice", "queue-test", payload={"lot_ids": [1]})
    assert jobs.claim_pending() is not None
