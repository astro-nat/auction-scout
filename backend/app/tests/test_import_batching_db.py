"""save_lots stays batched (`pytest -m db`).

This is a performance regression test, written as a correctness one.

save_lots used to cost 227ms per lot in production against 2ms locally.
None of that was computation - it was round trips: a SELECT per lot to ask
whether it existed, then a flush per new lot to get its id back for the
enrichment FK. Against a database one hop away that is invisible. Across a
network it was 110x, and a 1,182-lot auction sat in this function for four
and a half minutes with the progress bar reading 1182/1182.

Counting statements is the only way to catch that coming back. A timing
assertion would pass locally forever, because locally the slow version was
fast. So these tests count SQL instead, and the chunking test runs enough
lots to cross the 1,000-id boundary in the IN list.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import event

from app import models
from app.database import SessionLocal, engine
from app.workers.import_all import save_lots

pytestmark = pytest.mark.db

TEST_HIBID = 999999972


def _lot(lot_id, title="Makita XPH12Z Hammer Drill"):
    """The shape hibid.fetch_lots returns, trimmed to what save_lots uses."""
    return {
        "lot_id": lot_id, "title": title, "category": "General",
        "description": "", "current_bid": 1, "next_bid": 2, "bid_count": 0,
        "est_cost": 1.15, "status": "OPEN", "time_left": "2d", "closes_at": None,
        "lot_number": None, "estimate_low": None, "estimate_high": None,
        "source": "Ship", "logistics_ease": "EASY", "unreachable_pickup": False,
        "lot_link": None, "thumbnail_url": None, "hd_thumbnail_url": None,
        "fullsize_url": None, "image_count": 0,
    }


class _Counter:
    """Count SQL statements issued while the block runs."""

    def __init__(self):
        self.statements = []

    def __enter__(self):
        def before(conn, cursor, statement, params, context, many):
            self.statements.append(statement)
        self._before = before
        event.listen(engine, "before_cursor_execute", before)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self._before)
        return False

    def count(self, *fragments):
        return sum(1 for s in self.statements
                   if all(f.lower() in s.lower() for f in fragments))


@pytest.fixture
def auction():
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="import-batching-test",
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.commit()
    yield db, a
    # A failing test can leave the session in a poisoned transaction (an
    # IntegrityError does exactly that), and then teardown cannot run and
    # 2,500 junk lots stay in the dev database. Always roll back first.
    db.rollback()
    lot_ids = [r[0] for r in db.query(models.Lot.id)
                              .filter(models.Lot.auction_id == a.id).all()]
    if lot_ids:
        db.query(models.Enrichment).filter(
            models.Enrichment.lot_id.in_(lot_ids)).delete(synchronize_session=False)
        db.query(models.Lot).filter(
            models.Lot.id.in_(lot_ids)).delete(synchronize_session=False)
    db.query(models.Auction).filter(
        models.Auction.id == a.id).delete(synchronize_session=False)
    db.commit()
    db.close()


def test_a_fresh_import_does_not_select_once_per_lot(auction):
    """The original defect. 200 lots used to mean 200 existence SELECTs."""
    db, a = auction
    lots = [_lot(f"ib-{i}") for i in range(200)]
    with _Counter() as c:
        created, _, _ = save_lots(db, a, lots)
    assert created == 200
    selects = c.count("select", "from lots")
    assert selects <= 3, (
        f"{selects} SELECTs against lots for 200 lots - the per-lot existence "
        "check is back. This is what cost 227ms per lot in production.")


def test_new_lots_are_inserted_in_bulk_not_one_flush_each(auction):
    """The second half of it: a flush per new lot, to read back the id the
    enrichment FK needs. db.add_all + one flush gets the same ids."""
    db, a = auction
    lots = [_lot(f"ib-b{i}") for i in range(150)]
    with _Counter() as c:
        save_lots(db, a, lots)
    inserts = c.count("insert into lots")
    assert inserts <= 5, f"{inserts} INSERT statements for 150 lots"


def test_every_new_lot_still_gets_its_enrichment_row(auction):
    """Bulk insert is only correct if the ids it hands back are real. A
    lot with no enrichment row never becomes eligible for pricing, so it
    would simply never appear - a silent loss, not an error."""
    db, a = auction
    lots = [_lot(f"ib-e{i}") for i in range(120)]
    save_lots(db, a, lots)
    rows = db.query(models.Lot).filter(models.Lot.auction_id == a.id).all()
    assert len(rows) == 120
    missing = [r.lot_id for r in rows if r.enrichment is None]
    assert not missing, f"{len(missing)} lots saved with no enrichment row"
    assert all(r.enrichment.status == "pending" for r in rows)


def test_the_existence_lookup_chunks_above_a_thousand_ids(auction):
    """The IN list is chunked at 1,000. A 3,000-lot auction is a real size
    and an unchunked IN of that width is where drivers start to fail."""
    db, a = auction
    lots = [_lot(f"ib-c{i}") for i in range(2500)]
    created, _, _ = save_lots(db, a, lots)
    assert created == 2500

    # Re-import the same set: now every id is on file, so the lookup has to
    # find all 2,500 across chunks. A chunking bug shows up here as lots
    # re-created rather than updated.
    with _Counter() as c:
        created2, updated2, _ = save_lots(db, a, lots)
    assert (created2, updated2) == (0, 2500), (
        "re-import did not recognise existing lots - the chunked IN lookup "
        "is dropping ids past the first chunk")
    lookups = c.count("select", "from lots", "in (")
    assert lookups >= 3, "2,500 ids should take at least three chunks"


def test_a_re_import_creates_no_duplicates(auction):
    """Idempotence is the contract; the batching rewrite is the risk to it."""
    db, a = auction
    lots = [_lot(f"ib-d{i}") for i in range(50)]
    save_lots(db, a, lots)
    save_lots(db, a, lots)
    assert db.query(models.Lot).filter(
        models.Lot.auction_id == a.id).count() == 50


def test_cancelling_keeps_the_lots_already_decided(auction):
    """Cancel is checked every 200 lots. Whatever was decided before the
    stop is committed - a cancelled import should not lose its work."""
    db, a = auction
    lots = [_lot(f"ib-x{i}") for i in range(1000)]
    calls = {"n": 0}

    def should_cancel():
        calls["n"] += 1
        return calls["n"] >= 2          # stop at the second checkpoint

    created, _, cancelled = save_lots(db, a, lots, should_cancel=should_cancel)
    assert cancelled is True
    assert 0 < created < 1000
    saved = db.query(models.Lot).filter(models.Lot.auction_id == a.id).count()
    assert saved == created
