"""The audit sweep (`pytest -m db`): every unchecked gold gets its second
opinion, and nothing else does.

Regrades and bid refreshes mint golds by arithmetic alone, so a GOLD MINE
badge can stand on a value no auditor ever saw — Bacliff finished a full
reprice with 15 of 19 golds unaudited. The sweep's whole job is picking the
right rows; the audit itself is proven in test_gold_check. _verify_gold is
monkeypatched to a recorder, so no model spend and no image downloads.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import models
from app.main import app
from app.workers import enrich

pytestmark = pytest.mark.db

client = TestClient(app)

TEST_HIBID_OPEN = 999999964
TEST_HIBID_CLOSED = 999999965
PREFIX = "pytest-auditsweep-"


@pytest.fixture()
def auctions():
    db = SessionLocal()
    open_a = models.Auction(hibid_id=TEST_HIBID_OPEN, name="PYTEST sweep open",
                            closing_date=datetime.now() + timedelta(days=2),
                            imported_at=datetime.now())
    closed_a = models.Auction(hibid_id=TEST_HIBID_CLOSED, name="PYTEST sweep closed",
                              closing_date=datetime.now() - timedelta(days=1),
                              imported_at=datetime.now())
    db.add_all([open_a, closed_a])
    db.commit()
    yield open_a.id, closed_a.id
    db2 = SessionLocal()
    ids = [r[0] for r in db2.query(models.Lot.id)
                            .filter(models.Lot.lot_id.like(f"{PREFIX}%")).all()]
    db2.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
    db2.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
    db2.query(models.Auction).filter(models.Auction.hibid_id.in_(
        [TEST_HIBID_OPEN, TEST_HIBID_CLOSED])).delete(synchronize_session=False)
    db2.commit()
    db2.close()


def _insert(auction_id, suffix, *, roi_status="GOLD MINE", gold_check=None,
            hidden=False, roi_reason=None, profit=None):
    db = SessionLocal()
    lot = models.Lot(lot_id=f"{PREFIX}{suffix}", auction_id=auction_id,
                     title=f"pytest {suffix}", status="OPEN", hidden=hidden)
    db.add(lot)
    db.flush()
    db.add(models.Enrichment(lot_id=lot.id, status="success", est_resale=100,
                             roi_status=roi_status, gold_check=gold_check,
                             roi_reason=roi_reason, profit=profit))
    db.commit()
    lot_id = lot.id
    db.close()
    return lot_id


@pytest.fixture()
def sweep(monkeypatch):
    """Run the sweep with _verify_gold recorded; returns OUR lots it visited
    as (lot_id, candidate) pairs."""
    def run():
        visited = []
        monkeypatch.setattr(
            enrich, "_verify_gold",
            lambda db, lot, e, candidate=False: visited.append(
                (lot.lot_id, candidate)))
        enrich.run_audit_sweep()
        return [v for v in visited if v[0].startswith(PREFIX)]
    return run


def test_only_the_unchecked_gold_is_audited(auctions, sweep):
    open_a, _ = auctions
    _insert(open_a, "unchecked")
    _insert(open_a, "confirmed", gold_check="confirmed")
    _insert(open_a, "corrected", gold_check="corrected")
    _insert(open_a, "pass", roi_status="PASS")
    assert sweep() == [(f"{PREFIX}unchecked", False)]


def test_thin_lots_with_profit_become_promotion_candidates(auctions, sweep):
    open_a, _ = auctions
    _insert(open_a, "thin-rich", roi_status="PASS", profit=40,
            roi_reason="only 0 comps — the badge needs 2 agreeing")
    _insert(open_a, "thin-poor", roi_status="PASS", profit=4,
            roi_reason="only 0 comps — the badge needs 2 agreeing")
    _insert(open_a, "rich-but-demoted-reason", roi_status="PASS", profit=40,
            roi_reason="audit demoted the value (see its note)")
    assert sweep() == [(f"{PREFIX}thin-rich", True)]


def test_closed_auctions_and_hidden_lots_are_left_alone(auctions, sweep):
    open_a, closed_a = auctions
    _insert(closed_a, "closed-gold")
    _insert(open_a, "hidden-gold", hidden=True)
    assert sweep() == []


def test_endpoint_counts_what_the_sweep_will_visit(auctions, monkeypatch):
    """Differential: one new unchecked gold raises the endpoint's count by
    exactly one. jobs.enqueue is stubbed so no real job lands in the shared
    dev DB for the local worker to run (and bill) for real."""
    from app.routers import enrichment as enrichment_router
    monkeypatch.setattr(enrichment_router.jobs, "enqueue",
                        lambda *a, **k: "pytest-job")
    monkeypatch.setattr(enrichment_router.jobs, "has_pending",
                        lambda kind: False)
    open_a, _ = auctions
    before = client.post("/lots/audit-golds").json()
    _insert(open_a, "endpoint-gold")
    # A thin candidate must be counted too — the endpoint's count gates the
    # enqueue, and a 0 here silently skipped every promotion candidate.
    _insert(open_a, "endpoint-thin", roi_status="PASS", profit=40,
            roi_reason="only 0 comps — the badge needs 2 agreeing")
    after = client.post("/lots/audit-golds").json()
    assert after["auditing"] - before["auditing"] == 2
