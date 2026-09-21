"""GovDeals (Liquidity Services) search client.

GovDeals has no auction events — every listing is a standalone asset with
its own end time, sold by a government agency or company. The app's shape
is Auction→Lots, so assets are grouped by SELLER into one synthetic
auction per agency: one seller is one pickup location, which is exactly
the trip-planning unit the $200/auction floor reasons about.

The maestro API is the site's own backend (an Azure API Management
front): anonymous access with the two app keys served to every visitor in
the public JS bundle, plus per-request correlation ids. No login, no
account, no scraping of rendered pages. If the keys rotate with a site
release, override them via env without a deploy.
"""

import logging
import uuid
from datetime import datetime

import httpx

from .. import config

logger = logging.getLogger(__name__)

SEARCH_URL = "https://maestro.lqdt1.com/search/list"
PHOTO_BASE = "https://webassets.lqdt1.com/assets/photos"
ASSET_URL = "https://www.govdeals.com/en/asset/{asset_id}/{account_id}"

# A page is 120 assets; PAGES_MAX caps a runaway radius at a sane import.
DISPLAY_ROWS = 120
PAGES_MAX = 10


def _headers() -> dict:
    return {
        "x-api-key": config.GOVDEALS_API_KEY,
        "Ocp-Apim-Subscription-Key": config.GOVDEALS_SUB_KEY,
        "x-user-id": "-1",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://www.govdeals.com",
        "x-api-correlation-id": str(uuid.uuid4()),
        "x-ecom-session-id": str(uuid.uuid4()),
    }


def _body(page: int, session_id: str, *, zip_code: str | None = None,
          miles: int | None = None,
          account_ids: list[int] | None = None) -> dict:
    body = {
        "categoryIds": "", "businessId": "GD", "searchText": "*",
        "isQAL": False, "auctionTypeId": None, "page": page,
        "displayRows": DISPLAY_ROWS, "sortField": "timeleft",
        "sortOrder": "asc", "sessionId": session_id,
        "requestType": "search", "responseStyle": "fullResponse",
        "facets": [], "facetsFilter": [], "timeType": "",
        "sellerTypeId": None, "accountIds": account_ids or [],
        "isSimpleTimeSearch": True, "simpleTimeSearchType": "atauction",
        "simpleTimeWithIn": 0, "toDate": None, "fromDate": None,
        "timeUnitValue": "", "isVehicleSearch": False,
    }
    if zip_code:
        body["zipcode"] = zip_code
        body["proximityWithinDistance"] = str(miles or 25)
    return body


def _parse_end(value) -> datetime | None:
    """assetAuctionEndDateUtc ('2026-09-23T19:00:00Z') → naive UTC, the
    convention closing_date/closes_at already follow."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) \
                       .replace(tzinfo=None)
    except ValueError:
        return None


def normalize_asset(a: dict) -> dict | None:
    """One search row → the fields the importer stores. None if the row is
    missing its identity or a title — nothing to bid on."""
    account_id = a.get("accountId")
    asset_id = a.get("assetId")
    title = (a.get("assetShortDescription") or "").strip()
    if not account_id or not asset_id or not title:
        return None
    photo = a.get("photo") or ""
    photo_url = f"{PHOTO_BASE}/{account_id}/{photo}" if photo else None
    current = float(a.get("currentBid") or 0)
    increment = float(a.get("assetBidIncrement") or 0)
    return {
        "account_id": int(account_id),
        "asset_id": int(asset_id),
        "lot_id": f"gd-{account_id}-{asset_id}",
        "title": title[:500],
        "category": a.get("categoryDescription"),
        "seller": (a.get("companyName") or "").strip() or f"Seller {account_id}",
        "city": a.get("locationCity"),
        "state": a.get("locationState"),
        "zip": a.get("locationZip"),
        "current_bid": current,
        # What the next bidder pays — the grader prices this, same as HiBid.
        "next_bid": current + increment if increment else current,
        "bid_count": a.get("bidCount") or 0,
        "closes_at": _parse_end(a.get("assetAuctionEndDateUtc")),
        "thumbnail_url": f"{photo_url}&w=350" if photo_url and "?" in photo_url
                         else (f"{photo_url}?w=350" if photo_url else None),
        "fullsize_url": photo_url,
        "lot_link": ASSET_URL.format(asset_id=asset_id, account_id=account_id),
    }


def search_assets(zip_code: str | None = None, miles: int | None = None,
                  account_ids: list[int] | None = None,
                  pages_max: int = PAGES_MAX) -> list[dict]:
    """Every open asset matching the filter (a zip radius, or specific
    sellers), normalized and deduped by lot_id."""
    session_id = str(uuid.uuid4())
    seen: dict[str, dict] = {}
    with httpx.Client(timeout=30) as client:
        for page in range(1, pages_max + 1):
            body = _body(page, session_id, zip_code=zip_code, miles=miles,
                         account_ids=account_ids)
            r = client.post(SEARCH_URL, json=body, headers=_headers())
            r.raise_for_status()
            rows = r.json().get("assetSearchResults") or []
            for row in rows:
                item = normalize_asset(row)
                if item:
                    seen[item["lot_id"]] = item
            if len(rows) < DISPLAY_ROWS:
                break
    return list(seen.values())


def group_by_seller(assets: list[dict]) -> dict[int, dict]:
    """account_id → the synthetic-auction fields for that seller's pile.

    closing_date is the LATEST end among the seller's assets, so the
    auction row stays open (and bid-worthy in every filter) until its last
    lot closes; each lot's own closes_at carries the real staggered time.
    """
    groups: dict[int, dict] = {}
    for a in assets:
        g = groups.setdefault(a["account_id"], {
            "account_id": a["account_id"],
            "external_id": f"gd-{a['account_id']}",
            "seller": a["seller"],
            "city": a["city"], "state": a["state"], "zip": a["zip"],
            "closing_date": None,
            "assets": [],
        })
        g["assets"].append(a)
        end = a["closes_at"]
        if end and (g["closing_date"] is None or end > g["closing_date"]):
            g["closing_date"] = end
    return groups
