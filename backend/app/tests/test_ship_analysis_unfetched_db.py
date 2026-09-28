"""A terms page that never arrived is not a house that posted nothing.

The shipping read marked 146 auctions "No shipping details posted" in one
run, and set ship_analyzed_at on every one of them - so the default filter
(analysed auctions are skipped) would never look at them again. Their terms
pages were perfectly readable; the fetch simply brought nothing back.

Recording that as an answer is worse than recording nothing, because it is
both wrong and permanent. An auction the fetch missed now stays unanalysed,
which is what makes the next run pick it up.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.workers import enrich

pytestmark = pytest.mark.db

TEST_HIBID_A = 999999851
TEST_HIBID_B = 999999852


def _purge(db):
    (db.query(models.Auction)
       .filter(models.Auction.hibid_id.in_([TEST_HIBID_A, TEST_HIBID_B]))
       .delete(synchronize_session=False))
    db.commit()


@pytest.fixture
def two_auctions(monkeypatch):
    scrub = SessionLocal()
    _purge(scrub)
    scrub.close()
    db = SessionLocal()
    made = []
    for hib in (TEST_HIBID_A, TEST_HIBID_B):
        a = models.Auction(hibid_id=hib, name=f"ship-test-{hib}", source="Ship",
                           buyer_premium_mult=1.15,
                           closing_date=datetime.now() + timedelta(days=5),
                           imported_at=datetime.now(timezone.utc))
        db.add(a)
        made.append(a)
    db.commit()
    ids = [a.id for a in made]
    # No AI, no network: the read is not what is under test here.
    # _call_with_retry hands back the PARSED reply, not the raw text.
    monkeypatch.setattr(enrich, "_call_with_retry",
                        lambda fn: {"cost_estimate": 18.5, "summary": "Flat $18.50"})
    yield db, ids
    db.rollback()
    _purge(db)
    db.close()


def _run(monkeypatch, meta):
    monkeypatch.setattr(enrich.hibid, "fetch_auction_meta",
                        lambda http, ids: _async(meta))
    return meta


def _async(value):
    async def _coro():
        return value
    return _coro()


def test_an_auction_the_fetch_missed_stays_unanalysed(monkeypatch, two_auctions):
    """The whole bug in one case: nothing came back, so nothing is claimed."""
    db, ids = two_auctions
    _run(monkeypatch, {})                       # fetch returned no entries
    enrich.run_ship_analysis(ids)

    db.expire_all()
    for a in db.query(models.Auction).filter(models.Auction.id.in_(ids)):
        assert a.ship_analyzed_at is None, "a missed fetch was recorded as read"
        assert a.ship_summary is None
        assert a.ship_cost_estimate is None


def test_a_house_that_really_posted_nothing_is_recorded(monkeypatch, two_auctions):
    """The entry came back, and it is genuinely empty. That IS an answer."""
    db, ids = two_auctions
    rows = db.query(models.Auction).filter(models.Auction.id.in_(ids)).all()
    _run(monkeypatch, {a.hibid_id: {"ship_text": "", "terms_text": ""} for a in rows})
    enrich.run_ship_analysis(ids)

    db.expire_all()
    for a in db.query(models.Auction).filter(models.Auction.id.in_(ids)):
        assert a.ship_analyzed_at is not None
        assert a.ship_summary == "No shipping details posted"


def test_text_that_arrives_is_read(monkeypatch, two_auctions):
    db, ids = two_auctions
    rows = db.query(models.Auction).filter(models.Auction.id.in_(ids)).all()
    _run(monkeypatch, {a.hibid_id: {"ship_text": "Shipping is a flat $18.50 per item.",
                                    "terms_text": ""} for a in rows})
    enrich.run_ship_analysis(ids)

    db.expire_all()
    for a in db.query(models.Auction).filter(models.Auction.id.in_(ids)):
        assert a.ship_analyzed_at is not None
        assert float(a.ship_cost_estimate) == 18.5


def test_one_missed_auction_does_not_stop_the_others(monkeypatch, two_auctions):
    """A partial fetch is the likely real case, not an all-or-nothing one."""
    db, ids = two_auctions
    rows = sorted(db.query(models.Auction).filter(models.Auction.id.in_(ids)).all(),
                  key=lambda a: a.id)
    _run(monkeypatch, {rows[1].hibid_id: {"ship_text": "Flat $18.50 per item.",
                                          "terms_text": ""}})
    enrich.run_ship_analysis(ids)

    db.expire_all()
    got = {a.id: a for a in db.query(models.Auction).filter(models.Auction.id.in_(ids))}
    assert got[rows[0].id].ship_analyzed_at is None      # missed: retried later
    assert got[rows[1].id].ship_analyzed_at is not None  # read: recorded
