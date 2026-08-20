"""eBay Browse API client — OAuth, keyword search, and search-by-image.

Uses the client-credentials flow (app token, no user login). All methods degrade
to empty results when EBAY_APP_ID / EBAY_CERT_ID aren't configured, so the rest
of the pipeline works without eBay keys — you just get no comps/image matches.
"""

import base64
import logging
import threading
from typing import Optional

import httpx

from .. import config

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_BROWSE_SEARCH = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_IMAGE_SEARCH = "https://api.ebay.com/buy/browse/v1/item_summary/search_by_image"

_token: Optional[str] = None
_token_lock = threading.Lock()


def enabled() -> bool:
    return bool(config.EBAY_APP_ID and config.EBAY_CERT_ID)


def _get_token(client: httpx.Client) -> Optional[str]:
    global _token
    if _token:
        return _token
    with _token_lock:
        if _token:
            return _token
        creds = base64.b64encode(
            f"{config.EBAY_APP_ID}:{config.EBAY_CERT_ID}".encode()
        ).decode()
        resp = client.post(
            _TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {creds}",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
        )
        resp.raise_for_status()
        _token = resp.json()["access_token"]
        return _token


def search_active(query: str, limit: int = 8) -> list[dict]:
    """Fixed-price active listings, cheapest first. Returns itemSummaries."""
    if not enabled() or not query:
        return []
    try:
        with httpx.Client(timeout=30.0) as client:
            token = _get_token(client)
            r = client.get(
                _BROWSE_SEARCH,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                },
                params={
                    "q": query,
                    "filter": "buyingOptions:{FIXED_PRICE}",
                    "sort": "price",
                    "limit": str(limit),
                },
            )
        if r.status_code != 200:
            logger.warning("eBay browse search HTTP %s for %r", r.status_code, query)
            return []
        return r.json().get("itemSummaries", []) or []
    except Exception as exc:
        logger.warning("eBay browse search failed for %r: %s", query, exc)
        return []


def search_by_image(image_bytes: bytes, limit: int = 8) -> list[dict]:
    """Visual product match against eBay's live catalog."""
    if not enabled() or not image_bytes:
        return []
    try:
        b64 = base64.b64encode(image_bytes).decode()
        with httpx.Client(timeout=30.0) as client:
            token = _get_token(client)
            r = client.post(
                _IMAGE_SEARCH,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                    "Content-Type": "application/json",
                },
                params={"limit": str(limit)},
                json={"image": b64},
            )
        if r.status_code != 200:
            return []
        return r.json().get("itemSummaries", []) or []
    except Exception as exc:
        logger.warning("eBay image search failed: %s", exc)
        return []
