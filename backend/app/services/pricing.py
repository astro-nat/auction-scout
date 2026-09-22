"""Resale-price estimation from marketplace comps.

Ported from the prototype's ebay_prices.py, trimmed to the sanctioned APIs:
  1. SoldComps API (sold listings, 90 days) — used when SOLDCOMPS_API_KEY is set.
  2. eBay Browse API active listings — the fallback price signal.
The prototype's raw eBay HTML scraping tier is deliberately dropped (captcha
arms race), as are its dead integrations (Mercari, GoCollect).

All the anti-garbage filters carry over: relevance filtering (majority of query
tokens must appear in a comp title), quantity-mismatch filtering (single item vs
"lot of 12"), IQR outlier trimming, the variance cap, and the generic-title
single-comp ceiling. Each guard exists because a real lot got mispriced without it.
"""

import logging
import math
import os
import re
import statistics
import threading
import time
from typing import Optional

from datetime import datetime, timedelta, timezone

import httpx

from . import ebay

logger = logging.getLogger(__name__)

SOLDCOMPS_API_KEY = os.environ.get("SOLDCOMPS_API_KEY", "")

# --- Retail price printed in the lot title ------------------------------
# Amazon returns/overstock houses lead every title with the item's retail
# price: "$30 Hagerty Flatware Silver Dip", "New $122 Suptek 3-Shelf
# Bracket". On those catalogs it's a far better signal than comps — the
# goods are new, generic, and drown a keyword comp search in noise.
#
# Anchored at the START (after an optional condition word) on purpose. A
# mid-title dollar amount is nearly always something else: "Get $5.00 Store
# Credit", "$1 Starts", a model number, a promo.
_TITLE_RETAIL_RE = re.compile(
    r"""^\s*
        (?:(?:brand\s+new|open\s+box|like\s+new|pre-?owned|refurb(?:ished)?|
              new|opened|sealed|unsealed|used|tested|untested|missing|
              incomplete|damaged|dented|torn|scratched|cracked|stained|
              boxed|nib|nwt|nip)
           [\s\-:|,]*){0,3}
        \$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d{1,4}(?:\.\d{2})?)\b
    """,
    re.IGNORECASE | re.VERBOSE)

# Bid-speak rather than a sticker price: "$1 Starts", "$10 off", "$5 Store
# Credit". Only checked in the short run of text AFTER the amount — scanning
# the whole title rejected "New $35 NEW Compound W Freeze Off Wart Remover"
# on the "Off" in the product name.
_RETAIL_BID_SPEAK = re.compile(
    r"^\W*(?:start(?:s|ing)?|bid|bids|bidding|increment|reserve|minimum|min|"
    r"off|credit|coupon|rebate|discount)\b",
    re.IGNORECASE)

# Face value, not a retail sticker: a $50 gift card resells for about $45,
# not half. Checked across the whole title. Currency itself needs no entry
# here — "1976 $2 Bill" doesn't lead with the price, so the anchor rejects it.
_RETAIL_FACE_VALUE = re.compile(
    r"\b(?:gift\s?card|giftcard|e-?gift|gift\s+certificate|voucher|"
    r"store\s+credit)\b",
    re.IGNORECASE)

# Liquidation goods resell for roughly half their retail sticker.
MSRP_REALIZATION = float(os.environ.get("MSRP_REALIZATION", "0.5"))
# Outside this range the leading number is almost certainly not a price.
_RETAIL_MIN, _RETAIL_MAX = 3.0, 5000.0


def retail_from_title(title: str) -> Optional[float]:
    """The retail price a liquidation house stamped on the front of a title.

    Returns None when the title has no leading price, when the number is
    implausible as retail, or when the dollar figure is a face value rather
    than a sticker price.
    """
    if not title:
        return None
    m = _TITLE_RETAIL_RE.match(title)
    if not m:
        return None
    if _RETAIL_FACE_VALUE.search(title):
        return None
    if _RETAIL_BID_SPEAK.match(title[m.end():m.end() + 24]):
        return None
    try:
        value = float(m.group(1).replace(",", ""))   # "$1,200"
    except ValueError:
        return None
    return value if _RETAIL_MIN <= value <= _RETAIL_MAX else None


# Condition words the liquidation houses stamp ahead of the price, mapped to
# the same verdict vocabulary the AI pass uses. Reading them off the title is
# free, so a lot whose price is already printed needs no model call at all.
_TITLE_CONDITION = [
    (re.compile(r"\b(?:missing|incomplete|damaged|dented|torn|cracked|"
                r"for\s+parts)\b", re.IGNORECASE), "broken, damaged, or for parts"),
    (re.compile(r"\b(?:sealed|brand\s+new|nib|nwt|nip)\b", re.IGNORECASE),
     "mint condition or working perfectly"),
    (re.compile(r"\b(?:opened|open\s+box|used|pre-?owned|refurb(?:ished)?|"
                r"like\s+new|stained|scratched|tested)\b", re.IGNORECASE),
     "normal wear and tear"),
]


