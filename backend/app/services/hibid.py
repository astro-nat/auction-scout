"""HiBid GraphQL scraper — auction discovery, per-auction meta, lot harvesting.

Ported from the Streamlit prototype's pass1.py, minus pandas/Streamlit. Everything
here is unauthenticated: HiBid's public graphql endpoint serves discovery, auction
terms, and full lot listings without a login.

Load-bearing API facts (learned the hard way in the prototype):
- LotSearch's `pageNumber` is a SIBLING of `input`, not a field inside it, and page
  size is server-fixed at 100. The old pageSize/pageIndex fields 400 out.
- Server caps pagination at page 100 (10k lots/auction).
- `buyerPremiumRate` == 1 means "not filled in", NOT "no premium" — parse the free
  text instead, and only trust the rate when > 1.
- `category` on a lot may be a list of dicts, a single dict, or None.
- Effective bid = max(highBid, minBid): zero-bid lots carry cost in minBid.
- HiBid's CDN requires `Referer: https://hibid.com/` on image downloads.
"""

import asyncio
import logging
import math
import re
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from .. import config

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://hibid.com/graphql"
LOT_PAGE_SIZE = 100      # server-fixed
MAX_LOT_PAGES = 100      # server cap
AUCTION_BATCH = 20       # concurrent auctions per gather
META_CHUNK = 50          # eventIds per AuctionMeta call

HEADERS = {
    "User-Agent": config.HIBID_USER_AGENT,
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://hibid.com",
    "Referer": "https://hibid.com/",
}

# HiBid's filter vocabulary — probed against the live auctionMap endpoint
# (their UI shows more statuses, but these are what this query accepts).
AUCTION_TYPES = {"ALL", "ONLINE", "WEBCAST", "ABSENTEE", "LISTING"}
STATUSES = {"ALL", "OPEN", "CLOSING", "HOT", "CLOSED"}

CATEGORY_TREE_QUERY = """
query { categoryTree(input: {}) { id categoryName children { id categoryName } } }
"""

_category_cache: list | None = None


async def fetch_categories() -> list[dict]:
    """HiBid's top-level category tree (17 categories), cached per process."""
    global _category_cache
    if _category_cache is not None:
        return _category_cache
    async with httpx.AsyncClient() as client:
        data = await _graphql(client, None, CATEGORY_TREE_QUERY, {})
    _category_cache = [
        {"id": c["id"], "name": c["categoryName"]}
        for c in data.get("categoryTree") or []
    ]
    return _category_cache


AUCTION_MAP_QUERY = """
query AuctionMap($zip: String, $miles: Int, $searchText: String, $categoryId: CategoryId, $filter: AuctionLotFilter, $status: AuctionLotStatus, $eventIds: [Int!] = null) {
  auctionMap(
    input: {zip: $zip, miles: $miles, searchText: $searchText, category: $categoryId, filter: $filter, status: $status, eventIds: $eventIds}
  ) {
    mapMarkers {
      auction {
        id eventName auctioneer { name __typename } lotCount geoLong geoLat
        eventAddress eventCity eventZip eventState eventDateBegin eventDateInfo eventDateEnd __typename
      } __typename
    } __typename
  }
}
"""

AUCTION_META_QUERY = """
query AuctionMeta($eventIds: [Int!]) {
  auctionMap(input: {zip: "", miles: 0, searchText: "", category: -1, filter: ALL, status: ALL, eventIds: $eventIds}) {
    mapMarkers { auction { id buyerPremium buyerPremiumRate shippingAndPickupInfo termsAndConditions } }
  }
}
"""

LOT_SEARCH_QUERY = """
query LotSearch($auctionId: Int!, $pageNumber: Int!, $searchText: String!, $category: CategoryId) {
  lotSearch(input: {auctionId: $auctionId, searchText: $searchText, category: $category}, pageNumber: $pageNumber) {
    pagedResults {
      totalCount pageNumber
      results {
        id lotNumber lead description estimate
        category { categoryName }
        lotState { highBid minBid bidCount status timeLeft }
        pictures { thumbnailLocation hdThumbnailLocation fullSizeLocation }
        shippingOffered
      }
    }
  }
}
"""

