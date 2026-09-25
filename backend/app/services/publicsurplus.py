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

The listing grid carries no description. Each item page does, and it is
where the condition actually lives: "**Does not have Hard Drive. **No
power cord." on a laptop the app had called normal wear and tear, because
the title and photo were all it ever saw. fetch_detail() pulls that text
for one item; the enrichment pass fetches it for a PublicSurplus lot that
has none before asking the model anything.
"""

import logging
import re
from datetime import datetime, timezone

import httpx

from .. import config

logger = logging.getLogger(__name__)

BASE = "https://www.publicsurplus.com"
PAGES_MAX = 20   # 25 rows a page; a runaway radius stops at 500 items
USER_AGENT = "Mozilla/5.0"

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


# The item page's description block, and the Condition field above it.
_DETAIL_RE = re.compile(r'overflow-wrap:\s*anywhere"\s*>(.*?)(?:<!--\s*DOCUMENTS|\Z)',
                        re.DOTALL | re.IGNORECASE)
_CONDITION_RE = re.compile(
    r'class="auctitle"\s*>\s*Condition:\s*</span>\s*<span[^>]*>(.*?)</span>',
    re.DOTALL | re.IGNORECASE)
# Agency legal boilerplate, identical on every lot from the same seller.
# It is most of the text and none of the information, and it would crowd
# the real condition note out of the model's prompt.
_BOILERPLATE_RE = re.compile(
    r"(\*+\s*ITEMS ARE SOLD|ITEMS ARE SOLD\s+\"?AS-?IS|WE DO NOT SHIP|"
    r"ALL SALES ARE FINAL|Winning bidder must pay and take possession|"
    r"We are not\s+experts on the items|Payment (?:is )?due|"
    r"Removal of (?:the )?item|By placing a bid)",
    re.IGNORECASE)
DESCRIPTION_MAX = 1200
MAX_LOT_IMAGES = 8
# The item page lists its photos as small "thumb=b" renditions of the same
# docviewer URL the full photo uses; "thumb=a" is the full one (217 KB vs
# 4 KB on the Dell). The URL redirects to a signed S3 link that expires, so
# the docviewer URL is what gets stored and the downloader follows it.
_PICTURE_RE = re.compile(r'src="(/sms/docviewer/aucdoc/[^"]+)"', re.IGNORECASE)


def _text(fragment: str) -> str:
    """HTML fragment -> readable text, with block tags as line breaks."""
    import html as html_mod
    t = re.sub(r"<\s*(br|/p|/div|/li|/tr)\s*/?>", "\n", fragment, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_mod.unescape(t).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    return re.sub(r"\n{2,}", "\n", t).strip()


def parse_detail(html: str) -> dict:
    """One item page → {"description", "condition"}.

    The description keeps what the seller said about THIS item and drops
    the legal boilerplate that follows it. Missing fields come back empty
    rather than raising: a lot with no description still prices.
    """
    m = _DETAIL_RE.search(html or "")
    text = _text(m.group(1)) if m else ""
    cut = _BOILERPLATE_RE.search(text)
    if cut:
        text = text[:cut.start()].strip()
    c = _CONDITION_RE.search(html or "")
    condition = _text(c.group(1)) if c else ""
    # The page states a condition of its own ("UNKNOWN", "Used"); it is
    # part of what the seller disclosed, so it rides with the description.
    if condition and condition.lower() not in text.lower():
        text = f"Condition: {condition}\n{text}".strip()
    seen, images = set(), []
    for src in _PICTURE_RE.findall(html or ""):
        url = BASE + src.replace("thumb=b", "thumb=a")
        if url not in seen:
            seen.add(url)
            images.append(url)
    return {"description": text[:DESCRIPTION_MAX].strip(), "condition": condition,
            "images": images[:MAX_LOT_IMAGES]}


def fetch_detail(auction_id: int) -> dict | None:
    """The item page for one lot. None on any network trouble - a missing
    description must never fail an enrichment."""
    try:
        with httpx.Client(timeout=20, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as client:
            r = client.get(item_link(auction_id))
            r.raise_for_status()
            return parse_detail(r.text)
    except Exception as exc:  # noqa: BLE001 - a detail page is a bonus, never a blocker
        logger.warning("PublicSurplus detail fetch failed for %s: %s", auction_id, exc)
        return None


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
                      headers={"User-Agent": USER_AGENT}) as client:
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