def condition_from_title(title: str) -> str:
    """Condition verdict from the words a liquidation house puts up front.

    Only the prefix is read — the words that appear before the price are the
    house's condition grade, whereas the same word later in the string is
    usually part of the product ("Compound W Freeze Off", "New York Yankees").

    Defaults to normal wear: overstock is mostly unused, and the resale
    figure is already halved, so a second haircut would double-discount.
    """
    m = _TITLE_RETAIL_RE.match(title or "")
    prefix = (title or "")[:m.start(1)] if m else (title or "")[:24]
    for pattern, verdict in _TITLE_CONDITION:
        if pattern.search(prefix):
            return verdict
    return "normal wear and tear"


def title_without_retail_prefix(title: str) -> str:
    """The product name with the house's price and condition stamp removed —
    what you'd actually type into a search box."""
    m = _TITLE_RETAIL_RE.match(title or "")
    if not m:
        return (title or "").strip()
    rest = (title or "")[m.end():].lstrip(" -:|,").strip()
    return rest or (title or "").strip()


# House boilerplate posing as lots. Auction software pads catalogs with
# rows like "More Lots Loading....." and "Pick Up Location" — there is no
# item, so any money or model time spent pricing one produces a confident
# number for nothing (a placeholder priced at $474.19 off a motorcycle part
# and a stamp album; a pickup-location row hallucinated into a $7,734
# building). Full-title match only: "Preview" alone is boilerplate,
# "Preview Dresser" is furniture.
_PLACEHOLDER_RE = re.compile(
    r"^\s*(?:"
    r"more +lots +(?:loading|coming|to +come|being +added).*"
    r"|lots? +loading"
    r"|pick[ -]?up +(?:location|info(?:rmation)?|details|instructions|only).*"
    r"|preview(?: +(?:night|day|info(?:rmation)?|times?|hours?))?"
    r"|payment +(?:info(?:rmation)?|details|instructions)"
    r"|shipping +(?:info(?:rmation)?|details|instructions|available)"
    r"|terms(?: +(?:and|&) +conditions)?(?: +of +sale)?"
    r"|auction +(?:info(?:rmation)?|details|terms)"
    r"|removal(?: +(?:info(?:rmation)?|details|times?|day))?"
    r"|welcome(?: +to +.{0,40})?"
    r"|thank +you.{0,40}"
    r"|(?:test|sample) +lot"
    r"|do +not +bid.*"
    r"|read +(?:before +bidding|description|terms).*"
    # Policy announcements, optionally led by a year: "2026 PICK UP POLICY
    # UPDATE - PLEASE READ!!!" was priced at $150 off 28 comps while the
    # model itself noted 'no item to resell'. The tail is bounded to
    # announcement vocabulary — "1943 Payment Options Ledger Book" is an
    # antique, and a greedy tail would have silenced it.
    r"|(?:\d{4} +)?(?:overview +of +)?(?:updated? +)?"
    r"(?:pick[ -]?up|shipping|payment|bidding|auction) +"
    r"(?:polic(?:y|ies)|options?|schedules?|updates?|changes?)"
    r"(?: *(?:[&+]|and)? *(?:polic(?:y|ies)|options?|schedules?|updates?|changes?))*"
    r"(?: *[-–—:]* *please +read!*)?"
    r")[\s.!*_\-]*$",
    re.IGNORECASE)


def is_placeholder_title(title: str) -> bool:
    """True when a lot 'title' is catalog boilerplate, not an item."""
    return bool(title) and bool(_PLACEHOLDER_RE.match(title))


# --- Titled vehicles ----------------------------------------------------
from .. import config as _config  # noqa: E402 — regexes live with their kin

_VEH_CATEGORY_RE = re.compile(_config.VEHICLE_CATEGORY, re.I)
_VEH_CATEGORY_GUARD_RE = re.compile(_config.VEHICLE_CATEGORY_GUARD, re.I)
_VEH_NOUNS_RE = re.compile(_config.VEHICLE_NOUNS, re.I)
_VEH_MARKERS_RE = re.compile(_config.VEHICLE_MARKERS, re.I)
_VEH_TOY_GUARD_RE = re.compile(_config.VEHICLE_TOY_GUARD, re.I)


