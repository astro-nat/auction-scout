"""The acquisition week's boundary — pure math, no database.

The week runs Monday ~midnight Central, stored as 05:00 UTC. The cases that
matter are the edges: Sunday-night bidding (Central) must land in the week
that is ending, and the flip must happen at exactly 05:00 UTC Monday.
"""

from datetime import datetime

from app.routers.status import week_start_utc

MON_0500 = datetime(2026, 9, 14, 5, 0, 0)       # Monday 2026-09-14 05:00 UTC
NEXT_MON_0500 = datetime(2026, 9, 21, 5, 0, 0)


def test_midweek_maps_to_its_monday():
    assert week_start_utc(datetime(2026, 9, 16, 12, 30)) == MON_0500   # Wed
    assert week_start_utc(datetime(2026, 9, 20, 23, 59)) == MON_0500   # Sun (UTC)


def test_sunday_night_central_stays_in_the_old_week():
    # Monday 04:59 UTC is Sunday 11:59pm Central — still last week's bidding.
    assert week_start_utc(datetime(2026, 9, 21, 4, 59, 59)) == MON_0500


def test_flip_happens_exactly_at_0500_utc_monday():
    assert week_start_utc(NEXT_MON_0500) == NEXT_MON_0500


def test_boundary_is_a_fixed_point_and_always_monday():
    for probe in (datetime(2026, 9, 15, 3, 3, 3), datetime(2026, 12, 31, 18, 0),
                  datetime(2027, 1, 1, 2, 0), datetime(2028, 2, 29, 12, 0)):
        start = week_start_utc(probe)
        assert week_start_utc(start) == start          # fixed point
        assert (start.weekday(), start.hour, start.minute) == (0, 5, 0)
        assert start <= probe                          # never in the future