_SHIP_KILLERS_RE = re.compile(config.SHIP_KILLERS, re.IGNORECASE)
_MAILBOX_RE = re.compile(config.MAILBOX_WINNERS, re.IGNORECASE)
_PICKUP_ONLY_RE = re.compile(r"local pickup only|pickup only|no shipping", re.IGNORECASE)
_COND_SHIP_RE = re.compile(
    r"not available on all lots|contact .{0,30}prior to bidding|do not assume all items",
    re.IGNORECASE,
)


async def _graphql(client: httpx.AsyncClient, operation: str, query: str,
                   variables: dict) -> dict:
    """POST one GraphQL op with the prototype's escalating-timeout retry ladder —
    a single 100-lot page with heavy descriptions can blow the base timeout."""
    last_exc: Exception | None = None
    for attempt, (mult, backoff) in enumerate([(1, 1.5), (2, 3.0), (3, 0)], 1):
        try:
            resp = await client.post(
                GRAPHQL_URL,
                json={"operationName": operation, "query": query, "variables": variables},
                headers=HEADERS,
                timeout=config.HIBID_TIMEOUT_SECONDS * mult,
            )
            if resp.status_code != 200:
                # HiBid 400 bodies explain the problem — surface them, don't swallow
                raise RuntimeError(f"HiBid {operation} HTTP {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
            if data.get("errors"):
                raise RuntimeError(f"HiBid {operation} GraphQL error: {data['errors'][:2]}")
            return data["data"]
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if backoff:
                await asyncio.sleep(backoff)
    raise RuntimeError(f"HiBid {operation} failed after 3 attempts: {last_exc}")


# --------------------------------------------------------------- discovery

def _parse_event_end(auction: dict) -> Optional[datetime]:
    """eventDateEnd may be null or date-only; enrich the time-of-day from
    eventDateInfo's 'h:mm AM/PM' token, else assume end of day."""
    raw = auction.get("eventDateEnd")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None
    if dt.hour == 0 and dt.minute == 0:
        info = auction.get("eventDateInfo") or ""
        m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", info, re.IGNORECASE)
        if m:
            hour = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
            dt = dt.replace(hour=hour, minute=int(m.group(2)))
        else:
            dt = dt.replace(hour=23, minute=59)
    return dt


async def discover_auctions(zip_code: str | None = None,
                            radius_miles: int | None = None,
                            closing_within_days: int | None = None,
                            include_nationwide: bool = False,
                            search_text: str = "",
                            category_id: int = -1,
                            auction_type: str = "ALL",
                            status: str = "OPEN") -> list[dict]:
    """Find auctions matching HiBid's own search filters (keyword, category,
    auction type, status) near a zip — or anywhere with radius_miles=-1.
    Returns a list of auction dicts ready to persist."""
    zip_code = zip_code or config.SOURCING_ZIP
    radius_miles = radius_miles if radius_miles is not None else config.SOURCING_RADIUS_MILES
    closing_within = closing_within_days if closing_within_days is not None else config.CLOSING_WITHIN_DAYS
    auction_type = auction_type if auction_type in AUCTION_TYPES else "ALL"
    status = status if status in STATUSES else "OPEN"

    async with httpx.AsyncClient() as client:
        if radius_miles == -1:  # HiBid's "Anywhere"
            calls = [("nationwide", {"zip": "", "miles": 0})]
        else:
            calls = [("local", {"zip": zip_code, "miles": radius_miles})]
            if include_nationwide:
                calls.append(("nationwide", {"zip": "", "miles": 0}))

        results: list[dict] = []
        seen_ids: set[int] = set()
        cutoff = datetime.now() + timedelta(days=closing_within)

        for source_tag, geo in calls:
            variables = {**geo, "searchText": search_text or "",
                         "categoryId": category_id if category_id else -1,
                         "filter": auction_type, "status": status, "eventIds": None}
            data = await _graphql(client, "AuctionMap", AUCTION_MAP_QUERY, variables)
            markers = (data.get("auctionMap") or {}).get("mapMarkers") or []
            for marker in markers:
                a = marker.get("auction") or {}
                aid = a.get("id")
                if not aid or aid in seen_ids:
                    continue
                closing = _parse_event_end(a)
                # unresolvable closing dates are KEPT — err on inclusion
                if closing and closing > cutoff:
                    continue
                seen_ids.add(aid)
                results.append({
                    "hibid_id": aid,
                    "name": a.get("eventName") or f"Auction {aid}",
                    "auctioneer": (a.get("auctioneer") or {}).get("name"),
                    "lot_count": a.get("lotCount"),
                    "city": a.get("eventCity"),
                    "state": a.get("eventState"),
                    "zip": a.get("eventZip"),
                    "closing_date": closing,
                    "source": "Local Pickup" if source_tag == "local" else "Ship",
                    "source_url": f"https://hibid.com/auction/{aid}",
                })
        # nationwide sorted last / by date; None-safe key
        results.sort(key=lambda r: (r["source"] != "Local Pickup",
                                    r["closing_date"] or datetime.max))
        return results


# --------------------------------------------------------- commercial terms

def _parse_buyer_premium(meta: dict) -> Optional[float]:
    """Extract the buyer-premium multiplier (1.15 = 15%) from an auction's meta.
    Precedence: explicit 'no premium' text → free text % → rate>1 → terms text
    near the phrase 'buyer's premium' → None (caller falls back to config)."""
    text = (meta.get("buyerPremium") or "")
    if re.search(r"no buyer'?s? premium", text, re.IGNORECASE):
        return 1.0
    m = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", text)
    if m and 0 < float(m.group(1)) <= 50:
        return 1 + float(m.group(1)) / 100
    rate = meta.get("buyerPremiumRate")
    if isinstance(rate, (int, float)) and rate > 1:
        return float(rate) if rate < 2 else 1 + float(rate) / 100
    terms = meta.get("termsAndConditions") or ""
    for m in re.finditer(r"(\d{1,2}(?:\.\d+)?)\s*%", terms):
        window = terms[max(0, m.start() - 45):m.end() + 45].lower()
        if "premium" in window and 0 < float(m.group(1)) <= 50:
            return 1 + float(m.group(1)) / 100
    return None


async def fetch_auction_meta(client: httpx.AsyncClient, hibid_ids: list[int]) -> dict[int, dict]:
    """Per-auction premium multiplier + shipping hints. Failures degrade to {},
    never raise — callers fall back to config defaults."""
    out: dict[int, dict] = {}
    for i in range(0, len(hibid_ids), META_CHUNK):
        chunk = hibid_ids[i:i + META_CHUNK]
        try:
            data = await _graphql(client, "AuctionMeta", AUCTION_META_QUERY,
                                  {"eventIds": chunk})
        except Exception as exc:
            logger.warning("AuctionMeta failed for chunk %s: %s", chunk[:3], exc)
            continue
        for marker in (data.get("auctionMap") or {}).get("mapMarkers") or []:
            a = marker.get("auction") or {}
            ship_text = a.get("shippingAndPickupInfo") or ""
            out[a["id"]] = {
                "premium_mult": _parse_buyer_premium(a),
                "cond_ship": bool(_COND_SHIP_RE.search(ship_text)),
            }
    return out


# ----------------------------------------------------------------- lots

def _lot_category(raw: Any) -> Optional[str]:
    """category is polymorphic: list of dicts, single dict, or None."""
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if isinstance(raw, dict):
        return raw.get("categoryName")
    return None


def _logistics_ease(title: str, category: str, description: str) -> str:
    if _PICKUP_ONLY_RE.search(description or ""):
        return "HARD"
    hay = f"{title} {category or ''}"
    if _MAILBOX_RE.search(hay):
        return "EASY"
    if _SHIP_KILLERS_RE.search(f"{hay} {description or ''}"):
        return "HARD"
    return "NEUTRAL"


def _process_lot(raw: dict, auction_ctx: dict) -> dict:
    state = raw.get("lotState") or {}
    pictures = raw.get("pictures") or []
    first_pic = pictures[0] if pictures else {}
    title = raw.get("lead") or ""
    description = raw.get("description") or ""
    category = _lot_category(raw.get("category")) or ""

    current_bid = float(state.get("highBid") or 0.0)
    next_bid = round(float(state.get("minBid") or 0.0), 2)
    premium_mult = auction_ctx.get("premium_mult") or (1 + config.DEFAULT_BUYER_PREMIUM_PCT / 100)
    # effective bid = max(highBid, minBid): zero-bid lots carry cost in minBid
    est_cost = round(max(current_bid, next_bid) * premium_mult, 2)

    if raw.get("shippingOffered") is True:
        source = "Ship"
    elif raw.get("shippingOffered") is False:
        source = "Local Pickup"
    elif _PICKUP_ONLY_RE.search(description):
        source = "Local Pickup"
    else:
        source = auction_ctx.get("source", "Local Pickup")

    return {
        "lot_id": str(raw.get("id")),
        "title": title,
        "category": category or None,
        "description": description or None,
        "current_bid": current_bid,
        "next_bid": next_bid,
        "bid_count": state.get("bidCount") or 0,
        "est_cost": est_cost,
        "status": state.get("status"),
        "time_left": state.get("timeLeft"),
        "source": source,
        "logistics_ease": _logistics_ease(title, category, description),
        "lot_link": f"https://hibid.com/lot/{raw.get('id')}",
        "thumbnail_url": first_pic.get("thumbnailLocation"),
        "hd_thumbnail_url": first_pic.get("hdThumbnailLocation"),
        "fullsize_url": first_pic.get("fullSizeLocation"),
        "image_count": len(pictures),
        # nationwide auction + pickup-only lot = unbuyable, never grade as a bargain
        "unreachable_pickup": (auction_ctx.get("source") == "Ship"
                               and source == "Local Pickup"),
    }


async def fetch_lots(hibid_auction_id: int, auction_ctx: dict | None = None,
                     search_text: str = "", category_id: int = -1,
                     on_progress=None, should_cancel=None) -> list[dict]:
    """All open lots for one auction, optionally filtered to one HiBid
    category server-side. Paginates at the server-fixed 100/page.

    on_progress(fetched, total) is called after each page so callers can
    report live counts to the UI."""
    ctx = auction_ctx or {}
    lots: list[dict] = []
    async with httpx.AsyncClient() as client:
        page = 1
        total = None
        while page <= MAX_LOT_PAGES:
            if should_cancel and should_cancel():
                break
            data = await _graphql(client, "LotSearch", LOT_SEARCH_QUERY, {
                "auctionId": hibid_auction_id, "pageNumber": page,
                "searchText": search_text, "category": category_id,
            })
            paged = (data.get("lotSearch") or {}).get("pagedResults") or {}
            batch = paged.get("results") or []
            if total is None:
                total = paged.get("totalCount") or 0
            if not batch:
                break
            lots.extend(_process_lot(r, ctx) for r in batch)
            if on_progress:
                on_progress(len(lots), total)
            if len(lots) >= total:
                break
            page += 1
    # closed-lot filtering happens post-fetch
    return [l for l in lots
            if l["status"] != "CLOSED" and l["time_left"] != "Bidding Closed"]


async def download_image(url: str) -> Optional[bytes]:
    """Pull a thumbnail from HiBid's CDN — Referer header is mandatory."""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get(url, headers={
                "Referer": "https://hibid.com/",
                "User-Agent": config.HIBID_USER_AGENT,
            })
        if r.status_code == 200 and len(r.content) > 1000:
            return r.content
    except Exception:
        pass
    return None


COUNT_QUERY = """
query LotCount($auctionId: Int!, $category: CategoryId) {
  lotSearch(input: {auctionId: $auctionId, searchText: "", category: $category}, pageNumber: 1) {
    pagedResults { totalCount }
  }
}
"""


async def count_matching_lots(hibid_ids: list[int], category_id: int) -> dict[int, int]:
    """How many lots in each auction match a HiBid category — one cheap call
    per auction, batched concurrently. Failures just omit the auction."""
    out: dict[int, int] = {}

    async def one(client: httpx.AsyncClient, aid: int) -> None:
        try:
            data = await _graphql(client, "LotCount", COUNT_QUERY,
                                  {"auctionId": aid, "category": category_id})
            out[aid] = ((data.get("lotSearch") or {}).get("pagedResults") or {}).get("totalCount", 0)
        except Exception as exc:
            logger.warning("lot count failed for auction %s: %s", aid, exc)

    async with httpx.AsyncClient() as client:
        for i in range(0, len(hibid_ids), AUCTION_BATCH):
            batch = hibid_ids[i:i + AUCTION_BATCH]
            await asyncio.gather(*(one(client, aid) for aid in batch))
    return out