def is_titled_vehicle(title: str, category: str | None = None) -> bool:
    """True when the lot is a DMV-paperwork vehicle, not a parcel.

    Category first — the platforms label vehicles cleanly ("Automobiles/
    Cars", "SUV", "Motor Pool") — then title evidence: an unambiguous
    vehicle noun, or paperwork/odometer/year+make markers. Toys, models,
    parts and accessory lots stay out on either path."""
    title = title or ""
    if _VEH_TOY_GUARD_RE.search(title):
        return False
    cat = category or ""
    if (_VEH_CATEGORY_RE.search(cat)
            and not _VEH_CATEGORY_GUARD_RE.search(cat)):
        return True
    return bool(_VEH_NOUNS_RE.search(title)
                or _VEH_MARKERS_RE.search(title))


def price_from_title(title: str) -> Optional[dict]:
    """A comps-shaped result built from the title's retail price, or None.

    Same shape as lookup_comps so callers can use the two interchangeably.
    comp_count is 1 — a single data point, but an authoritative one: the
    price the item actually sells for new.
    """
    retail = retail_from_title(title)
    if retail is None:
        return None
    est = round(retail * MSRP_REALIZATION, 2)
    return {
        "est_resale": est,
        "price_low": round(est * 0.75, 2),
        "price_high": round(est * 1.25, 2),
        "comp_count": 1,
        "price_source": f"retail ${retail:g} in title ×{MSRP_REALIZATION:g}",
        "comps": [{"price": retail, "title": "retail price printed in the lot title",
                   "url": None, "date": None, "kind": "retail"}],
    }


# A printed retail claim at or above this gets a market cross-check. Below
# it, being wrong costs a few dollars and the check costs comp quota; above
# it, an inflated sticker mints a false gold mine — a "$556 retail" racing
# wheel whose used market is ~$130 was badged GOLD MINE at a $111 max bid.
RETAIL_VERIFY_MIN = 150.0


def verified_title_price(title: str, search_title: str | None = None) -> Optional[dict]:
    """price_from_title, cross-checked against the market when the claim is
    big enough to hurt.

    Liquidation houses print MSRPs that can be stale, list-price fiction,
    or plain wrong — the claim is a data point, not an appraisal. Cheap
    claims pass through untouched (zero-cost pricing is the whole feature);
    expensive ones get a comp lookup and the LOWER answer wins. No market
    data leaves the claim standing, labeled as unchecked.
    """
    titled = price_from_title(title)
    if titled is None:
        return None
    retail = retail_from_title(title)
    if retail is None or retail < RETAIL_VERIFY_MIN:
        return titled
    market = lookup_comps(search_title or title_without_retail_prefix(title)
                          or title)
    if market["est_resale"] and float(market["est_resale"]) < float(titled["est_resale"]):
        market = dict(market)
        market["price_source"] += (
            f" — beat the retail ${retail:g} claim "
            f"(×{MSRP_REALIZATION:g} would say ${titled['est_resale']:g})")
        return market
    titled = dict(titled)
    titled["price_source"] += (" (market-checked)" if market["est_resale"]
                               else " (no comps to check the claim)")
    return titled


# Active eBay listings are ASKING prices — what sellers hope for, often for
# new stock — while we're valuing a used lot from an auction. Realized sale
# prices run well below asking, so discount them. Sold-price sources
# (SoldComps) are already realized and get no discount.
# Tune with ACTIVE_REALIZATION; 1.0 disables the adjustment.
ACTIVE_REALIZATION = float(os.environ.get("ACTIVE_REALIZATION", "0.65"))

_PRICE_MIN, _PRICE_MAX = 0.99, 50000.0
_MIN_FULL_COMPS = 3
_QUERY_WORD_CAPS = (6, 4, 3)

_STOPWORDS = {"the", "and", "for", "with", "of", "to", "in", "on", "a", "an",
              "by", "or", "new", "used", "set", "size"}

_CONDITION_NOISE = re.compile(
    r"\b(very good|like new|brand new|open box|no in packaging|in original packaging"
    r"|no packaging|condition|damaged|untested|for parts|as-?is|sealed|unopened"
    r"|unused|new|used|good|fair|poor|mint|excellent)\b",
    re.IGNORECASE,
)
_BULK_RE = re.compile(
    r"lot of \d+|\d+\s*(pcs|pieces|cars|count)\b|collection|bundle|huge lot"
    r"|large lot|case of|wholesale|dealer lot|estate lot"
    # Multi-packs: a "3 Pack" wheeled-hamper listing was setting the price of
    # a single laundry basket.
    r"|\d+\s*-?\s*pack\b|pack of \d+|set of \d+|\bpair of\b|\b\d+x\s",
    re.IGNORECASE,
)
_NOS_RE = re.compile(
    r"\bNOS\b|new old stock|\bMIB\b|\bNIB\b|\bMISB\b|sealed|unopened"
    r"|new in (box|package)|factory[- ]sealed|brand[- ]new|deadstock",
    re.IGNORECASE,
)
_SPECIFIC_RE = re.compile(
    r"#\d+|\b(CGC|PSA|BGS|SGC|CBCS|ANACS)\b|1st edition|\b(19|20)\d{2}\b"
    r"|autograph|signed|\bauto\b|sealed",
    re.IGNORECASE,
)


