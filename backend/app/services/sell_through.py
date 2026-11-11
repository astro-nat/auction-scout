"""Sell-through rate (STR) for a gold mine, from SoldComps' totals.

The user's call (2026-11-10): once a lot is a gold mine, check how fast
the thing actually sells, and show it - informational, not a gate.

SoldComps has no STR field. Its /v1/scrape search reports eBay's own
total for the search ("totalResults", e.g. "14,000+") in both modes, so
STR is two searches on the same words: sold / (sold + active). Both run
with exactMatch on - the pricing search turns it off to gather volume,
but "results matching fewer words" would inflate both totals unevenly.
eBay rounds big totals, so a broad search's STR is approximate.

Costs two SoldComps requests per lot; callers keep the result on the lot
and recheck at most weekly. Shares pricing's pacing, outage breaker and
usage counters, so it can't run the account past its rate limit.
"""

from __future__ import annotations

import logging
import re
import time

import httpx

from . import pricing

logger = logging.getLogger(__name__)

SCRAPE_URL = "https://api.sold-comps.com/v1/scrape"


def parse_total(value) -> int | None:
    """eBay's total as SoldComps reports it: "14,000+", "89", 89 -> int."""
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def _total(query: str, sold: bool) -> int | None:
    """eBay's total result count for one search, or None on any failure."""
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
                # The smallest page: only the total is wanted, and a request
                # costs the same whatever it returns.
                params={"keyword": query, "count": 60, "exactMatch": "true",
                        "sold": "true" if sold else "false"},
            )
    except Exception as exc:  # noqa: BLE001 - a missing STR must not fail the lot
        took = (time.perf_counter() - sent) * 1000
        pricing._usage_add(calls=1, http_ms=took)
        pricing._note_response(query, None, note=f"STR {'sold' if sold else 'active'}: "
                                                 f"{type(exc).__name__}"[:120], duration_ms=took)
        return None
    took = (time.perf_counter() - sent) * 1000
    pricing._usage_add(calls=1, http_ms=took)
    if r.status_code == 429:
        pricing._note_429()
    if r.status_code != 200:
        pricing._note_response(query, r.status_code,
                               note=f"STR {'sold' if sold else 'active'}", duration_ms=took)
        return None
    pricing._note_ok()
    body = r.json() or {}
    total = parse_total(body.get("totalResults"))
    if total is None and not body.get("items"):
        total = 0               # nothing listed or sold is an answer: zero
    pricing._note_response(query, 200, total_items=body.get("totalResults"),
                           note=f"STR {'sold' if sold else 'active'} total",
                           duration_ms=took)
    return total


def rate(sold: int | None, active: int | None) -> float | None:
    """Sold as a share of everything sold or listed, in percent."""
    if sold is None or active is None or sold + active == 0:
        return None
    return round(100.0 * sold / (sold + active), 1)


def check(query: str) -> dict | None:
    """{sold, active, pct} for one search, or None when either total failed."""
    query = (query or "").strip()
    if not query:
        return None
    sold = _total(query, sold=True)
    if sold is None:
        return None
    active = _total(query, sold=False)
    if active is None:
        return None
    return {"sold": sold, "active": active, "pct": rate(sold, active)}
