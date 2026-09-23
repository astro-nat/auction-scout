"""The kept price trail and the endpoints that read it (`pytest -m db`).

These exist so the WRONG numbers survive. est_resale is overwritten in
place, so before this a comp set that overshot tenfold left no trace - and
that is the only evidence that says a filter is missing. The Swarovski
members' gift priced at $370 against a real value near $25 is the case
they were built for.

test_price_log covers the tiering. This covers the write, and the three
readers built on top of it.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.services import price_log

pytestmark = pytest.mark.db

client = TestClient(app)
TEST_HIBID = 999999974


@pytest.fixture
def lot():
    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="price-api-test",
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    row = models.Lot(lot_id="pa-1", title="Swarovski SCS Crystal Egg Gift",
                     auction_id=auction.id, current_bid=4)
    db.add(row)
    db.flush()
    db.add(models.Enrichment(lot_id=row.id, status="success", est_resale=80))
    db.commit()
    lot_db_id, lot_key = row.id, row.lot_id
    yield db, lot_db_id, lot_key
    db.rollback()          # a failed test must still clean up after itself
    db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id == lot_db_id).delete(synchronize_session=False)
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id == lot_db_id).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id == lot_db_id).delete(
        synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == auction.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _swarovski_trail(lot_db_id):
    """What the pipeline now records for that lot: comps overshot, the
    audit rejected the number and supplied its own."""
    price_log.record(lot_db_id, 370.0, method="comps",
                     price_source="sold (SoldComps)", comp_count=20,
                     chosen=False, rejected=True,
                     note="audit: SCS eggs typically sell for $80-150",
                     query="Swarovski 2023 SCS Crystal Egg Golden Topaz")
    price_log.record(lot_db_id, 80.0, method="audit",
                     price_source="audit-corrected",
                     note="SCS eggs typically sell for $80-150")


def test_a_rejected_number_is_kept_not_discarded(lot):
    """The whole point. Overwriting est_resale threw this away every time."""
    db, lot_db_id, _ = lot
    _swarovski_trail(lot_db_id)
    rows = (db.query(models.PriceObservation)
              .filter(models.PriceObservation.lot_id == lot_db_id)
              .order_by(models.PriceObservation.id).all())
    assert [float(r.value) for r in rows] == [370.0, 80.0]
    assert rows[0].rejected is True and rows[1].rejected is False


def test_the_query_that_caused_a_bad_match_is_recorded(lot):
    """The field that matters for fixing the algorithm: not that $370 was
    wrong, but what was asked to produce it."""
    db, lot_db_id, _ = lot
    _swarovski_trail(lot_db_id)
    bad = (db.query(models.PriceObservation)
             .filter(models.PriceObservation.lot_id == lot_db_id,
                     models.PriceObservation.rejected.is_(True)).one())
    assert "SCS Crystal Egg" in bad.query


def test_history_endpoint_returns_the_trail_newest_first(lot):
    db, lot_db_id, lot_key = lot
    _swarovski_trail(lot_db_id)
    r = client.get(f"/prices/history/{lot_key}")
    assert r.status_code == 200
    body = r.json()
    assert body["current"] == 80.0
    values = [o["value"] for o in body["observations"]]
    assert values == [80.0, 370.0]


def test_history_of_an_unknown_lot_is_a_404():
    assert client.get("/prices/history/no-such-lot").status_code == 404


def test_disagreements_surfaces_the_gap_and_its_query(lot):
    """A 4.6x spread between two methods is the signal worth reading."""
    db, lot_db_id, lot_key = lot
    _swarovski_trail(lot_db_id)
    r = client.get("/prices/disagreements?hours=24&min_ratio=2")
    assert r.status_code == 200
    ours = [x for x in r.json()["lots"] if x["lot_id"] == lot_key]
    assert ours, "a 370-vs-80 disagreement was not reported"
    assert ours[0]["ratio"] == pytest.approx(4.6, abs=0.1)
    assert any("SCS Crystal Egg" in (o["query"] or "")
               for o in ours[0]["observations"])


def test_agreeing_methods_are_not_a_disagreement(lot):
    db, lot_db_id, lot_key = lot
    price_log.record(lot_db_id, 100.0, method="comps", price_source="sold (SoldComps)")
    price_log.record(lot_db_id, 105.0, method="audit", price_source="audit-corrected")
    r = client.get("/prices/disagreements?hours=24&min_ratio=2")
    assert not [x for x in r.json()["lots"] if x["lot_id"] == lot_key]


def test_a_single_observation_is_not_a_disagreement(lot):
    """Nothing to disagree with."""
    db, lot_db_id, lot_key = lot
    price_log.record(lot_db_id, 100.0, method="comps", price_source="sold (SoldComps)")
    r = client.get("/prices/disagreements?hours=24&min_ratio=2")
    assert not [x for x in r.json()["lots"] if x["lot_id"] == lot_key]


def test_evidence_mix_reports_a_reject_rate_per_tier(lot):
    """A tier that is usually rejected is one to stop trusting."""
    db, lot_db_id, _ = lot
    _swarovski_trail(lot_db_id)
    r = client.get("/prices/evidence-mix?hours=24")
    assert r.status_code == 200
    mix = r.json()["by_evidence"]
    # The window is global, so other rows may be counted too; assert only
    # what this lot must have contributed.
    assert "sold" in mix and "audit" in mix
    assert mix["sold"]["rejected"] >= 1
    assert 0 < mix["sold"]["reject_rate"] <= 1
    assert mix["audit"]["produced"] >= 1


def test_a_write_with_no_lot_is_dropped_quietly():
    """Unsaved lots turn up in tests and in half-built rows; a failed
    INSERT is not worth the noise, and never worth an exception."""
    price_log.record(None, 10.0, method="comps")      # must not raise


def _empty_row(lot_db_id):
    """What the guards write when a lookup comes back with nothing: no
    value, method 'empty', not chosen. New today, and the readers were
    written before it existed."""
    price_log.record(lot_db_id, None, method="empty", chosen=False,
                     note="lookup returned nothing; kept $80.00 (sold (SoldComps))",
                     query="Swarovski 2023 SCS Crystal Egg Golden Topaz")


def test_an_empty_row_shows_in_history_with_no_value(lot):
    db, lot_db_id, lot_key = lot
    _swarovski_trail(lot_db_id)
    _empty_row(lot_db_id)
    body = client.get(f"/prices/history/{lot_key}").json()
    assert body["current"] == 80.0                    # the kept value, untouched
    newest = body["observations"][0]
    assert newest["method"] == "empty" and newest["value"] is None
    assert "kept $80.00" in newest["note"]


def test_an_empty_row_is_not_a_disagreement(lot):
    """No value means nothing to disagree with. It must neither create a
    disagreement on its own nor break the ratio maths for the real ones."""
    db, lot_db_id, lot_key = lot
    _swarovski_trail(lot_db_id)
    _empty_row(lot_db_id)
    r = client.get("/prices/disagreements?hours=24&min_ratio=2")
    assert r.status_code == 200
    ours = [x for x in r.json()["lots"] if x["lot_id"] == lot_key]
    assert ours and ours[0]["ratio"] == pytest.approx(4.6, abs=0.1)
    assert all(o["value"] is not None for o in ours[0]["observations"])


def test_an_empty_row_gets_its_own_evidence_bucket_and_no_average(lot):
    """The mix groups by evidence and averages value; a bucket of NULLs
    must come out as a count with avg_value None, not a 500."""
    db, lot_db_id, _ = lot
    _empty_row(lot_db_id)
    r = client.get("/prices/evidence-mix?hours=24")
    assert r.status_code == 200
    mix = r.json()["by_evidence"]
    assert "empty" in mix
    assert mix["empty"]["produced"] >= 1
    assert mix["empty"]["avg_value"] is None