# ------------------------------------------------------------- query building

def clean_title(title: str) -> str:
    """Strip auction-listing noise so the title works as a marketplace query."""
    t = title or ""
    t = re.sub(r"\$\d+(\.\d+)?", " ", t)                      # retail-value hints poison search
    t = re.sub(r"\b(retail( value)?|msrp|est(imated)? (value|worth))\b", " ", t, flags=re.I)
    t = re.sub(r"^qty[-: ]*\d+\s*", "", t, flags=re.I)
    t = _CONDITION_NOISE.sub(" ", t)
    t = re.sub(r"\((.{0,25})\)", " ", t)                       # short parentheticals
    t = re.sub(r"[,;:/\\|-]+", " ", t)                          # eBay treats " - " as NOT
    t = re.sub(r"\s+", " ", t).strip()
    # pop trailing connector fragments
    words = t.split()
    while words and (words[-1].lower() in {"and", "or", "the", "with", "for", "&"}
                     or len(words[-1]) == 1):
        words.pop()
    return " ".join(words)


# Measurement-ish trailing tokens ("18x21", '21"', "12in", "30cm", "5pc").
# These are NOT the item noun, and keeping one as the anchor word produced
# queries like "Antique Still Life 18x21" that match almost nothing — the
# handful of listings that do match then price the lot off an anecdote.
_DIMENSION_RE = re.compile(
    r"""^(?:\d+(?:[.,]\d+)?\s*(?:x|by)\s*\d+(?:[.,]\d+)?  # 18x21
        |\d+(?:[.,]\d+)?\s*(?:"|''|in|inch|inches|cm|mm|ft|lb|lbs|oz|pc|pcs|pk)
        |\d+(?:[.,]\d+)?)$""",
    re.IGNORECASE | re.VERBOSE)


def query_variants(title: str) -> list[str]:
    """Progressively shorter queries. eBay returns zero results for very long
    queries; 4-6 words is the sweet spot. When truncating, always keep the
    LAST MEANINGFUL word — enriched titles end with the item-type noun
    ("...Ironwood 18 Head Statue"), and dropping it comps a statue against
    generic 'vintage african' listings. Trailing dimensions are skipped when
    picking that anchor; they describe the item, they don't identify it."""
    cleaned = clean_title(title)
    words = cleaned.split()
    # Anchor on the last non-dimension token.
    anchor_idx = len(words) - 1
    while anchor_idx > 0 and _DIMENSION_RE.match(words[anchor_idx]):
        anchor_idx -= 1
    anchor = words[anchor_idx:anchor_idx + 1]
    variants, seen = [], set()

    def add(tokens):
        v = " ".join(tokens)
        if len(v) >= 5 and v.lower() not in seen:
            seen.add(v.lower())
            variants.append(v)

    add(words[:8])  # near-full title first — most specific match wins
    for cap in _QUERY_WORD_CAPS:
        if len(words) > cap:
            head = [w for w in words[:cap - 1] if w != anchor[0]]
            add(head + anchor)  # keep the item noun, not a stray dimension
        else:
            add(words[:cap])
    return variants


# ------------------------------------------------------------------ filtering

def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower())
            if t not in _STOPWORDS}


def _relevant(query: str, comp_title: str) -> bool:
    """Comp counts only if enough query tokens (prefix-)match its title.
    Consistently-priced-but-WRONG comps are invisible to variance checks —
    this filter is what catches them. Specific queries (4+ meaningful words,
    e.g. enriched vision titles) demand a 2/3 match; short ones settle for
    half, or they'd never match anything."""
    if not comp_title:
        return True
    q, c = _tokens(query), _tokens(comp_title)
    if not q:
        return True
    hits = sum(1 for qt in q
               if any(ct.startswith(qt) or qt.startswith(ct) for ct in c))
    needed = math.ceil(len(q) * 2 / 3) if len(q) >= 4 else math.ceil(len(q) / 2)
    return hits >= needed


# Model codes mix letters and digits (U818A, F181W, HS290, 505-626) and ARE
# the product's identity — a "Holy Stone U818A" priced off an F181W, or a
# "Grand Gourmet" roaster priced off "Gourmet Standard", is a wrong comp.
# Dimensions (10x10, 8x10) and short capacity tags (53L, 14L) are excluded.
_MODEL_RE = re.compile(
    r"\b(?![\d]+x[\d]+\b)(?=[a-z0-9-]*[a-z])(?=[a-z0-9-]*\d)[a-z0-9][a-z0-9-]{3,}\b",
    re.IGNORECASE,
)


