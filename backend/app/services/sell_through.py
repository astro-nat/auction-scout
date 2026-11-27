"""Sell-through rate (STR) for a gold mine: sold / (sold + listed).

The user's call (2026-11-10): once a lot is a gold mine, check how fast
the thing actually sells, and show it - informational, not a gate.

Both sides have to count the SAME item, found the same way, or the ratio
means nothing. Every earlier version broke that (2026-11-25..27):
- SoldComps' raw totals: an empty sold total read 65 gold mines as 0%,
  then loose sold matches against a narrow active total put the median at
  92%;
- eBay's Browse search for the listed side: it requires every word, the
  artist's name included, while SoldComps' sold search is loose - a quarter
  of gold mines read 100% ("Leonard Maloney" lapis cuff: 16 sold, 0 listed)
  however the wording was chosen.

So both sides now come from the same engine, wording and filter:
- Sold: the lot's own sold comps - pricing's loose SoldComps sold search
  over 90 days, kept by its relevance checks. Already paid for.
- Listed: the same loose SoldComps search in active mode, same wording,
  same page size, kept by the same relevance checks. One request a lot.

A lot priced from a printed retail price or from asking prices has no sold
side, so it gets no STR rather than an invented one. METHOD marks which
version made a reading: older readings are hidden and redone.
"""

from __future__ import annotations

import logging
import time

import httpx

from . import pricing

logger = logging.getLogger(__name__)

METHOD = "matched-v4"
SCRAPE_URL = "https://api.sold-comps.com/v1/scrape"
# The page pricing's sold search asks for, so the two sides see alike.
LISTED_COUNT = 120


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


def comp_wording(title: str, comps) -> str | None:
    """The search wording a lot's sold comps came from, for lots priced
    before pricing saved it: the first wording, most specific first, that
    most of its stored sold comps match - the one pricing would have kept."""
    variants = pricing.query_variants(title)
    titles = [c.get("title") or "" for c in (comps or []) if (c or {}).get("kind") == "sold"]
    if not variants or not titles:
        return None
    for query in variants:
        if 2 * sum(1 for t in titles if pricing._relevant(query, t)) >= len(titles):
            return query
    return variants[-1]                 # the broadest, as pricing's last try


def _active_titles(query: str) -> list[str] | None:
    """Titles from SoldComps' loose active search, or None on any failure.
    Shares pricing's pacing, outage breaker and usage counters."""
    if not pricing.SOLDCOMPS_API_KEY or pricing._breaker_open():
        return None
    pricing._throttle.wait()
    sent = time.perf_counter()
    try:
        with httpx.Client(timeout=httpx.Timeout(
                pricing.SOLDCOMPS_TIMEOUT_SECONDS,
                connect=pricing._CONNECT_TIMEOUT_SECONDS)) as client:
            r = client.get(
                SCRAPE_URL,
                headers={"Authorization": f"Bearer {pricing.SOLDCOMPS_API_KEY}"},
                params={"keyword": query, "count": LISTED_COUNT,
                        "exactMatch": "false", "sold": "false"})
    except Exception as exc:  # noqa: BLE001 - a missing STR must not fail the lot
        took = (time.perf_counter() - sent) * 1000
        pricing._usage_add(calls=1, http_ms=took)
        pricing._note_response(query, None, note=f"STR listed: {type(exc).__name__}"[:120],
                               duration_ms=took)
        return None
    took = (time.perf_counter() - sent) * 1000
    pricing._usage_add(calls=1, http_ms=took)
    if r.status_code == 429:
        pricing._note_429()
    if r.status_code != 200:
        pricing._note_response(query, r.status_code, note="STR listed", duration_ms=took)
        return None
    pricing._note_ok()
    items = (r.json() or {}).get("items") or []
    pricing._note_response(query, 200, items=len(items), note="STR listed",
                           duration_ms=took)
    return [it.get("title") or "" for it in items]


def listed_side(title: str, comp_query: str | None = None, comps=None) -> int | None:
    """Active listings that are the same item as the lot, found the way its
    sold comps were; None when the search can't be made."""
    query = comp_query or comp_wording(title, comps)
    if not query:
        return None
    titles = _active_titles(query)
    if titles is None:
        return None
    return sum(1 for t in titles if pricing.active_matches(title, query, t))
