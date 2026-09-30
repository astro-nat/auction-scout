"""Live Auction mode's alert rule (workers/live.newly_past_max).

Fires on the crossing - at or under max before the refresh, over it after -
for watched lots only, once per lot.
"""

from types import SimpleNamespace as NS

from app.workers.live import newly_past_max


def lot(id, bid, max_bid, watched=True, alerted=None):
    return NS(id=id, current_bid=bid, watched=watched, max_passed_alert_at=alerted,
              enrichment=NS(max_bid=max_bid))


def test_the_crossing_alerts():
    assert [l.id for l in newly_past_max({1: 20}, [lot(1, 26, 25)])] == [1]


def test_already_over_before_does_not_alert_again():
    assert newly_past_max({1: 30}, [lot(1, 32, 25)]) == []


def test_still_under_does_not_alert():
    assert newly_past_max({1: 20}, [lot(1, 25, 25)]) == []     # at max is not past it


def test_only_watched_lots():
    assert newly_past_max({1: 20}, [lot(1, 26, 25, watched=False)]) == []


def test_once_per_lot():
    assert newly_past_max({1: 20}, [lot(1, 26, 25, alerted="2026-09-30")]) == []


def test_a_lot_with_no_max_or_no_bid_is_skipped():
    assert newly_past_max({1: 20}, [lot(1, 26, None)]) == []
    assert newly_past_max({}, [lot(1, 26, 25)]) == []
