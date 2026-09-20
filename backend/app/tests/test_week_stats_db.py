"""Acquisition pacing (`pytest -m db`): /stats/week and the pacing settings.

Every counting rule is proven differentially: read the endpoint, insert one
row, read again, assert the EXACT delta. The shared dev database can hold
anything — deltas make each rule's test independent of it, and a rule that
wrongly includes a row fails loudly instead of hiding inside a >= total.
(The local worker can touch other rows between reads; the window is a few
milliseconds, accepted for a db-marked suite.)

The trusted-week arithmetic mirrors the settlement reconciliations: a won
lot counts at its stored profit unless the audit demoted it, and losses
count against the week — the math that priced the Sterling haul at $223.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import models
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)

TEST_HIBID_OPEN = 999999962
TEST_HIBID_CLOSED = 999999963
PREFIX = "pytest-weekpace-"


def read():
    r = client.get("/stats/week")
    assert r.status_code == 200
    return r.json()


@pytest.fixture()
def auctions():
    """One open and one closed auction to hang lots on; wipes every PREFIX
    lot on the way out so a failed test can't poison the next run."""
    db = SessionLocal()
    open_a = models.Auction(hibid_id=TEST_HIBID_OPEN, name="PYTEST pacing open",
                            closing_date=datetime.now() + timedelta(days=2),
                            imported_at=datetime.now())
    closed_a = models.Auction(hibid_id=TEST_HIBID_CLOSED, name="PYTEST pacing closed",
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


def _insert(auction_id, suffix, *, profit, won_at=None, gold_check=None,
            roi_status="PASS", status="SOLD"):
    db = SessionLocal()
    lot = models.Lot(lot_id=f"{PREFIX}{suffix}", auction_id=auction_id,
                     title=f"pytest {suffix}", status=status,
                     won=won_at is not None, won_at=won_at)
    db.add(lot)
    db.flush()
    db.add(models.Enrichment(lot_id=lot.id, status="success", est_resale=100,
                             profit=profit, gold_check=gold_check,
                             roi_status=roi_status))
    db.commit()
    db.close()


# --- the weekly won number -------------------------------------------------

def test_trusted_win_adds_exactly_its_profit(auctions):
    open_a, _ = auctions
    before = read()
    _insert(open_a, "win", profit=51.37, won_at=datetime.now())
    after = read()
    assert after["won_trusted_profit"] - before["won_trusted_profit"] == pytest.approx(51.37)
    assert after["won_count"] - before["won_count"] == 1


def test_losses_count_against_the_week(auctions):
    open_a, _ = auctions
    before = read()
    _insert(open_a, "loss", profit=-23.11, won_at=datetime.now())
    after = read()
    assert after["won_trusted_profit"] - before["won_trusted_profit"] == pytest.approx(-23.11)


def test_demoted_win_is_counted_but_contributes_nothing(auctions):
    """The audit rejected its value, so its profit is not money you can bank —
    but it IS a win, so the count reflects it."""
    open_a, _ = auctions
    before = read()
    _insert(open_a, "demoted", profit=100.0, won_at=datetime.now(),
            gold_check="demoted")
    after = read()
    assert after["won_trusted_profit"] == pytest.approx(before["won_trusted_profit"])
    assert after["won_count"] - before["won_count"] == 1


def test_last_weeks_win_is_invisible(auctions):
    open_a, _ = auctions
    before = read()
    _insert(open_a, "old", profit=77.0, won_at=datetime.now() - timedelta(days=8))
    after = read()
    assert after["won_trusted_profit"] == pytest.approx(before["won_trusted_profit"])
    assert after["won_count"] == before["won_count"]


def test_week_boundary_is_inclusive_at_the_start(auctions):
    """A win stamped exactly at week_start belongs to this week; one second
    earlier belongs to last week."""
    open_a, _ = auctions
    week_start = datetime.fromisoformat(read()["week_start"])
    before = read()
    _insert(open_a, "on-boundary", profit=10.0, won_at=week_start)
    mid = read()
    assert mid["won_trusted_profit"] - before["won_trusted_profit"] == pytest.approx(10.0)
    _insert(open_a, "before-boundary", profit=10.0,
            won_at=week_start - timedelta(seconds=1))
    after = read()
    assert after["won_trusted_profit"] == pytest.approx(mid["won_trusted_profit"])


# --- what's still on the board ---------------------------------------------

def test_open_gold_mine_adds_to_available(auctions):
    open_a, _ = auctions
    before = read()
    _insert(open_a, "gold-open", profit=61.29, roi_status="GOLD MINE",
            status="OPEN")
    after = read()
    assert (after["available_gold_profit"] - before["available_gold_profit"]
            == pytest.approx(61.29))


def test_pass_lot_and_closed_auction_gold_add_nothing(auctions):
    open_a, closed_a = auctions
    before = read()
    _insert(open_a, "pass-open", profit=61.29, roi_status="PASS", status="OPEN")
    _insert(closed_a, "gold-closed", profit=61.29, roi_status="GOLD MINE",
            status="OPEN")
    after = read()
    assert after["available_gold_profit"] == pytest.approx(before["available_gold_profit"])


# --- the pacing settings ---------------------------------------------------

def test_pacing_settings_roundtrip_without_regrade():
    before = client.get("/settings").json()
    try:
        r = client.patch("/settings", json={"weekly_goal_usd": 750,
                                            "auction_floor_usd": 150}).json()
        assert r["regrading"] == 0        # pacing is display-only: no regrade
        got = client.get("/settings").json()
        assert got["weekly_goal_usd"] == 750
        assert got["auction_floor_usd"] == 150
        assert got["target_roi_pct"] == before["target_roi_pct"]
    finally:
        client.patch("/settings", json={"weekly_goal_usd": before["weekly_goal_usd"],
                                        "auction_floor_usd": before["auction_floor_usd"]})


def test_settings_validation_rejects_garbage_without_side_effects():
    before = client.get("/settings").json()
    assert client.patch("/settings", json={}).status_code == 422
    assert client.patch("/settings", json={"auction_floor_usd": -5}).status_code == 422
    assert client.patch("/settings", json={"weekly_goal_usd": "lots"}).status_code == 422
    assert client.patch("/settings",
                        json={"target_roi_pct": 0}).status_code == 422
    # The half-save trap: one valid field + one invalid must save NEITHER.
    assert client.patch("/settings", json={"weekly_goal_usd": 333,
                                           "auction_floor_usd": -5}).status_code == 422
    assert client.get("/settings").json() == before   # nothing half-saved
