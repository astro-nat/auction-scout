"""Bulk pricing works soonest-closing first (services/closing_order)."""

from datetime import datetime, timedelta

from app.services.closing_order import _order

T = datetime(2026, 10, 1, 18, 0)


def test_soonest_first_and_no_time_last():
    closes = {"a": T + timedelta(hours=5), "b": T, "c": None, "d": T + timedelta(hours=1)}
    assert _order(["a", "b", "c", "d"], closes) == ["b", "d", "a", "c"]


def test_lots_closing_together_keep_the_callers_order():
    # The caller's order is usually most valuable first; a tie keeps it.
    closes = {"x": T, "y": T, "z": T}
    assert _order(["z", "x", "y"], closes) == ["z", "x", "y"]


def test_ids_missing_from_the_lookup_go_last():
    assert _order(["gone", "here"], {"here": T}) == ["here", "gone"]
