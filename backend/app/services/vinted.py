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
from html import unescape

import httpx

logger = logging.getLogger(__name__)

BASE = "https://www.vinted.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0 Safari/537.36")
PAGES_MAX = 2   # newest-first: two pages of ~96 is the whole "fresh" window

_CARD_RE = re.compile(
    r'<img src="(https://images\d*\.vinted\.net/[^"]+)" '
    r'alt="([^"]+)"[^>]*data-testid="product-item-id-(\d+)--image--img"')
_ALT_RE = re.compile(
    r"^(?P<title>.*), Brand: (?P<brand>[^,]*), Condition: (?P<cond>[^,]*), "
    r"(?P<price>[\d.,]+) \$(?:, [\d.,]+ \$)?$")


def item_link(item_id: int, slug_hint: str = "") -> str:
    return f"{BASE}/items/{item_id}"


def parse_catalog(html: str) -> list[dict]:
    items = []
    for thumb, alt, item_id in _CARD_RE.findall(html):
        alt = unescape(alt)
        m = _ALT_RE.match(alt)
        if not m:
            # A reshuffled alt format should fail loudly in counts, not
            # silently import titles with prices glued on.
            logger.warning("Vinted alt did not parse: %r", alt[:120])
            continue
        try:
            price = float(m.group("price").replace(",", ""))
        except ValueError:
            continue
        brand = m.group("brand").strip()
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