def _model_codes(text: str) -> set[str]:
    return {m.group(0).lower() for m in _MODEL_RE.finditer(text or "")}


def _model_match(query: str, comp_title: str) -> bool:
    """If the query names a specific model, the comp has to name it too."""
    codes = _model_codes(query)
    if not codes:
        return True
    comp = (comp_title or "").lower()
    return any(c in comp for c in codes)


def _quantity_match(query: str, comp_title: str) -> bool:
    return bool(_BULK_RE.search(query)) == bool(_BULK_RE.search(comp_title or ""))


# Kids vs adult apparel price entirely differently — a kids Nike hoodie
# priced off adult listings is a wrong comp. If the query declares an
# audience, comps declaring the OPPOSITE audience are rejected; silent
# comps still count (requiring the token would empty the comp pool).
# "jr"/"junior" deliberately absent — sports cards ("Griffey Jr") collide.
_KID_RE = re.compile(
    r"\b(kids?|youth|toddlers?|infants?|baby|babies|boys?|girls?|child|childrens?)\b",
    re.IGNORECASE)
_ADULT_RE = re.compile(r"\b(men'?s?|women'?s?|ladies|adult)\b", re.IGNORECASE)


def _audience_match(query: str, comp_title: str) -> bool:
    comp = comp_title or ""
    if _KID_RE.search(query) and _ADULT_RE.search(comp) and not _KID_RE.search(comp):
        return False
    if _ADULT_RE.search(query) and _KID_RE.search(comp) and not _ADULT_RE.search(comp):
        return False
    return True



# Items that were never sold at retail: club gifts, loyalty premiums,
# giveaways. They carry the brand and often the same shape as the real
# product, so a keyword comp search lands squarely on the retail range -
# which is how a Swarovski SCS renewal gift worth about $25 came back at
# $370 off twenty genuine sold comps for actual crystal eggs.
#
# The asymmetry matters. A giveaway comped against retail is inflated by an
# order of magnitude; a retail item comped against a giveaway is dragged
# down, which is the safe direction. So the rule fires one way only: if the
# LOT is a giveaway, its comps must be giveaways too.
_PROMO_RE = re.compile(
    r"\b(?:"
    r"scs|crystal\s+society|"
    r"member(?:ship)?\s+(?:gift|exclusive|piece)|"
    r"renewal\s+gift|loyalty\s+gift|club\s+(?:gift|piece|exclusive)|"
    r"gift\s+with\s+purchase|gwp|free\s+gift|"
    r"not\s+for\s+resale|nfr|"
    r"promotional\s+(?:item|giveaway)|giveaway|"
    r"dealer\s+(?:gift|premium)|advertising\s+premium"
    r")\b",
    re.IGNORECASE)


def _promo_match(query: str, comp_title: str) -> bool:
    """False when a giveaway is being priced against retail stock.

    One-directional on purpose - see _PROMO_RE. Only the lot being a
    giveaway constrains what may comp it.
    """
    if not _PROMO_RE.search(query or ""):
        return True
    return bool(_PROMO_RE.search(comp_title or ""))


def _iqr_filter(prices: list[float]) -> list[float]:
    if len(prices) < 4:
        return prices
    q1, _, q3 = statistics.quantiles(prices, n=4)
    fence = 1.5 * (q3 - q1)
    return [p for p in prices if q1 - fence <= p <= q3 + fence]


def _iqr_records(comps: list[dict]) -> list[dict]:
    """_iqr_filter over comp records — same fences, but each surviving price
    keeps its evidence (title, url, date) attached for display."""
    if len(comps) < 4:
        return comps
    q1, _, q3 = statistics.quantiles([c["price"] for c in comps], n=4)
    fence = 1.5 * (q3 - q1)
    return [c for c in comps if q1 - fence <= c["price"] <= q3 + fence]


# ---------------------------------------------------------------- comp sources

