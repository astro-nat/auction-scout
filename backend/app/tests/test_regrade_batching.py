"""Regrade commits in batches, and a lost batch is loud (`pytest -m db`).

From a real incident: run_regrade held all ~1,245 rows for one commit at the
end while run_reprice committed the same enrichment rows per-lot. The
regrade's commit lost the race silently — no exception, no log line — and 81
verdicts stayed wrong until a manual re-run. The contract now: a commit
conflict costs at most one batch, every other batch lands, and the loss is
logged with a row count.
"""

import logging

import pytest
from sqlalchemy.exc import OperationalError

from app import models
from app.database import SessionLocal
from app.workers import enrich

pytestmark = pytest.mark.db

TEST_HIBID = 999999952

# _apply_roi only ever writes "GOLD MINE", "PASS", or None — so a row still
# holding the sentinel after a run is a row whose regrade was not committed,
# regardless of what the live ROI target computes.
SENTINEL = "STALE-TEST-VERDICT"


class FlakySession:
    """Delegates to a real session, but the chosen commits fail the way a
    lost race does — after the ORM has flushed, out of commit() itself."""

    def __init__(self, real, fail_on: set[int]):
        self._real = real
        self._fail_on = fail_on
        self.commits = 0

    def commit(self):
        self.commits += 1
        if self.commits in self._fail_on:
            self._real.rollback()
            raise OperationalError("could not serialize access", None,
                                   Exception("concurrent update"))
        self._real.commit()

    def __getattr__(self, name):
        return getattr(self._real, name)


def _cleanup(db):
    lot_ids = [r[0] for r in
               db.query(models.Lot.id)
                 .join(models.Auction)
                 .filter(models.Auction.hibid_id == TEST_HIBID).all()]
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(lot_ids)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id.in_(lot_ids)).delete(
        synchronize_session=False)
    db.query(models.Auction).filter(
        models.Auction.hibid_id == TEST_HIBID).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def seeded():
    """Five enriched lots whose roi_status is a sentinel _apply_roi never
    emits, so 'was this row's regrade committed' is a plain equality check."""
    db = SessionLocal()
    _cleanup(db)
    auction = models.Auction(hibid_id=TEST_HIBID, name="regrade-batch-test",
                             source="Local Pickup")
    db.add(auction)
    db.flush()
    ids = []
    for rank in range(5):
        lot = models.Lot(lot_id=f"rgb-{rank}", title=f"regrade lot {rank}",
                         auction_id=auction.id, current_bid=5,
                         source="Local Pickup", logistics_ease="EASY")
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(
            lot_id=lot.id, status="success", est_resale=500, comp_count=3,
            verdict="normal wear and tear", roi_status=SENTINEL))
        ids.append(lot.id)
    db.commit()
    yield ids
    _cleanup(db)
    db.close()


def _load_rows(db, ids):
    """The same shape run_regrade feeds the loop, restricted to our lots."""
    return (db.query(models.Lot)
              .filter(models.Lot.id.in_(ids))
              .order_by(models.Lot.id)
              .all())


def _statuses(ids):
    db = SessionLocal()
    got = {e.lot_id: e.roi_status for e in
           db.query(models.Enrichment)
             .filter(models.Enrichment.lot_id.in_(ids)).all()}
    db.close()
    return [got[i] for i in ids]


def test_a_failed_commit_loses_one_batch_not_the_run(seeded, monkeypatch, caplog):
    monkeypatch.setattr(enrich, "REGRADE_BATCH", 2)
    db = FlakySession(SessionLocal(), fail_on={2})
    try:
        with caplog.at_level(logging.WARNING, logger="app.workers.enrich"):
            changed, lost = enrich._regrade_rows(db, _load_rows(db, seeded))
    finally:
        db.close()

    # Batches: rows 1-2 committed, rows 3-4 lost the race, row 5 committed.
    assert _statuses(seeded)[:2] != [SENTINEL, SENTINEL]
    assert _statuses(seeded)[2:4] == [SENTINEL, SENTINEL]
    assert _statuses(seeded)[4] != SENTINEL
    assert changed == 3
    assert lost == 2
    # The whole point of the incident: the loss must be in the log.
    assert any("Regrade commit failed" in r.message for r in caplog.records)


def test_a_clean_run_commits_every_batch(seeded, monkeypatch):
    monkeypatch.setattr(enrich, "REGRADE_BATCH", 2)
    db = FlakySession(SessionLocal(), fail_on=set())
    try:
        changed, lost = enrich._regrade_rows(db, _load_rows(db, seeded))
    finally:
        db.close()

    assert all(s != SENTINEL for s in _statuses(seeded))
    assert (changed, lost) == (5, 0)
    assert db.commits == 3, "5 rows at batch size 2 is 3 commits (2+2+1)"


def test_default_batch_still_commits_a_short_run(seeded):
    """Fewer rows than one batch — the tail commit must still fire."""
    db = SessionLocal()
    try:
        changed, lost = enrich._regrade_rows(db, _load_rows(db, seeded))
    finally:
        db.close()

    assert all(s != SENTINEL for s in _statuses(seeded))
    assert (changed, lost) == (5, 0)
