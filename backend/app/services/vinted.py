"""Vinted search client.

Fixed-price marketplace, not an auction house: sourcing here means
watching a query for fresh underpriced listings, so the scan pulls the
catalog sorted newest-first and grades each item at its ASKING price —
gold means the ask already clears the ROI target, and the ceiling
(max_bid) doubles as the most an offer should ever be.

The catalog page is fully server-rendered (~7MB of HTML per page) and
served to anonymous visitors after one cookie-setting hit to the home
page. Every item card's <img alt> packs the interesting fields into one
string — "Title, Brand: X, Condition: Y, 48.00 $, 51.10 $" (ask, then
buyer-protection price) — which beats walking hashed CSS classes that
change every deploy. Titles can contain commas, so the alt is parsed
from its anchored tail, not by splitting.
"""

import logging
import re
import json
from html import unescape

import httpx

logger = logging.getLogger(__name__)

BASE = "https://www.vinted.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0 Safari/537.36")
PAGES_MAX = 2   # newest-first: two pages of ~96 is the whole "fresh" window
WARDROBE_PAGE = 96      # the API's own maximum
WARDROBE_PAGES_MAX = 12 # ~1150 items; a guard against a shop account

_CARD_RE = re.compile(
    r'<img src="(https://images\d*\.vinted\.net/[^"]+)" '
    r'alt="([^"]+)"[^>]*data-testid="product-item-id-(\d+)--image--img"')
# Two shapes: clothing/housewares carry "Brand: X", media (CDs, books)
# goes straight to Condition. Tried in that order — a single regex with an
# optional brand group lets a greedy title swallow the brand instead.
_ALT_BRANDED_RE = re.compile(
    r"^(?P<title>.*), Brand: (?P<brand>[^,]*), Condition: (?P<cond>[^,]*), "
    r"(?P<price>[\d.,]+) \$(?:, [\d.,]+ \$)?$")
_ALT_PLAIN_RE = re.compile(
    r"^(?P<title>.*), Condition: (?P<cond>[^,]*), "
    r"(?P<price>[\d.,]+) \$(?:, [\d.,]+ \$)?$")


# The catalog page ships its item objects as escaped JSON inside the Next.js
# hydration payload, and each one carries the seller the card itself never
# shows: "url":"/items/<id>-slug" ... "user":{"id":<seller>}. Bounded so a
# card without a user can never borrow the next card's.
_ITEM_SELLER_RE = re.compile(
    r'\\"url\\":\\"/items/(\d+)-[^"]{0,300}?\\".{0,2000}?\\"user\\":\{\\"id\\":(\d+)')


def sellers_by_item(html: str) -> dict[int, int]:
    """item id -> seller id, from the catalog page's hydration payload."""
    return {int(i): int(u) for i, u in _ITEM_SELLER_RE.findall(html or "")}


def member_link(user_id: int) -> str:
    return f"{BASE}/member/{user_id}"


def item_link(item_id: int, slug_hint: str = "") -> str:
    return f"{BASE}/items/{item_id}"


def parse_catalog(html: str) -> list[dict]:
    sellers = sellers_by_item(html)
    items = []
    for thumb, alt, item_id in _CARD_RE.findall(html):
        alt = unescape(alt)
        m = _ALT_BRANDED_RE.match(alt) or _ALT_PLAIN_RE.match(alt)
        if not m:
            # A reshuffled alt format should fail loudly in counts, not
            # silently import titles with prices glued on.
            logger.warning("Vinted alt did not parse: %r", alt[:120])
            continue
        try:
            price = float(m.group("price").replace(",", ""))
        except ValueError:
            continue
        brand = (m.groupdict().get("brand") or "").strip()
        cond = m.group("cond").strip()
        title = m.group("title").strip()[:400]
        items.append({
            "item_id": int(item_id),
            "lot_id": f"vt-{item_id}",
            "title": title,
            "brand": brand or None,
            "condition": cond or None,
            "price": price,
            "thumbnail_url": thumb,
            "lot_link": item_link(int(item_id)),
            "seller_id": sellers.get(int(item_id)),
        })
    return items


def search_items(query: str, max_price: float | None = None) -> list[dict]:
    """Newest listings matching the query, deduped by id."""
    seen: dict[int, dict] = {}
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True,
                      timeout=60) as client:
        client.get(f"{BASE}/")   # sets the anonymous session cookies
        for page in range(1, PAGES_MAX + 1):
            params = {"search_text": query, "order": "newest_first",
                      "page": page}
            if max_price:
                params["price_to"] = max_price
                params["currency"] = "USD"
            r = client.get(f"{BASE}/catalog", params=params)
            r.raise_for_status()
            items = parse_catalog(r.text)
            if not items:
                break
            before = len(seen)
            for it in items:
                seen[it["item_id"]] = it
            if len(seen) == before:
                break
    return list(seen.values())


def _session(client: httpx.Client) -> None:
    """Vinted's API answers 401 without the cookies the home page sets."""
    client.get(f"{BASE}/")


def _wardrobe_item(raw: dict) -> dict | None:
    """One API item in the shape the importer already understands."""
    try:
        item_id = int(raw["id"])
        price = float((raw.get("price") or {}).get("amount"))
    except (KeyError, TypeError, ValueError):
        return None
    photos = raw.get("photos") or []
    thumb = (photos[0].get("url") if photos and isinstance(photos[0], dict) else None)
    return {
        "item_id": item_id,
        "lot_id": f"vt-{item_id}",
        "title": (raw.get("title") or "").strip()[:400],
        "brand": (raw.get("brand_title") or (raw.get("brand") or {}).get("title")
                  if isinstance(raw.get("brand"), dict) else raw.get("brand_title")) or None,
        "condition": (raw.get("status") or "").strip() or None,
        "price": price,
        "thumbnail_url": thumb,
        "lot_link": raw.get("url") or item_link(item_id),
        "seller_id": (raw.get("user") or {}).get("id"),
    }


def fetch_wardrobe(user_id: int, max_pages: int = WARDROBE_PAGES_MAX) -> dict:
    """Everything a seller currently has for sale.

    The member page renders its closet in the browser, so there is nothing
    to scrape there; the page calls /api/v2/wardrobe/<id>/items and so does
    this. Returns {"seller": {"id", "login"}, "items": [...], "total": N}.
    Items already sold, reserved or hidden are left out - they cannot be
    bought.
    """
    items: dict[int, dict] = {}
    login = None
    total = 0
    with httpx.Client(headers={"User-Agent": UA, "Accept": "application/json"},
                      follow_redirects=True, timeout=60) as client:
        _session(client)
        for page in range(1, max_pages + 1):
            r = client.get(f"{BASE}/api/v2/wardrobe/{user_id}/items",
                           params={"page": page, "per_page": WARDROBE_PAGE,
                                   "order": "relevance"})
            r.raise_for_status()
            payload = r.json()
            raws = payload.get("items") or []
            if not raws:
                break
            pag = payload.get("pagination") or {}
            total = pag.get("total_entries") or total
            for raw in raws:
                login = login or ((raw.get("user") or {}).get("login"))
                if raw.get("is_closed") or raw.get("is_hidden") or raw.get("is_reserved"):
                    continue
                it = _wardrobe_item(raw)
                if it:
                    it["seller_id"] = it["seller_id"] or user_id
                    items[it["item_id"]] = it
            if page >= (pag.get("total_pages") or 1):
                break
    return {"seller": {"id": int(user_id), "login": login},
            "items": list(items.values()), "total": total}