# --- SoldComps pacing ------------------------------------------------------
# One lot asks for up to four query variants, the worker runs three lots at
# once, and an itemised lot asks four per item — so a single "enrich these"
# could fire fifty requests in a second or two. That earned a wall of HTTP
# 429s, and because a 429 returned "no comps" the lot quietly fell through to
# eBay ASKING prices. The rate limit wasn't just noise in the log; it was
# silently swapping real sold data for the weaker signal.
SOLDCOMPS_RPS = float(os.environ.get("SOLDCOMPS_RPS", "2"))
SOLDCOMPS_MAX_RETRIES = int(os.environ.get("SOLDCOMPS_MAX_RETRIES", "3"))
# Repeated variants of similar titles ("Vintage Souvenir Plates Set" vs
# "Vintage Souvenir Set") ask the same thing minutes apart. Sold prices over
# a 90-day window don't move in fifteen minutes.
SOLDCOMPS_CACHE_SECONDS = float(os.environ.get("SOLDCOMPS_CACHE_SECONDS", "900"))
_CACHE_MAX_ENTRIES = 2000
# After this many 429s in a row, stop asking for a while. Pacing should keep
# us clear; if it hasn't, the budget is gone and hammering only makes the
# backoff worse for everything behind us.
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN = float(os.environ.get("SOLDCOMPS_COOLDOWN_SECONDS", "120"))
# A Retry-After at or above this means the quota is gone rather than the
# request rate being too high. Pacing cannot help; only time or a bigger plan
# can, so back off hard instead of burning worker minutes on doomed retries.
_QUOTA_WALL_SECONDS = float(os.environ.get("SOLDCOMPS_QUOTA_WALL_SECONDS", "10"))
_QUOTA_COOLDOWN = float(os.environ.get("SOLDCOMPS_QUOTA_COOLDOWN_SECONDS", "900"))


class _Throttle:
    """Least-surprising rate limiter: hand out evenly spaced slots.

    Shared by every worker thread, so the limit is a property of the process
    rather than of one call site. The sleep happens OUTSIDE the lock — held
    through the sleep, threads would serialise instead of pipelining.
    """

    def __init__(self, rps: float):
        self._interval = 1.0 / rps if rps > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._interval
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)


_throttle = _Throttle(SOLDCOMPS_RPS)
_cache: dict[str, tuple[float, list]] = {}
_cache_lock = threading.Lock()
_breaker_lock = threading.Lock()
_consecutive_429 = 0
_blocked_until = 0.0


def _cache_get(key: str):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        if hit:
            _cache.pop(key, None)
    return None


def _cache_put(key: str, value: list) -> None:
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX_ENTRIES:
            _cache.clear()          # crude, but this is a cache, not a store
        _cache[key] = (time.monotonic() + SOLDCOMPS_CACHE_SECONDS, value)


def _breaker_open() -> bool:
    with _breaker_lock:
        return time.monotonic() < _blocked_until


def _open_breaker(reason: str, seconds: float | None = None) -> None:
    """Stop asking for a while, and say why in terms the log reader can act on."""
    global _blocked_until
    cooldown = _QUOTA_COOLDOWN if seconds is None else seconds
    with _breaker_lock:
        if time.monotonic() < _blocked_until:
            return                      # already paused; don't re-log per call
        _blocked_until = time.monotonic() + cooldown
    logger.warning(
        "SoldComps unavailable (%s) — pausing sold-comp lookups for %.0fs. "
        "Prices set during this window come from eBay ACTIVE listings, which "
        "are asking prices, not sold ones.", reason, cooldown)


def _note_429() -> None:
    global _consecutive_429, _blocked_until
    with _breaker_lock:
        _consecutive_429 += 1
        over = _consecutive_429 >= _BREAKER_THRESHOLD
    if over:
        _open_breaker(reason=f"{_consecutive_429} rate limits in a row",
                      seconds=_BREAKER_COOLDOWN)


def _note_ok() -> None:
    global _consecutive_429
    with _breaker_lock:
        _consecutive_429 = 0


def _retry_after_seconds(response, attempt: int) -> float:
    """Honour the server's own Retry-After when it sends one."""
    raw = response.headers.get("Retry-After") if response is not None else None
    if raw:
        try:
            return min(float(raw), 30.0)
        except ValueError:
            pass
    return min(2.0 ** attempt, 30.0)   # 1s, 2s, 4s…



# --- durable cache -------------------------------------------------------
# The in-process cache above is the first hop. This one survives restarts
# and spans jobs, which is where the real saving is: a re-price of the whole
# catalogue within the TTL costs almost no API calls at all.
#
# Seven days because the underlying data is a 90-day trailing window of
# completed sales. It does not move in a week, and pretending otherwise is
# what makes the quota the binding constraint.
COMP_CACHE_DAYS = float(os.environ.get("COMP_CACHE_DAYS", "7"))
# A cache, not an archive: enough rows to price against, not every result.
_CACHE_MAX_ITEMS = 60
_db_cache_stats = {"hit": 0, "miss": 0}


def _db_cache_get(query: str, source: str):
    """Cached answer, or None. Never raises - a cache that breaks the
    pipeline is worse than no cache."""
    from ..database import SessionLocal
    from .. import models
    db = None
    try:
        db = SessionLocal()
        cutoff = datetime.now(timezone.utc) - timedelta(days=COMP_CACHE_DAYS)
        row = (db.query(models.CompCache)
                 .filter(models.CompCache.query == query,
                         models.CompCache.source == source,
                         models.CompCache.created_at >= cutoff).first())
        if row is None:
            _db_cache_stats["miss"] += 1
            return None
        row.hits = (row.hits or 0) + 1
        db.commit()
        _db_cache_stats["hit"] += 1
        return [(float(p), t) for p, t in (row.payload or [])]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Comp cache read failed for %r: %s", query, exc)
        return None
    finally:
        if db is not None:
            db.close()


