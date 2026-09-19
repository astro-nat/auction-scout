"""The flush must never delete won or watched lots (`pytest -m db`).

A won lot is resale inventory — its enrichment (the paid identification,
comps, resale value) is exactly what's needed to list the item on eBay,
and the lot closing is precisely when it matters. The 2026-09-19
auto-flush deleted 10 just-won webcast lots; these tests pin the guard
that prevents a repeat, for both flush triggers (auction ended, and the
lot's own HiBid status closed).
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import models
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)

TEST_HIBID = 999999951


@pytest.fixture()
def seeded_closed_auction():
    """A closed auction holding one flushable lot and three protected ones,
    plus a won lot whose own status (not its auction) says it's done."""
    db = SessionLocal()
    ended = models.Auction(hibid_id=TEST_HIBID, name="PYTEST won-flush auction",
                           closing_date=datetime.now() - timedelta(hours=2),
                           imported_at=datetime.now())
    open_a = models.Auction(hibid_id=TEST_HIBID + 1, name="PYTEST won-flush open auction",
                            closing_date=datetime.now() + timedelta(days=2),
                            imported_at=datetime.now())
    db.add_all([ended, open_a])
    db.flush()
    specs = [
        # (lot_id suffix, auction, status, won, watched)
        ("plain",       ended,  "SOLD", False, False),   # flushable
        ("won",         ended,  "SOLD", True,  False),   # kept: won
        ("watched",     ended,  "OPEN", False, True),    # kept: watched
        ("won-null",    ended,  None,   True,  False),   # kept: won, NULL status
        ("won-status",  open_a, "CLOSED", True, False),  # kept: won, own status closed
    ]
    ids = {}
    for suffix, auction, status, won, watched in specs:
        lot = models.Lot(lot_id=f"pytest-wonflush-{suffix}", auction_id=auction.id,
                         title=f"pytest {suffix}", status=status,
                         won=won, watched=watched)
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, status="success",
                                 est_resale=120, enriched_title=f"resale {suffix}"))
        ids[suffix] = lot.id
    db.commit()
    yield ended.id, open_a.id, ids
    db2 = SessionLocal()
    lot_ids = list(ids.values())
    db2.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(lot_ids)).delete(synchronize_session=False)
    db2.query(models.Lot).filter(
        models.Lot.id.in_(lot_ids)).delete(synchronize_session=False)
    db2.query(models.Auction).filter(
        models.Auction.hibid_id.in_([TEST_HIBID, TEST_HIBID + 1])).delete(
        synchronize_session=False)
    db2.commit()
    db2.close()
    db.close()


def test_flush_spares_won_and_watched_lots(seeded_closed_auction):
    _, _, ids = seeded_closed_auction
    client.post("/lots/flush-closed")
    db = SessionLocal()
    left = {l.lot_id for l in db.query(models.Lot)
            .filter(models.Lot.id.in_(list(ids.values()))).all()}
    # Enrichment survives with the kept lots — that's the whole point.
    enriched = {e.lot_id for e in db.query(models.Enrichment)
                .filter(models.Enrichment.lot_id.in_(list(ids.values()))).all()}
    db.close()
    assert "pytest-wonflush-plain" not in left        # unprotected → flushed
    assert "pytest-wonflush-won" in left              # auction ended, won
    assert "pytest-wonflush-watched" in left          # auction ended, watched
    assert "pytest-wonflush-won-null" in left         # NULL status, won
    assert "pytest-wonflush-won-status" in left       # own status CLOSED, won
    assert ids["plain"] not in enriched
    for kept in ("won", "watched", "won-null", "won-status"):
        assert ids[kept] in enriched


def test_flush_dry_run_count_excludes_protected(seeded_closed_auction):
    """The confirm dialog quotes the dry-run count — it must not include
    the won/watched lots the real flush would keep."""
    _, _, ids = seeded_closed_auction
    before = client.post("/lots/flush-closed?dry_run=true").json()["lots"]
    db = SessionLocal()
    db.query(models.Lot).filter(models.Lot.id == ids["won"]).update({"won": False})
    db.commit()
    db.close()
    after = client.post("/lots/flush-closed?dry_run=true").json()["lots"]
    assert after == before + 1  # unmarking one won lot exposes exactly it


def test_won_roundtrip(seeded_closed_auction):
    r = client.post("/lots/pytest-wonflush-plain/won?won=true").json()
    assert r["won"] is True
    r = client.post("/lots/pytest-wonflush-plain/won?won=false").json()
    assert r["won"] is False
    assert client.post("/lots/no-such-lot/won").status_code == 404
