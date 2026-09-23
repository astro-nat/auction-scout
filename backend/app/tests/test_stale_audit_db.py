"""A rejected number must not keep advising, and a stale audit must not
outlive the number it judged (`pytest -m db`).

The lot that showed both: a 2003 LeBron rookie the vision pass first
identified as the Topps #221 (comps $771), which the audit rejected for a
worn raw copy - correctly - and demoted. Then a re-price identified the
same photo as the far cheaper Upper Deck card ($53.50), and the lot kept
the demotion, kept the note about "the $771 comp", and could never be
audited again, because a lot with any verdict is never re-audited and the
single-lot path never cleared one. Meanwhile, while it was still at $771,
the card showed a struck-through value beside a $326 max bid computed
from that very value.

Two rules. A demoted lot gets no bid guidance - the value stays for
context, the advice goes. And a value change clears the audit that
certified the old value, on the single-lot path as it always did on the
bulk one. The kept-value path (empty lookup) leaves the audit standing:
the number it certified is still the number on display.
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services import pricing
from app.workers import enrich

pytestmark = pytest.mark.db

# The fixture stubs _verify_gold to a no-op so the pipeline tests never call
# the model. The audit-branch tests below need the real one back, with only
# the model call itself stubbed - captured here, before any fixture runs.
_REAL_VERIFY_GOLD = enrich._verify_gold

TEST_HIBID = 999999981
EMPTY = {"est_resale": None, "price_low": None, "price_high": None,
         "comp_count": 0, "price_source": None, "comps": []}
CHEAPER = {"est_resale": 53.5, "price_low": 49.99, "price_high": 187.5,
           "comp_count": 37, "price_source": "sold (SoldComps)", "comps": []}
NOTE = "the $771 comp likely represents a graded high-grade version"


@pytest.fixture
def demoted_lot(monkeypatch):
    """The LeBron lot as it stood: $771 from sold comps, audit demoted,
    queued for a re-price with every paid call stubbed."""
    monkeypatch.setattr(enrich, "_call_text", lambda *a, **k: {
        "enriched_title": "2003 Upper Deck Rookie Exclusive LeBron James #1",
        "verdict": "normal wear and tear", "confident": True,
        "notes": "n", "ship": "EASY"})
    monkeypatch.setattr(enrich, "_download_image", lambda *a, **k: None)
    monkeypatch.setattr(enrich, "_verify_gold", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "verified_title_price", lambda *a, **k: None)

    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="stale-audit-test",
                             buyer_premium_mult=1.15, source="Ship",
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    lot = models.Lot(lot_id="sa-1", title="2003 NBA Draft LeBron James",
                     description="x" * 200, auction_id=auction.id,
                     current_bid=1, next_bid=2, logistics_ease="EASY", source="Ship")
    db.add(lot)
    db.flush()
    db.add(models.Enrichment(
        lot_id=lot.id, status="queued", queued_task="enrich", user_overrides=[],
        enriched_title="2003 NBA Draft LeBron James Rookie Card Topps",
        est_resale=771.02, price_low=625, price_high=894.25, comp_count=33,
        price_source="sold (SoldComps)", gold_check="demoted", gold_check_note=NOTE))
    db.commit()
    lot_id = lot.id
    yield db, lot_id
    db.rollback()
    db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id == lot_id).delete(synchronize_session=False)
    db.query(models.TaskTiming).filter(
        models.TaskTiming.auction_id == auction.id).delete(synchronize_session=False)
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id == lot_id).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id == lot_id).delete(synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == auction.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _enrichment(db, lot_id):
    db.expire_all()
    return db.query(models.Enrichment).filter(models.Enrichment.lot_id == lot_id).one()


def test_a_demoted_lot_gets_no_bid_guidance(demoted_lot):
    """The first symptom: $771 struck through, $326 max bid beside it.
    ROI alone, no re-price - the demotion is on the row already."""
    db, lot_id = demoted_lot
    lot = db.query(models.Lot).filter(models.Lot.id == lot_id).one()
    e = lot.enrichment
    enrich._apply_roi(lot, e)
    assert e.roi_status == "PASS"
    assert e.max_bid is None
    assert e.profit is None
    assert e.est_roi is None
    assert float(e.est_resale) == pytest.approx(771.02)     # context stays
    assert "audit" in (e.roi_reason or "").lower()


def test_a_changed_value_clears_the_stale_audit(demoted_lot, monkeypatch):
    """The second symptom: re-identified and re-priced to $53.50, still
    wearing the note about the $771 comp."""
    db, lot_id = demoted_lot
    monkeypatch.setattr(pricing, "lookup_comps", lambda *a, **k: dict(CHEAPER))
    enrich.run_enrichment(lot_id)
    e = _enrichment(db, lot_id)
    assert e.status == "success", e.error_message
    assert float(e.est_resale) == pytest.approx(53.5)
    assert e.gold_check is None
    assert e.gold_check_note is None
    assert "audit demoted" not in (e.roi_reason or "")


def test_a_kept_value_keeps_its_audit(demoted_lot, monkeypatch):
    """The empty-lookup guard keeps $771 on display, so the audit that
    judged $771 is still the right audit for what is shown."""
    db, lot_id = demoted_lot
    monkeypatch.setattr(pricing, "lookup_comps", lambda *a, **k: dict(EMPTY))
    enrich.run_enrichment(lot_id)
    e = _enrichment(db, lot_id)
    assert float(e.est_resale) == pytest.approx(771.02)
    assert e.gold_check == "demoted"
    assert e.gold_check_note == NOTE
    assert e.max_bid is None, "a kept-but-rejected number still advised a bid"


# --- through the real audit branch, with only the model call stubbed --------

def _fresh_gold(db, lot_id, value=56.0):
    """The lot as the audit finds it: a fresh GOLD MINE, no verdict yet."""
    lot = db.query(models.Lot).filter(models.Lot.id == lot_id).one()
    e = lot.enrichment
    e.gold_check = None
    e.gold_check_note = None
    e.est_resale = value
    e.price_source = "sold (SoldComps)"
    e.comp_count = 36
    enrich._apply_roi(lot, e)
    e.roi_status = "GOLD MINE"
    db.commit()
    return lot, e


def _audit_returns(monkeypatch, reason, realistic_value=None):
    monkeypatch.setattr(enrich, "_verify_gold", _REAL_VERIFY_GOLD)
    monkeypatch.setattr(enrich, "GOLD_CHECK", True)
    payload = {"plausible": False, "reason": reason}
    if realistic_value is not None:
        payload["realistic_value"] = realistic_value
    monkeypatch.setattr(enrich, "_call_with_retry", lambda fn: payload)


def test_an_audit_that_says_the_value_is_too_low_confirms_it(demoted_lot, monkeypatch):
    """The LeBron case after the identity was fixed: $56 from 36 correct
    comps, and the auditor said 'typically sells for $200-$400, not $56'.
    That is an auditor agreeing the number is at least right. It used to
    demote the lot to PASS on a $1 bid - the opposite of its point."""
    db, lot_id = demoted_lot
    lot, e = _fresh_gold(db, lot_id)
    _audit_returns(monkeypatch, "Rookie Exclusives #1 in mint condition typically "
                                "sells for $200-$400+, not $56; undervalued")
    enrich._verify_gold(db, lot, e)
    db.expire_all()
    e = _enrichment(db, lot_id)
    assert e.gold_check == "confirmed"
    assert float(e.est_resale) == pytest.approx(56.0), "raised on one opinion"
    assert e.roi_status != "PASS"
    assert e.max_bid is not None and e.max_bid > 0


def test_an_audit_with_no_number_demotes_and_blanks_the_advice_now(demoted_lot, monkeypatch):
    """Not on the next bid refresh - now. ROI ran before the audit, so the
    demotion branch has to regrade itself or the rejected number's max
    bid stays on the card."""
    db, lot_id = demoted_lot
    lot, e = _fresh_gold(db, lot_id)
    _audit_returns(monkeypatch, "no real resale value; this is a reprint")
    enrich._verify_gold(db, lot, e)
    db.expire_all()
    e = _enrichment(db, lot_id)
    assert e.gold_check == "demoted"
    assert e.roi_status == "PASS"
    assert e.max_bid is None and e.profit is None
    assert float(e.est_resale) == pytest.approx(56.0)        # context stays


def test_an_audit_with_a_lower_number_still_corrects(demoted_lot, monkeypatch):
    """The path that already worked, kept: a figure below the claim
    replaces it and the lot is regraded on the auditor's number."""
    db, lot_id = demoted_lot
    lot, e = _fresh_gold(db, lot_id, value=771.02)
    _audit_returns(monkeypatch, "worn raw copies typically sell $30-150; "
                                "the $771 comp is a graded copy")
    enrich._verify_gold(db, lot, e)
    db.expire_all()
    e = _enrichment(db, lot_id)
    assert e.gold_check == "corrected"
    assert float(e.est_resale) == pytest.approx(30.0)         # the low end, on purpose
    assert e.price_source.startswith("audit-corrected")
    rows = db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id == lot_id).all()
    assert any(r.rejected and r.query for r in rows), "the rejection row lost its query"
