"""Zero-bid lots must not manufacture 900% ROIs.

Auctions open at $0-1; dividing a real resale by a $1 bid put 400-900%
"gold mines" at the top of the ROI sort while meaning nothing. Grading
assumes a hammer of at least MIN_ASSUMED_BID.
"""

import pytest

from app import models
from app.workers import enrich


def _lot(current_bid, next_bid, resale=200.0):
    lot = models.Lot(lot_id="1", title="Widget", logistics_ease="NEUTRAL",
                     source="Local Pickup", current_bid=current_bid,
                     next_bid=next_bid,
                     auction=models.Auction(name="a", source="Local Pickup",
                                            buyer_premium_mult=1.15))
    e = models.Enrichment(lot_id=1, est_resale=resale, comp_count=5,
                          user_overrides=[])
    lot.enrichment = e
    return lot, e


def test_zero_bid_grades_like_min_assumed_bid():
    lot0, e0 = _lot(0, 0)
    enrich._apply_roi(lot0, e0)
    lot5, e5 = _lot(enrich.MIN_ASSUMED_BID, 0)
    enrich._apply_roi(lot5, e5)
    assert e0.est_roi == pytest.approx(e5.est_roi)
    assert e0.est_roi is not None and e0.est_roi < 100  # sane, not a mirage


def test_real_bids_above_the_floor_are_untouched():
    lot, e = _lot(40, 45)
    enrich._apply_roi(lot, e)
    lot_hi, e_hi = _lot(45, 45)
    enrich._apply_roi(lot_hi, e_hi)
    # graded at max(current, next) = 45 either way — the floor is inert here
    assert e.est_roi == pytest.approx(e_hi.est_roi)
