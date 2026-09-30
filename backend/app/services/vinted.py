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
# How deep a search goes. Two pages was called "the whole fresh window" and
# it is nothing of the sort: "cd lots" returns 960 listings over 10 pages, so
# two pages saw 20% of them. Vinted hands us its own pagination block, so the
# scan now runs to the end of the result set and this is only the stop.
# Cost measured against a live search: ~7 MB and ~1.5s per page.
PAGES_MAX = 10
WARDROBE_PAGE = 96      # the API's own maximum
WARDROBE_PAGES_MAX = 12 # ~1150 items; a guard against a shop account

_CARD_RE = re.compile(
    r'<img src="(https://images\d*\.vinted\.net/[^"]+)" '
    r'alt="([^"]+)"[^>]*data-testid="product-item-id-(\d+)--image--img"')
# Two shapes: clothing/housewares carry "Brand: X", media (CDs, books)
# goes straight to Condition. Tried in that order — a single regex with an
# optional brand group lets a greedy title swallow the brand instead.
# One pattern over however many labelled fields the card carries, instead of
# one pattern per field combination. The two-pattern version knew about Brand
# and Condition only, so a card with a Size - which is most of Vinted - matched
# neither and was dropped with a warning nobody read. Measured on live
# searches: "cd lots" lost nothing (media has no size), "nike shoes" lost 95 of
# 96 cards.
#
# The title is non-greedy so the field run takes as much as it can, which is
# what keeps a title's own "Size M" out of the Size field.
_ALT_RE = re.compile(
    r"^(?P<title>.+?)"
    r"(?P<fields>(?:, (?:Brand|Condition|Size): [^,]*)+)"
    r", (?P<price>[\d.,]+) \$(?:, [\d.,]+ \$)?$")
_ALT_FIELD_RE = re.compile(r", (Brand|Condition|Size): ([^,]*)")


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
        m = _ALT_RE.match(alt)
        if not m:
            # A reshuffled alt format should fail loudly in counts, not
            # silently import titles with prices glued on.
            logger.warning("Vinted alt did not parse: %r", alt[:120])
            continue
        fields = dict(_ALT_FIELD_RE.findall(m.group("fields")))
        try:
            price = float(m.group("price").replace(",", ""))
        except ValueError:
            continue
        brand = (fields.get("Brand") or "").strip()
        cond = (fields.get("Condition") or "").strip()
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


# Vinted states its own result size on every catalog page.
_PAGINATION_RE = re.compile(
    r'"pagination":\{"current_page":(\d+),"per_page":(\d+),'
    r'"time":\d+,"total_entries":(\d+),"total_pages":(\d+)')


def result_size(html: str) -> tuple[int, int]:
    """(total_entries, total_pages) as Vinted reports them, or (0, 0)."""
    m = _PAGINATION_RE.search(html.replace('\\"', '"'))
    return (int(m.group(3)), int(m.group(4))) if m else (0, 0)


def search_items(query: str, max_price: float | None = None,
                 max_pages: int | None = None) -> dict:
    """Newest listings matching the query, deduped by id.

    Returns the items AND whether the whole result set was read, because the
    caller uses absence as evidence: a lot it does not see again is treated
    as sold. That inference is only sound over a COMPLETE result set. Two
    pages of a ten-page search made it wrong in both directions - live lots
    closed for scrolling out of the window, and genuinely sold ones kept
    alive because the window never reached them.
    """
    cap = PAGES_MAX if max_pages is None else max(1, max_pages)
    seen: dict[int, dict] = {}
    total_entries = total_pages = 0
    pages_read = 0
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True,
                      timeout=60) as client:
        client.get(f"{BASE}/")   # sets the anonymous session cookies
        for page in range(1, cap + 1):
            params = {"search_text": query, "order": "newest_first",
                      "page": page}
            if max_price:
                params["price_to"] = max_price
                params["currency"] = "USD"
            r = client.get(f"{BASE}/catalog", params=params)
            r.raise_for_status()
            if page == 1:
                total_entries, total_pages = result_size(r.text)
            items = parse_catalog(r.text)
            pages_read = page
            if not items:
                break
            before = len(seen)
            for it in items:
                seen[it["item_id"]] = it
            if len(seen) == before:
                break
            if total_pages and page >= total_pages:
                break
    # Complete when Vinted's own page count was reached, or when it never
    # told us and the run stopped short of the cap on its own.
    complete = (pages_read >= total_pages) if total_pages else (pages_read < cap)
    return {"items": list(seen.values()), "complete": complete,
            "pages_read": pages_read, "total_pages": total_pages,
            "total_entries": total_entries}


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


def seller_stats(user_id: int, client: "httpx.Client | None" = None) -> dict:
    """What a seller account has behind it: rating, how many ratings, how
    many things listed, how many bought.

    Two calls, because Vinted splits them: /user_feedbacks/summary carries
    the rating and its count, /users/<id> the closet size and what they have
    bought. Neither is on the item payload, which is why a listing alone
    cannot tell a burner account from a seller.

    Never raises. A seller whose stats will not load is left unknown rather
    than assumed guilty - services/seller.untrusted only acts on what it can
    see, and an unknown seller keeps the benefit of the doubt.
    """
    out = {"rating": None, "feedback_count": None,
           "item_count": None, "bought_count": None}
    own = client is None
    if own:
        client = httpx.Client(headers={"User-Agent": UA, "Accept": "application/json"},
                              follow_redirects=True, timeout=30)
        _session(client)
    try:
        try:
            r = client.get(f"{BASE}/api/v2/user_feedbacks/summary",
                           params={"user_id": user_id})
            if r.status_code == 200:
                f = r.json().get("user_feedback_summary") or {}
                out["rating"] = float(f["feedback_rating"]) if f.get("feedback_rating") else None
                out["feedback_count"] = f.get("feedback_count")
        except Exception as exc:  # noqa: BLE001 - unknown, not guilty
            logger.debug("Vinted feedback lookup failed for %s: %s", user_id, exc)
        try:
            r = client.get(f"{BASE}/api/v2/users/{user_id}")
            if r.status_code == 200:
                u = r.json().get("user") or {}
                out["item_count"] = u.get("item_count")
                out["bought_count"] = u.get("taken_item_count")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Vinted user lookup failed for %s: %s", user_id, exc)
    finally:
        if own:
            client.close()
    return out


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
