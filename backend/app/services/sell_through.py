"""Sell-through rate (STR) for a gold mine, from SoldComps' totals.

The user's call (2026-11-10): once a lot is a gold mine, check how fast
the thing actually sells, and show it - informational, not a gate.

SoldComps has no STR field, so STR is two searches on the same words:
sold / (sold + active), the usual 90-day sell-through. Active is eBay's
own total for the search ("totalResults", e.g. "14,000+"). Sold is the
count of sales in the last 90 days, read from the listings themselves:
in sold mode totalResults came back empty, and reading it showed 65 of
138 gold mines at STR 0% - 0 sold against 899 listed for a squash blossom
whose pricing search had just returned 100+ sales (2026-11-25). Sold
listings cap at 240 a search, so "240" means 240 or more. Both searches
run with exactMatch off, as pricing does. eBay rounds big totals, so a
broad search's STR is approximate.

Costs two SoldComps requests per lot; callers keep the result on the lot
and recheck at most weekly. Shares pricing's pacing, outage breaker and
usage counters, so it can't run the account past its rate limit.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone

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


SOLD_WINDOW_DAYS = 90
SOLD_MAX = 240          # the most one search returns


def _params(query: str, sold: bool) -> dict:
    if sold:
        # Every sale in the window, as listings: the count IS the figure.
        after = datetime.now(timezone.utc) - timedelta(days=SOLD_WINDOW_DAYS)
        return {"keyword": query, "count": SOLD_MAX, "exactMatch": "false",
                "sold": "true", "soldAfter": after.strftime("%Y-%m-%d")}
    # The smallest page: only eBay's total is read, and a request costs the
    # same whatever it returns.
    return {"keyword": query, "count": 60, "exactMatch": "false", "sold": "false"}


def count_from(body: dict, sold: bool) -> int:
    """The figure one reply gives: sales listed in the window when sold,
    eBay's total when active (falling back to the listings returned)."""
    items = body.get("items") or []
    if sold:
        return min(len(items), SOLD_MAX)
    total = parse_total(body.get("totalResults"))
    return total if total is not None else len(items)


def _total(query: str, sold: bool) -> int | None:
    """One side of the STR for a search, or None on any failure."""
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
                params=_params(query, sold),
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
    total = count_from(body, sold)
    pricing._note_response(query, 200, total_items=total,
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
