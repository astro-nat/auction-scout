"""PublicSurplus search client.

publicsurplus.com is a server-rendered site with no session or bot wall:
the region search page accepts zipCode + milesLocation and pages through
plain GETs, so this parses the listing grid it serves every browser. Like
GovDeals, every "auction" is one standalone item with its own soft-close
end time — but unlike GovDeals the listing rows don't say which agency is
selling, so the scan groups everything into ONE synthetic auction per
search area. Each lot's link opens the item page, where the agency and
exact pickup address live.

Parsed per row: the auction id, title, current price, thumbnail, and the
exact close time — the page embeds it as epoch millis in its countdown
script, which beats parsing "10 hours 31 mins" strings that go stale.
"""

import logging
import re
from datetime import datetime, timezone

import httpx

from .. import config

logger = logging.getLogger(__name__)

BASE = "https://www.publicsurplus.com"
PAGES_MAX = 20   # 25 rows a page; a runaway radius stops at 500 items

_TITLE_RE = re.compile(r'title="#(\d+) - ([^"]{1,200})"')
_PRICE_RE = re.compile(r'id="val_(\d+)searchGrid">\s*\$([\d,]+\.?\d*)')
_THUMB_RE = re.compile(r'src="(https://[^"]+/cdnmainaucdoc/thumb-b/(\d+)/\d+)"')
# updateTimeLeftSpan(timeLeftInfoMap, <aucId>, "<domId>", <nowMs>, <endMs>, ...)
_END_RE = re.compile(r'updateTimeLeftSpan\(timeLeftInfoMap,\s*(\d+),\s*"[^"]*",'
                     r'\s*\d+,\s*(\d+)')
_LASTPAGE_RE = re.compile(r"srchPage\('(\d+)'\)")


def _search_url(zip_code: str, miles: int, page: int) -> str:
    region = config.PUBLICSURPLUS_REGION
    return (f"{BASE}/sms/all,{region}/browse/search"
            f"?posting=y&endHours=-1&startHours=-1"
            f"&zipCode={zip_code}&milesLocation={miles}&page={page}")


def item_link(auction_id: int) -> str:
    return (f"{BASE}/sms/all,{config.PUBLICSURPLUS_REGION}"
            f"/auction/view?auc={auction_id}")


def parse_page(html: str) -> tuple[list[dict], int]:
    """One listing page → (items, last_page_index)."""
    import html as html_mod
    titles = {int(i): html_mod.unescape(t).strip()
              for i, t in _TITLE_RE.findall(html)}
    prices = {int(i): float(p.replace(",", ""))
              for i, p in _PRICE_RE.findall(html)}
    thumbs = {int(i): u for u, i in
              ((u, int(i)) for u, i in _THUMB_RE.findall(html))}
    ends = {}
    for i, ms in _END_RE.findall(html):
        try:
            ends[int(i)] = (datetime.fromtimestamp(int(ms) / 1000,
                                                   tz=timezone.utc)
                            .replace(tzinfo=None))
        except (ValueError, OSError, OverflowError):
            pass
    items = []
    for auc_id, title in titles.items():
        if not title:
            continue
        items.append({
            "auction_id": auc_id,
            "lot_id": f"ps-{auc_id}",
            "title": title[:500],
            "current_bid": prices.get(auc_id, 0.0),
            "closes_at": ends.get(auc_id),
            "thumbnail_url": thumbs.get(auc_id),
            # The listing page only links the 120px "thumb-b" rendition. The
            # same path with "thumb-a" serves the full photo (~20x the bytes),
            # and the vision pass needs it: from the thumbnail alone it read a
            # Denon deck's model as "DR-M11" - a guess from the layout - and
            # priced it against three-head decks worth four times as much.
            # The badge on the full photo says DRM-555.
            "fullsize_url": (thumbs[auc_id].replace("/thumb-b/", "/thumb-a/")
                             if thumbs.get(auc_id) else None),
            "lot_link": item_link(auc_id),
        })
    pages = [int(p) for p in _LASTPAGE_RE.findall(html)]
    return items, max(pages) if pages else 0


def search_items(zip_code: str, miles: int) -> list[dict]:
    """Every open item within the radius, deduped by id."""
    seen: dict[int, dict] = {}
    last_page = 0
    with httpx.Client(timeout=30, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"}) as client:
        page = 0
        while page <= min(last_page, PAGES_MAX):
            r = client.get(_search_url(zip_code, miles, page))
            r.raise_for_status()
            items, last_page = parse_page(r.text)
            if not items:
                break
            before = len(seen)
            for it in items:
                seen[it["auction_id"]] = it
            if len(seen) == before:   # a repeated page — stop rather than loop
                break
            page += 1
    return list(seen.values())
