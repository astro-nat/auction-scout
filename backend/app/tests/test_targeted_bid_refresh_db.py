"""Refreshing a selection reads those lots, not their catalogues (`-m db`).

Asked for as: "why can't refresh bids only do those lots".

It can. lotSearch has no by-id lookup, but its searchText filter is
selective enough to stand in for one - on a 385-lot sale, searching "1"
returned 4 results and "250" returned 2, with the exact lot on the first
page every time. So a handful of lots costs a handful of small requests
instead of paging a catalogue to reach them, and nothing the user did not
select gets touched.

The catch is what absence means. In a full read, a lot missing from the
results has closed - HiBid soft-closes catalogues lot by lot. In a targeted
read, a lot we could not find might have closed or might just have been
missed by the search, and guessing would mark a live lot closed. So a miss
sends that auction down the full-catalogue path, where absence is evidence.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services import jobs
from app.workers import refresh

pytestmark = pytest.mark.db

HIBID = 999999881


def _purge(db):
    for a in db.query(models.Auction).filter(models.Auction.hibid_id == HIBID).all():
        ids = [r[0] for r in db.query(models.Lot.id)
                               .filter(models.Lot.auction_id == a.id).all()]
        if ids:
            db.query(models.Enrichment).filter(
                models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
        db.query(models.Lot).filter(models.Lot.auction_id == a.id).delete(
            synchronize_session=False)
    (db.query(models.Auction).filter(models.Auction.hibid_id == HIBID)
       .delete(synchronize_session=False))
    db.query(models.Job).filter(models.Job.kind == "bid-refresh").delete(
        synchronize_session=False)
    db.commit()


@pytest.fixture
def sale():
    scrub = SessionLocal()
    _purge(scrub)
    scrub.close()
    db = SessionLocal()
    a = models.Auction(hibid_id=HIBID, name="targeted-refresh-test", source="Ship",
                       buyer_premium_mult=1.15, lot_count=2345,
                       closing_date=datetime.now() + timedelta(days=5),
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.flush()
    for n in ("1", "2", "3"):
        db.add(models.Lot(lot_id=f"tr-{n}", lot_number=n, title=f"Widget {n}",
                          auction_id=a.id, current_bid=5, next_bid=6,
                          bid_count=0, est_cost=5, status="OPEN",
                          logistics_ease="EASY", source="Ship"))
    db.commit()
    yield db, a
    db.rollback()
    _purge(db)
    db.close()


def _fresh(lot_number, bid):
    """One lot in the shape the HiBid parser hands back."""
    return {"lot_id": f"tr-{lot_number}", "lot_number": lot_number,
            "current_bid": bid, "next_bid": bid + 1, "bid_count": 1,
            "est_cost": bid, "status": "OPEN", "time_left": "2d",
            "closes_at": None, "estimate_low": None, "estimate_high": None}


def _run_with(monkeypatch, db, auction, lot_ids, targeted_result, full_result=None):
    """Queue a bid-refresh the way the selection endpoint does, then run it
    the way the worker does - claiming the row, so the payload is in scope."""
    calls = {"targeted": 0, "full": 0}

    async def _by_number(hibid_id, numbers, auction_ctx=None, should_cancel=None):
        calls["targeted"] += 1
        calls["numbers"] = list(numbers)
        return targeted_result

    async def _all(hibid_auction_id, auction_ctx=None, should_cancel=None, **kw):
        calls["full"] += 1
        return full_result if full_result is not None else []

    monkeypatch.setattr(refresh.hibid, "fetch_lots_by_number", _by_number)
    monkeypatch.setattr(refresh.hibid, "fetch_lots", _all)
    job_id = jobs.enqueue("bid-refresh", "Refreshing current bids", total=1,
                          payload={"auction_ids": [auction.id], "lot_ids": lot_ids})
    jobs.claim(job_id)
    refresh.run_bid_refresh([auction.id], resume_job_id=job_id)
    db.expire_all()
    return calls


def _bids(db, auction):
    return {l.lot_id: float(l.current_bid) for l in
            db.query(models.Lot).filter(models.Lot.auction_id == auction.id).all()}


def _statuses(db, auction):
    return {l.lot_id: l.status for l in
            db.query(models.Lot).filter(models.Lot.auction_id == auction.id).all()}


def test_a_selection_reads_only_those_lots(monkeypatch, sale):
    db, a = sale
    calls = _run_with(monkeypatch, db, a, ["tr-1"], ([_fresh("1", 99.0)], []))
    assert calls["targeted"] == 1
    assert calls["full"] == 0, "read a whole catalogue to reach one lot"
    assert calls["numbers"] == ["1"]


def test_it_searches_by_lot_number_not_by_our_own_id(monkeypatch, sale):
    """lotSearch only takes text. The lot NUMBER is what HiBid shows; the
    lot_id on our row is its internal id and would find nothing."""
    db, a = sale
    calls = _run_with(monkeypatch, db, a, ["tr-2", "tr-3"],
                      ([_fresh("2", 50.0), _fresh("3", 60.0)], []))
    assert sorted(calls["numbers"]) == ["2", "3"]


def test_the_selected_lot_gets_its_new_bid(monkeypatch, sale):
    db, a = sale
    _run_with(monkeypatch, db, a, ["tr-1"], ([_fresh("1", 99.0)], []))
    assert _bids(db, a)["tr-1"] == 99.0


def test_the_lots_nobody_selected_are_left_alone(monkeypatch, sale):
    """The whole point of the question: 24 ticked lots should not move 2,345
    rows."""
    db, a = sale
    before = _bids(db, a)
    _run_with(monkeypatch, db, a, ["tr-1"], ([_fresh("1", 99.0)], []))
    after = _bids(db, a)
    assert after["tr-2"] == before["tr-2"]
    assert after["tr-3"] == before["tr-3"]


def test_an_unselected_lot_is_never_marked_closed(monkeypatch, sale):
    """In a full read, absence means closed. A targeted read holds three
    lots; treating everything else as absent would close the catalogue."""
    db, a = sale
    _run_with(monkeypatch, db, a, ["tr-1"], ([_fresh("1", 99.0)], []))
    got = _statuses(db, a)
    assert got["tr-2"] == "OPEN"
    assert got["tr-3"] == "OPEN"


def test_a_lot_the_search_cannot_find_falls_back_to_the_catalogue(monkeypatch, sale):
    """A miss is not evidence of closure - the search may simply have missed
    it - so the auction is read in full, where absence IS evidence."""
    db, a = sale
    calls = _run_with(monkeypatch, db, a, ["tr-1", "tr-2"],
                      ([_fresh("1", 99.0)], ["2"]),          # 2 was not found
                      full_result=[_fresh("1", 99.0), _fresh("2", 12.0),
                                   _fresh("3", 7.0)])
    assert calls["full"] == 1, "a miss did not fall back"
    assert _bids(db, a)["tr-2"] == 12.0


def test_the_fallback_restores_full_read_semantics(monkeypatch, sale):
    """Once it is a full read, a lot missing from the results has closed."""
    db, a = sale
    _run_with(monkeypatch, db, a, ["tr-1", "tr-2"],
              ([_fresh("1", 99.0)], ["2"]),
              full_result=[_fresh("1", 99.0), _fresh("2", 12.0)])   # 3 absent
    assert _statuses(db, a)["tr-3"] == "CLOSED"


def test_no_selection_still_reads_the_catalogue(monkeypatch, sale):
    """The hourly loop and the auction-level button send no lot_ids. In bulk
    the catalogue is the cheaper read, and it is the only one that can say
    which lots have closed."""
    db, a = sale
    calls = _run_with(monkeypatch, db, a, [],
                      ([], []), full_result=[_fresh("1", 3.0), _fresh("2", 4.0),
                                             _fresh("3", 5.0)])
    assert calls["targeted"] == 0
    assert calls["full"] == 1
    assert _bids(db, a)["tr-2"] == 4.0


def test_a_selection_from_another_sale_does_not_target_this_one(monkeypatch, sale):
    """lot_ids covers the whole job; an auction holding none of them is a
    full read, not a targeted read of nothing."""
    db, a = sale
    calls = _run_with(monkeypatch, db, a, ["some-other-sales-lot"],
                      ([], []), full_result=[_fresh("1", 8.0)])
    assert calls["targeted"] == 0
    assert calls["full"] == 1
