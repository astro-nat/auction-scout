"""Sell-through rate (STR) for a gold mine: sold / (sold + listed).

The user's call (2026-11-10): once a lot is a gold mine, check how fast
the thing actually sells, and show it - informational, not a gate.

Both sides count the SAME item - listings that pass the relevance checks
pricing uses - or the ratio means nothing. Two earlier versions read
SoldComps' raw search totals and failed that way: sold mode's total came
back empty (65 of 138 gold mines at 0%), then counting loose sold matches
capped at 240 against eBay's much narrower active total put the median at
92% (2026-11-25).

- Sold: the lot's own sold comps. Pricing's sold search covers the last 90
  days and keeps only relevant comps, so its comp count is the sold side,
  already paid for.
- Listed: eBay's active listings for the same words (the Browse API, free),
  kept only if they pass pricing's active-listing relevance checks.

A lot priced from a printed retail price or from asking prices has no sold
side, so it gets no STR rather than an invented one. METHOD marks which
version made a reading: older readings are hidden and redone.
"""

from __future__ import annotations

import logging

from . import ebay, pricing

logger = logging.getLogger(__name__)

METHOD = "matched-v2"
# The most one Browse search returns.
LISTED_MAX = 200


def rate(sold: int | None, active: int | None) -> float | None:
    """Sold as a share of everything sold or listed, in percent."""
    if sold is None or active is None or sold + active == 0:
        return None
    return round(100.0 * sold / (sold + active), 1)


def sold_side(comp_count: int | None, comps) -> int | None:
    """The lot's relevant sold comps, or None when it wasn't priced from
    sold listings (a retail price, asking prices)."""
    if not comp_count or not any((c or {}).get("kind") == "sold" for c in (comps or [])):
        return None
    return int(comp_count)


def listed_side(title: str) -> int | None:
    """Active eBay listings that are the same item as the lot, or None when
    eBay can't be asked."""
    if not ebay.enabled():
        return None
    variants = pricing.query_variants(title)
    if not variants:
        return None
    query = variants[0]                 # most specific, as pricing tries first
    items = ebay.search_active(query, limit=LISTED_MAX)
    return sum(1 for it in items
               if pricing.active_matches(title, query, it.get("title") or ""))


def check(title: str, comp_count: int | None, comps) -> dict | None:
    """{sold, active, pct} for a lot, or None when there's no fair figure."""
    title = (title or "").strip()
    sold = sold_side(comp_count, comps)
    if not title or sold is None:
        return None
    active = listed_side(title)
    if active is None:
        return None
    return {"sold": sold, "active": active, "pct": rate(sold, active)}
