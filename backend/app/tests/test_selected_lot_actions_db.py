"""Bid refresh and shipping, run on a selection of lots (`pytest -m db`).

Asked for as: "when I select items I want the option to refresh Bid and
calculate shipping based on the auction details."

Both jobs are per-auction underneath - HiBid serves a whole catalogue at a
time, and shipping terms are one page for all of a sale's lots - so the
endpoints turn a lot selection into the sales behind it, and say plainly how
many lots that actually moves.

The shipping half only means anything because the estimate is now spent: the
job re-costs the lots of any sale whose figure moved. Before this it stored a
number nothing ever read, so every lot stayed costed at the flat default
however the terms read.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from sqlalchemy.orm import joinedload

from app.services import jobs
from app.workers import enrich

pytestmark = pytest.mark.db

client = TestClient(app)
HIBID_A = 999999871
HIBID_B = 999999872
VINTED_NAME = "sel-vinted"
JOB_LABELS = ("Refreshing current bids", "Reading shipping terms per auction")


def _purge(db):
    names = ["sel-house-a", "sel-house-b", VINTED_NAME]
    for a in db.query(models.Auction).filter(models.Auction.name.in_(names)).all():
        ids = [r[0] for r in db.query(models.Lot.id)
                               .filter(models.Lot.auction_id == a.id).all()]
        if ids:
            db.query(models.Enrichment).filter(
                models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
        db.query(models.Lot).filter(models.Lot.auction_id == a.id).delete(
            synchronize_session=False)
    (db.query(models.Auction).filter(models.Auction.name.in_(names))
       .delete(synchronize_session=False))
    # The rows these endpoints queue outlive the request, and a leftover one
    # makes the next test's already_queued read true.
    (db.query(models.Job).filter(models.Job.label.in_(JOB_LABELS))
       .delete(synchronize_session=False))
    db.commit()


def _lot(db, auction, lot_id, resale=None, ease="EASY"):
    lot = models.Lot(lot_id=lot_id, title=f"Widget {lot_id}",
                     auction_id=auction.id, current_bid=10, next_bid=11,
                     status="OPEN", logistics_ease=ease, source="Ship")
    db.add(lot)
    db.flush()
    if resale is not None:
        db.add(models.Enrichment(lot_id=lot.id, status="success",
                                 est_resale=resale, user_overrides=[]))
    return lot


@pytest.fixture
def sale():
    scrub = SessionLocal()
    _purge(scrub)
    scrub.close()
    db = SessionLocal()
    # Four days out: outside the bid-refresh window the auction-level button
    # uses, which is the point of one of the tests below.
    soon = datetime.now() + timedelta(days=4)
    a = models.Auction(hibid_id=HIBID_A, name="sel-house-a", source="Ship",
                       buyer_premium_mult=1.15, closing_date=soon,
                       imported_at=datetime.now(timezone.utc))
    b = models.Auction(hibid_id=HIBID_B, name="sel-house-b", source="Ship",
                       buyer_premium_mult=1.15, closing_date=soon,
                       imported_at=datetime.now(timezone.utc))
    # No hibid_id: nothing here has a bid feed or a terms page to read.
    v = models.Auction(name=VINTED_NAME, source="Vinted",
                       buyer_premium_mult=1.0,
                       imported_at=datetime.now(timezone.utc))
    db.add_all([a, b, v])
    db.flush()
    _lot(db, a, "sel-a1", resale=200)
    _lot(db, a, "sel-a2", resale=200)
    _lot(db, a, "sel-a3")                 # unpriced: nothing to re-cost
    _lot(db, b, "sel-b1", resale=200)
    _lot(db, v, "sel-v1", resale=200)
    db.commit()
    # Grade them once, so every lot starts costed at the flat default. The
    # tests below are about what MOVES that cost, which needs a real number
    # to move from - a fresh Enrichment row carries no all_in_cost at all.
    for lot in db.query(models.Lot).options(
            joinedload(models.Lot.enrichment),
            joinedload(models.Lot.auction)).filter(
                models.Lot.lot_id.in_(["sel-a1", "sel-a2", "sel-b1", "sel-v1"])).all():
        enrich._apply_roi(lot, lot.enrichment)
    db.commit()
    yield db, a, b, v
    db.rollback()
    _purge(db)
    db.close()


def _post(path, lot_ids, **params):
    r = client.post(path, json={"lot_ids": lot_ids}, params=params)
    assert r.status_code in (200, 202), r.text
    return r.json()


def _queued(db, kind):
    return (db.query(models.Job)
              .filter(models.Job.kind == kind,
                      models.Job.label.in_(JOB_LABELS)).all())


BOTH = ["/lots/refresh-bids", "/lots/analyze-shipping"]


# --- turning a lot selection into the sales behind it ---------------------

@pytest.mark.parametrize("path", BOTH)
def test_lots_from_one_sale_are_one_unit_of_work(path, sale):
    got = _post(path, ["sel-a1", "sel-a2"], dry_run=True)
    assert got["auctions"] == 1
    assert got["selected"] == 2


@pytest.mark.parametrize("path", BOTH)
def test_lots_from_two_sales_are_two(path, sale):
    got = _post(path, ["sel-a1", "sel-b1"], dry_run=True)
    assert got["auctions"] == 2
    assert set(got["auction_names"]) == {"sel-house-a", "sel-house-b"}


@pytest.mark.parametrize("path", BOTH)
def test_it_says_how_many_lots_actually_move(path, sale):
    """Ticking one lot of a three-lot sale refreshes all three. Reporting "1"
    would be a lie the user only discovers afterwards."""
    got = _post(path, ["sel-a1"], dry_run=True)
    assert got["selected"] == 1
    assert got["lots_affected"] == 3


@pytest.mark.parametrize("path", BOTH)
def test_a_sale_with_no_bid_feed_is_reported_not_dropped(path, sale):
    """A Vinted closet has no HiBid catalogue and no terms page. Skipping it
    in silence would leave the user wondering which half worked."""
    got = _post(path, ["sel-a1", "sel-v1"], dry_run=True)
    assert got["auctions"] == 1
    assert got["skipped_not_hibid"] == 1
    assert got["covered"] == 1


@pytest.mark.parametrize("path", BOTH)
def test_a_selection_with_nothing_reachable_queues_nothing(path, sale):
    got = _post(path, ["sel-v1"])
    assert got["queued"] is False
    assert got["reason"]


@pytest.mark.parametrize("path", BOTH)
def test_a_dry_run_queues_nothing(path, sale):
    db, *_ = sale
    kind = "bid-refresh" if "refresh" in path else "ship-analysis"
    _post(path, ["sel-a1"], dry_run=True)
    assert _queued(db, kind) == []


def test_refreshing_bids_queues_the_sales_and_not_the_lots(sale):
    db, a, _b, _v = sale
    got = _post("/lots/refresh-bids", ["sel-a1", "sel-a2"])
    assert got["queued"] is True
    rows = _queued(db, "bid-refresh")
    assert len(rows) == 1
    assert rows[0].payload["auction_ids"] == [a.id]


def test_shipping_queues_the_sales_behind_the_selection(sale):
    db, a, b, _v = sale
    got = _post("/lots/analyze-shipping", ["sel-a1", "sel-b1"])
    assert got["queued"] is True
    rows = _queued(db, "ship-analysis")
    assert sorted(rows[0].payload["auction_ids"]) == sorted([a.id, b.id])


def test_a_selection_ignores_the_refresh_window(sale):
    """The auction-level button only covers sales closing soon. Ticking a lot
    is an explicit request for that lot's number, so a sale four days out -
    outside the window - still refreshes."""
    got = _post("/lots/refresh-bids", ["sel-a1"])
    assert got["queued"] is True and got["auctions"] == 1


@pytest.mark.parametrize("path,kind", list(zip(BOTH, ["bid-refresh", "ship-analysis"])))
def test_a_second_selection_is_reported_but_not_refused(path, kind, sale):
    """A different selection is different work. Refusing it would be wrong;
    saying nothing would let a second shipping read spend the money twice
    without the dialog mentioning it."""
    first = _post(path, ["sel-a1"])
    assert first["already_queued"] is False
    second = _post(path, ["sel-b1"], dry_run=True)
    assert second["already_queued"] is True
    db, *_ = sale
    assert len(_queued(db, kind)) == 1, "the dry run still queued nothing"


# --- the shipping read now costs something -------------------------------

def _stub_read(monkeypatch, auctions, cost):
    """No network and no AI: what is under test is what the app does with
    the answer, not how it gets one."""
    meta = {a.hibid_id: {"ship_text": f"Flat ${cost} per item",
                         "terms_text": "Standard terms"} for a in auctions}

    async def _coro():
        return meta
    monkeypatch.setattr(enrich.hibid, "fetch_auction_meta",
                        lambda http, ids: _coro())
    # _call_with_retry hands back the PARSED reply, not raw text.
    monkeypatch.setattr(enrich, "_call_with_retry",
                        lambda fn: {"cost_estimate": cost,
                                    "summary": f"Flat ${cost} per item"})


def test_the_shipping_it_reads_lands_on_the_auction(monkeypatch, sale):
    db, a, _b, _v = sale
    _stub_read(monkeypatch, [a], 40.0)
    enrich.run_ship_analysis([a.id])
    db.expire_all()
    assert db.query(models.Auction).filter(
        models.Auction.id == a.id).first().ship_cost_estimate == 40.0


def test_reading_shipping_recosts_the_lots(monkeypatch, sale):
    """The bug this closes: the estimate was stored and nothing spent it, so
    a lot stayed costed at the flat default however the terms read."""
    db, a, _b, _v = sale
    lot = db.query(models.Lot).filter(models.Lot.lot_id == "sel-a1").first()
    before = float(lot.enrichment.all_in_cost or 0)
    assert before > 0, "the fixture lot should already be costed"

    _stub_read(monkeypatch, [a], 40.0)          # vs DEFAULT_INBOUND_SHIP of 15
    enrich.run_ship_analysis([a.id])

    db.expire_all()
    after = float(db.query(models.Lot)
                    .filter(models.Lot.lot_id == "sel-a1").first()
                    .enrichment.all_in_cost)
    assert after > before, f"cost did not follow the shipping read ({before} -> {after})"
    assert after - before == pytest.approx(
        (40.0 - enrich.DEFAULT_INBOUND_SHIP) * enrich.INBOUND_TIER_MULT["EASY"])


def test_every_lot_of_the_sale_is_recosted_not_only_a_ticked_one(monkeypatch, sale):
    """Two lots of one sale must not end up showing two different shipping
    assumptions side by side."""
    db, a, _b, _v = sale
    _stub_read(monkeypatch, [a], 40.0)
    enrich.run_ship_analysis([a.id])
    db.expire_all()
    costs = {l.lot_id: float(l.enrichment.all_in_cost)
             for l in db.query(models.Lot)
                        .filter(models.Lot.auction_id == a.id).all()
             if l.enrichment and l.enrichment.all_in_cost is not None}
    assert costs["sel-a1"] == costs["sel-a2"]


def test_a_sale_left_out_of_the_run_keeps_its_old_cost(monkeypatch, sale):
    db, a, b, _v = sale
    kept = float(db.query(models.Lot).filter(models.Lot.lot_id == "sel-b1")
                   .first().enrichment.all_in_cost)
    _stub_read(monkeypatch, [a], 40.0)
    enrich.run_ship_analysis([a.id])
    db.expire_all()
    assert float(db.query(models.Lot).filter(models.Lot.lot_id == "sel-b1")
                   .first().enrichment.all_in_cost) == kept


def test_an_unchanged_estimate_recosts_nothing(monkeypatch, sale):
    """A run that learns the number it already had should not churn through
    the inventory - which is why the re-cost is scoped to sales whose figure
    actually moved."""
    db, a, _b, _v = sale
    _stub_read(monkeypatch, [a], 40.0)
    enrich.run_ship_analysis([a.id])
    db.expire_all()
    settled = float(db.query(models.Lot).filter(models.Lot.lot_id == "sel-a1")
                      .first().enrichment.all_in_cost)

    seen = []
    real = enrich._regrade_rows
    monkeypatch.setattr(enrich, "_regrade_rows",
                        lambda db_, rows, job=None: (seen.append(len(rows))
                                                     or real(db_, rows, job)))
    enrich.run_ship_analysis([a.id])            # same stub, same answer
    assert seen == [], "re-costed lots whose shipping had not changed"
    db.expire_all()
    assert float(db.query(models.Lot).filter(models.Lot.lot_id == "sel-a1")
                   .first().enrichment.all_in_cost) == settled


def test_an_unpriced_lot_is_left_alone(monkeypatch, sale):
    """Nothing to re-cost without a value, and inventing one here would be a
    pricing decision made by a shipping job."""
    db, a, _b, _v = sale
    _stub_read(monkeypatch, [a], 40.0)
    enrich.run_ship_analysis([a.id])
    db.expire_all()
    lot = db.query(models.Lot).filter(models.Lot.lot_id == "sel-a3").first()
    assert lot.enrichment is None