def _db_cache_put(query: str, source: str, comps: list) -> None:
    from ..database import SessionLocal
    from .. import models
    db = None
    try:
        db = SessionLocal()
        payload = [[float(p), (t or "")[:200]] for p, t in comps[:_CACHE_MAX_ITEMS]]
        row = (db.query(models.CompCache)
                 .filter(models.CompCache.query == query,
                         models.CompCache.source == source).first())
        if row is None:
            db.add(models.CompCache(query=query, source=source, payload=payload))
        else:
            row.payload = payload
            row.created_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Comp cache write failed for %r: %s", query, exc)
    finally:
        if db is not None:
            db.close()


def cache_stats() -> dict:
    total = _db_cache_stats["hit"] + _db_cache_stats["miss"]
    return {**_db_cache_stats,
            "hit_rate": round(_db_cache_stats["hit"] / total, 3) if total else None}


def _soldcomps_lookup(query: str, count: int = 120) -> list[tuple[float, str]]:
    """SoldComps API — real sold prices over the last 90 days.

    Paced, cached and retried, because a 429 here is not a harmless miss:
    an empty result sends the lot to eBay active listings, so being rate
    limited quietly downgrades a sold-price estimate to an asking-price one.
    """
    if not SOLDCOMPS_API_KEY:
        return []
    key = f"{query}|{count}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    # Durable hop: survives restarts and spans jobs, so a re-price inside
    # the TTL asks the API almost nothing.
    cached = _db_cache_get(key, "soldcomps")
    if cached is not None:
        _cache_put(key, cached)
        return cached
    if _breaker_open():
        return []

    last_status = None
    for attempt in range(SOLDCOMPS_MAX_RETRIES):
        _throttle.wait()
        try:
            with httpx.Client(timeout=40.0) as client:
                r = client.get(
                    "https://api.sold-comps.com/v1/scrape",
                    headers={"Authorization": f"Bearer {SOLDCOMPS_API_KEY}"},
                    params={"keyword": query, "count": min(max(count, 1), 240),
                            "daysToScrape": 90},
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("SoldComps failed for %r: %s", query, exc)
            return []

        last_status = r.status_code
        if r.status_code == 429:
            wait = _retry_after_seconds(r, attempt)
            # A long Retry-After is not "slow down", it's "you're out". The
            # first call from a cold container came back 429 with ~30s, which
            # no amount of pacing can fix — the plan's quota is spent.
            # Retrying that costs a minute of worker time per query variant
            # and cannot succeed, so stop asking and let the caller fall
            # through to active listings immediately.
            if wait >= _QUOTA_WALL_SECONDS:
                _open_breaker(reason=f"Retry-After {wait:.0f}s — quota, not pace")
                return []
            _note_429()
            if attempt < SOLDCOMPS_MAX_RETRIES - 1 and not _breaker_open():
                time.sleep(wait)
                continue
            logger.warning("SoldComps rate-limited on %r, giving up after %d "
                           "attempts", query, attempt + 1)
            return []
        if r.status_code != 200:
            logger.warning("SoldComps HTTP %s for %r", r.status_code, query)
            return []

        _note_ok()
        out = []
        for item in r.json().get("items", []) or []:
            raw = str(item.get("soldPrice") or "").replace("$", "").replace(",", "")
            try:
                p = float(raw)
            except ValueError:
                continue
            if _PRICE_MIN < p < _PRICE_MAX:
                # Full records, not bare prices: the comps ARE the evidence,
                # and showing them is what saves the user a manual re-search.
                # url/date field names are defensive — the API's item shape
                # isn't contractual, and a missing link is just an unlinked row.
                out.append({"price": p, "title": item.get("title") or "",
                            "url": item.get("link") or item.get("url")
                                   or item.get("itemUrl"),
                            "date": item.get("soldDate") or item.get("dateSold")
                                    or item.get("date"),
                            "kind": "sold"})
        # Cached even when empty: "nothing sold matching this" is an answer,
        # and re-asking it per variant is what built the burst.
        _cache_put(key, out)
        _db_cache_put(key, "soldcomps", out)
        return out

    logger.warning("SoldComps gave up on %r (last status %s)", query, last_status)
    return []

def _active_lookup(query: str) -> list[dict]:
    """eBay Browse active fixed-price listings — the always-available fallback."""
    out = []
    for item in ebay.search_active(query, limit=20):
        try:
            p = float((item.get("price") or {}).get("value") or 0)
        except (ValueError, TypeError):
            continue
        if p > _PRICE_MIN:
            out.append({"price": p, "title": item.get("title") or "",
                        "url": item.get("itemWebUrl"), "date": None,
                        "kind": "asking"})
    return out


# ------------------------------------------------------------------ main entry

def lookup_comps(title: str) -> dict:
    """Estimate resale value for one lot title.

    Returns {est_resale, price_low, price_high, comp_count, price_source} —
    est_resale is None when nothing priced.
    """
    result = {"est_resale": None, "price_low": None, "price_high": None,
              "comp_count": 0, "price_source": None, "comps": []}
    # Boilerplate rows can't be priced, only mispriced: searching "More Lots
    # Loading" once matched a motorcycle part and a stamp album into $474.19.
    if is_placeholder_title(title):
        return result
    variants = query_variants(title)
    if not variants:
        return result

    best_partial: Optional[tuple] = None
    for query in variants:
        comps = _soldcomps_lookup(query)
        source = "sold (SoldComps)"
        if not comps:
            continue
        comps = [c for c in comps
                 if _relevant(query, c["title"]) and _quantity_match(title, c["title"])
                 and _model_match(query, c["title"])
                 and _audience_match(title, c["title"])
                 and _promo_match(title, c["title"])]
        kept = _iqr_records(comps)
        if len(kept) >= _MIN_FULL_COMPS:
            return _finalize(title, kept, source, result)
        if kept and best_partial is None:
            best_partial = (kept, f"sold (thin comps · {query})")

    if best_partial:
        return _finalize(title, best_partial[0], best_partial[1], result)

    # Active-listing fallback. Walk the variants MOST-SPECIFIC first (same
    # order as the sold-comps loop above) and take the first query with
    # enough agreeing listings: precision first, breadth only as needed.
    # Pricing off a single surviving comp is pricing off an anecdote, and on
    # dispersed markets (antique art, collectibles) that lone listing is
    # often the outlier that makes a $100 item look like a $600 one.
    best = None
    for query in variants:
        comps = _active_lookup(query)
        comps = [c for c in comps
                 if _relevant(query, c["title"]) and _quantity_match(title, c["title"])
                 and _model_match(title, c["title"])
                 and _audience_match(title, c["title"])
                 and _promo_match(title, c["title"])]
        kept = _iqr_records(comps)
        if not kept:
            continue
        if len(kept) >= _MIN_FULL_COMPS:
            return _finalize(title, kept, "active (eBay)", result,
                             realization=ACTIVE_REALIZATION)
        if best is None or len(kept) > len(best):
            best = kept
    if best:
        return _finalize(title, best, "active (eBay)", result,
                         realization=ACTIVE_REALIZATION)
    return result


# How many comp records survive into the enrichment row. Enough to judge
# the evidence at a glance; the row is display data, not an archive.
_COMPS_STORED = 15


def _finalize(title: str, comps: list[dict], source: str, result: dict,
              realization: float = 1.0) -> dict:
    prices = [c["price"] for c in comps]
    median = round(statistics.median(prices), 2)
    if len(prices) >= 4:
        q1, _, q3 = statistics.quantiles(prices, n=4)
        low, high = round(q1, 2), round(q3, 2)
    else:
        low, high = round(min(prices), 2), round(max(prices), 2)

    # Variance-contamination cap: a wild spread means the comps mix products.
    # NOS/sealed titles are exempt — they legitimately sit at the high end.
    if len(prices) >= 5 and low > 0 and high / low > 3.0 and not _NOS_RE.search(title):
        spread = high / low
        cap_mult = 2.5 if spread <= 5 else (1.5 if spread <= 10 else 1.0)
        median = min(median, round(cap_mult * low, 2))
        source += " (variance-capped)"

    # Generic-title single-comp ceiling: one pricey comp + vague title = bad match
    if len(prices) == 1 and median > 100 and not _SPECIFIC_RE.search(title):
        median = low
        source += " (generic-title single-comp)"

    if realization != 1.0:
        median = round(median * realization, 2)
        low = round(low * realization, 2)
        high = round(high * realization, 2)
        source += f" ×{realization:g} asking→sold"

    # The stored comps are RAW observations — no realization scaling, no
    # variance caps. est_resale is the conclusion; these are the evidence,
    # and evidence that's been adjusted to match the conclusion proves
    # nothing. price_source records every adjustment applied.
    result.update(est_resale=median, price_low=min(low, median),
                  price_high=max(high, median), comp_count=len(prices),
                  price_source=source,
                  comps=[{"price": round(c["price"], 2),
                          "title": (c["title"] or "")[:120],
                          "url": c.get("url"), "date": c.get("date"),
                          "kind": c.get("kind", "sold")}
                         for c in sorted(comps, key=lambda c: -c["price"])
                         [:_COMPS_STORED]])
    return result
