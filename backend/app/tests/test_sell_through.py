"""Sell-through rate on gold mines. Pure - SoldComps is stubbed."""

from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.services import sell_through
from app.workers import enrich


@pytest.mark.parametrize("raw,total", [
    ("14,000+", 14000), ("89", 89), (89, 89), ("0", 0), (None, None), ("", None),
])
def test_parse_total(raw, total):
    assert sell_through.parse_total(raw) == total


@pytest.mark.parametrize("sold,active,pct", [
    (120, 80, 60.0), (0, 50, 0.0), (30, 0, 100.0), (0, 0, None), (None, 5, None),
])
def test_rate(sold, active, pct):
    assert sell_through.rate(sold, active) == pct


def test_check_needs_both_totals(monkeypatch):
    totals = {True: 120, False: None}
    monkeypatch.setattr(sell_through, "_total", lambda q, sold: totals[sold])
    assert sell_through.check("Coleman 220J lantern") is None
    totals[False] = 80
    assert sell_through.check("Coleman 220J lantern") == {"sold": 120, "active": 80, "pct": 60.0}


class _Db:
    def commit(self):
        pass


def _gold(status="GOLD MINE", checked=None):
    lot = models.Lot(lot_id="1", title="Vtg Coleman Model 220J Double Mantle Lantern")
    e = models.Enrichment(lot_id=1, roi_status=status, str_checked_at=checked,
                          enriched_title=None)
    return lot, e


def test_a_gold_mine_gets_checked(monkeypatch):
    asked = []
    monkeypatch.setattr(sell_through, "check",
                        lambda q: asked.append(q) or {"sold": 120, "active": 80, "pct": 60.0})
    lot, e = _gold()
    assert enrich._check_str(_Db(), lot, e) is True
    assert (e.str_sold, e.str_active, e.str_pct) == (120, 80, 60.0)
    assert asked == ["Vtg Coleman Model 220J Double Mantle Lantern"]


def test_only_gold_mines_spend_requests(monkeypatch):
    monkeypatch.setattr(sell_through, "check", lambda q: pytest.fail("checked a PASS"))
    lot, e = _gold(status="PASS")
    assert enrich._check_str(_Db(), lot, e) is False


def test_a_fresh_check_is_not_repeated(monkeypatch):
    monkeypatch.setattr(sell_through, "check", lambda q: pytest.fail("rechecked"))
    lot, e = _gold(checked=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2))
    assert enrich._check_str(_Db(), lot, e) is False


def test_a_week_old_check_is_redone(monkeypatch):
    monkeypatch.setattr(sell_through, "check", lambda q: {"sold": 1, "active": 1, "pct": 50.0})
    lot, e = _gold(checked=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8))
    assert enrich._check_str(_Db(), lot, e) is True


def test_a_failed_check_leaves_it_due(monkeypatch):
    monkeypatch.setattr(sell_through, "check", lambda q: None)
    lot, e = _gold()
    assert enrich._check_str(_Db(), lot, e) is False
    assert e.str_checked_at is None


def _priced(e):
    e.comp_count, e.price_source = 12, "sold (SoldComps)"
    return e


def test_zero_sold_on_a_sold_comped_lot_is_not_recorded(monkeypatch):
    """A $2 star note read 0 sold against 42 listed: the search missed."""
    monkeypatch.setattr(sell_through, "check", lambda q: {"sold": 0, "active": 42, "pct": 0.0})
    lot, e = _gold()
    _priced(e)
    assert enrich._check_str(_Db(), lot, e) is False
    assert e.str_pct is None


def test_a_recorded_zero_is_rechecked(monkeypatch):
    lot, e = _gold(checked=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1))
    _priced(e)
    e.str_sold, e.str_active, e.str_pct = 0, 42, 0.0
    assert enrich._str_due(e)


def test_sold_counts_the_sales_listed_not_the_empty_total():
    """In sold mode totalResults came back empty: 65 of 138 gold mines read
    0 sold. The sales themselves are the count."""
    body = {"totalResults": None, "items": [{"soldPrice": "$40"}] * 113}
    assert sell_through.count_from(body, sold=True) == 113
    assert sell_through.count_from({"items": [{}] * 500}, sold=True) == 240   # capped


def test_active_reads_ebays_total():
    assert sell_through.count_from({"totalResults": "14,000+", "items": [{}] * 60}, sold=False) == 14000
    assert sell_through.count_from({"totalResults": None, "items": [{}] * 7}, sold=False) == 7


def test_the_sold_search_covers_ninety_days():
    p = sell_through._params("squash blossom", sold=True)
    assert p["sold"] == "true" and p["count"] == 240 and "soldAfter" in p
    assert "soldAfter" not in sell_through._params("squash blossom", sold=False)


def test_a_zero_on_an_audit_corrected_lot_is_rechecked(monkeypatch):
    lot, e = _gold(checked=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1))
    e.comp_count, e.price_source = 108, "audit-corrected (comps said $807.5)"
    e.str_sold, e.str_active, e.str_pct = 0, 899, 0.0
    assert enrich._str_due(e)
