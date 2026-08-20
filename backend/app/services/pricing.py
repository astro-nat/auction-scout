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
from typing import Optional

import httpx

from . import ebay

logger = logging.getLogger(__name__)

SOLDCOMPS_API_KEY = os.environ.get("SOLDCOMPS_API_KEY", "")

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
    r"|large lot|case of|wholesale|dealer lot|estate lot",
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


def query_variants(title: str) -> list[str]:
    """Progressively shorter queries (6 → 4 → 3 words). eBay returns zero results
    for very long queries; 4-6 words is the sweet spot."""
    cleaned = clean_title(title)
    variants, seen = [], set()
    for cap in _QUERY_WORD_CAPS:
        v = " ".join(cleaned.split()[:cap])
        if len(v) >= 5 and v.lower() not in seen:
            seen.add(v.lower())
            variants.append(v)
    return variants


# ------------------------------------------------------------------ filtering

def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower())
            if t not in _STOPWORDS}


def _relevant(query: str, comp_title: str) -> bool:
    """Comp counts only if a majority of query tokens (prefix-)match its title.
    Consistently-priced-but-WRONG comps are invisible to variance checks —
    this filter is what catches them."""
    if not comp_title:
        return True
    q, c = _tokens(query), _tokens(comp_title)
    if not q:
        return True
    hits = sum(1 for qt in q
               if any(ct.startswith(qt) or qt.startswith(ct) for ct in c))
    return hits >= math.ceil(len(q) / 2)


def _quantity_match(query: str, comp_title: str) -> bool:
    return bool(_BULK_RE.search(query)) == bool(_BULK_RE.search(comp_title or ""))


def _iqr_filter(prices: list[float]) -> list[float]:
    if len(prices) < 4:
        return prices
    q1, _, q3 = statistics.quantiles(prices, n=4)
    fence = 1.5 * (q3 - q1)
    return [p for p in prices if q1 - fence <= p <= q3 + fence]


# ---------------------------------------------------------------- comp sources

def _soldcomps_lookup(query: str, count: int = 120) -> list[tuple[float, str]]:
    """SoldComps API — real sold prices over the last 90 days."""
    if not SOLDCOMPS_API_KEY:
        return []
    try:
        with httpx.Client(timeout=40.0) as client:
            r = client.get(
                "https://api.sold-comps.com/v1/scrape",
                headers={"Authorization": f"Bearer {SOLDCOMPS_API_KEY}"},
                params={"keyword": query, "count": min(max(count, 1), 240),
                        "daysToScrape": 90},
            )
        if r.status_code != 200:
            logger.warning("SoldComps HTTP %s for %r", r.status_code, query)
            return []
        out = []
        for item in r.json().get("items", []) or []:
            raw = str(item.get("soldPrice") or "").replace("$", "").replace(",", "")
            try:
                p = float(raw)
            except ValueError:
                continue
            if _PRICE_MIN < p < _PRICE_MAX:
                out.append((p, item.get("title") or ""))
        return out
    except Exception as exc:
        logger.warning("SoldComps failed for %r: %s", query, exc)
        return []


def _active_lookup(query: str) -> list[tuple[float, str]]:
    """eBay Browse active fixed-price listings — the always-available fallback."""
    out = []
    for item in ebay.search_active(query, limit=20):
        try:
            p = float((item.get("price") or {}).get("value") or 0)
        except (ValueError, TypeError):
            continue
        if p > _PRICE_MIN:
            out.append((p, item.get("title") or ""))
    return out


# ------------------------------------------------------------------ main entry

def lookup_comps(title: str) -> dict:
    """Estimate resale value for one lot title.

    Returns {est_resale, price_low, price_high, comp_count, price_source} —
    est_resale is None when nothing priced.
    """
    result = {"est_resale": None, "price_low": None, "price_high": None,
              "comp_count": 0, "price_source": None}
    variants = query_variants(title)
    if not variants:
        return result

    best_partial: Optional[tuple] = None
    for query in variants:
        comps = _soldcomps_lookup(query)
        source = "sold (SoldComps)"
        if not comps:
            continue
        comps = [(p, t) for p, t in comps
                 if _relevant(query, t) and _quantity_match(title, t)]
        prices = _iqr_filter([p for p, _ in comps])
        if len(prices) >= _MIN_FULL_COMPS:
            return _finalize(title, prices, source, result)
        if prices and best_partial is None:
            best_partial = (prices, f"sold (thin comps · {query})")

    if best_partial:
        return _finalize(title, best_partial[0], best_partial[1], result)

    # Active-listing fallback with the shortest variant
    comps = _active_lookup(variants[-1])
    comps = [(p, t) for p, t in comps if _relevant(variants[-1], t)]
    prices = _iqr_filter([p for p, _ in comps])
    if prices:
        return _finalize(title, prices, "active (eBay)", result)
    return result


def _finalize(title: str, prices: list[float], source: str, result: dict) -> dict:
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
        source += " ⚠ variance-capped"

    # Generic-title single-comp ceiling: one pricey comp + vague title = bad match
    if len(prices) == 1 and median > 100 and not _SPECIFIC_RE.search(title):
        median = low
        source += " ⚠ generic-title single-comp"

    result.update(est_resale=median, price_low=min(low, median),
                  price_high=max(high, median), comp_count=len(prices),
                  price_source=source)
    return result
