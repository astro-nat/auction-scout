"""Gold and silver content, read from a lot's own words, and what it melts for.

Jewelry houses print the karat in the title ("14K Yellow Gold ...") and the
weight in the description ("total grams are 4.42"). Together with the day's
spot price that is a floor no comp search can argue with: the AI audit once
valued a 5.08g 14K bracelet at $45, under half its gold.

Deliberately conservative, because it becomes a price floor:
- gold-filled, plated, vermeil, gold-tone and "gold over" pieces carry no
  gold worth counting, and gold over sterling is priced as sterling;
- a piece naming both ("Sterling & 14K") is priced as the cheaper metal;
- a weight that includes stones or pearls counts only STONE_METAL_SHARE of
  it as metal.

Spot prices come from gold-api.com (free, no key), fetched at most every
SPOT_TTL and kept in the settings table so a failed fetch falls back to the
last good one. With no price at all there is no floor - never a guess.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

TROY_OZ_GRAMS = 31.1035
STONE_METAL_SHARE = 0.7
SPOT_TTL = 6 * 3600
SPOT_URL = "https://api.gold-api.com/price/{symbol}"
_SETTINGS_KEY = "spot_prices"

_NOT_SOLID_RE = re.compile(
    r"\b(gold[\s-]*filled|g\.?f\.?|gold[\s-]*plated|plated|vermeil|gold[\s-]*tone|"
    r"goldtone|rolled[\s-]*gold|r\.?g\.?p\.?|h\.?g\.?e\.?|electroplated?|gold[\s-]*over|"
    r"gold[\s-]*wash(?:ed)?|gold[\s-]*dipped)\b", re.IGNORECASE)
_KARAT_RE = re.compile(r"\b(8|9|10|12|14|18|20|21|22|24)\s*(?:k|kt|karat)\b", re.IGNORECASE)
_SILVER = [
    (re.compile(r"\b(?:999|\.999)\s*(?:fine\s*)?silver\b|\bfine\s+silver\b", re.I), 0.999),
    (re.compile(r"\bsterling\b|\b\.?925\b|\bs925\b", re.I), 0.925),
    (re.compile(r"\bcoin\s+silver\b|\b\.?900\s+silver\b", re.I), 0.900),
    (re.compile(r"\b\.?800\s+silver\b", re.I), 0.800),
]
_GRAMS_RES = [
    # "4.42 grams", "15.7g"
    re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:g|gr|grams?|gms?)\b", re.IGNORECASE),
    # "total grams are 4.42", "total grams with stone are 5.89", "Grams: 2.7"
    re.compile(r"\bgrams?\b[^\d]{0,25}?(\d+(?:\.\d+)?)", re.IGNORECASE),
]
_DWT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*dwt\b", re.IGNORECASE)
_STONE_RE = re.compile(
    r"\b(with\s+stones?|diamonds?|pearls?|jade|emeralds?|rub(?:y|ies)|sapphires?|opals?|"
    r"turquoise|garnets?|amethysts?|topaz|onyx|coral|cameo|tiger'?s?\s*eye|crystals?|"
    r"stones?|gems?|lapis|agate|jasper|amber|malachite|moonstone)\b", re.IGNORECASE)


def _grams(text: str) -> float | None:
    if re.search(r"\bgauge\b", text, re.IGNORECASE):
        return None
    m = _DWT_RE.search(text)
    if m:
        return round(float(m.group(1)) * 1.5552, 2)
    for rx in _GRAMS_RES:
        m = rx.search(text)
        if m:
            g = float(m.group(1))
            if 0 < g < 5000:
                return g
    return None


def assess(title: str | None, description: str | None) -> dict | None:
    """The precious metal in a lot, from its title and description, or None.

    Returns {metal, purity, label, grams, metal_grams} - grams as stated,
    metal_grams after the stone allowance. grams may be None when the lot
    names a metal but no weight (the label still helps; there is no melt).
    """
    title = title or ""
    text = f"{title} {description or ''}"
    not_solid = bool(_NOT_SOLID_RE.search(text))
    silver = next((p for rx, p in _SILVER if rx.search(text)), None)
    karat = None
    if not not_solid:
        m = _KARAT_RE.search(title) or _KARAT_RE.search(text)
        karat = int(m.group(1)) if m else None
    if karat and silver:
        metal, purity, label = "silver", silver, "Sterling" if silver == 0.925 else f"{silver:.3f} silver"
    elif karat:
        metal, purity, label = "gold", karat / 24, f"{karat}K"
    elif silver:
        metal, purity, label = "silver", silver, "Sterling" if silver == 0.925 else f"{silver:.3f} silver"
    else:
        return None
    grams = _grams(text)
    metal_grams = None
    if grams:
        metal_grams = round(grams * (STONE_METAL_SHARE if _STONE_RE.search(text) else 1.0), 2)
    return {"metal": metal, "purity": purity, "label": label,
            "grams": grams, "metal_grams": metal_grams}


# --- spot prices ------------------------------------------------------------

_spot: dict | None = None
_spot_at = 0.0
_spot_lock = threading.Lock()


def _fetch_spot() -> dict | None:
    try:
        out = {}
        with httpx.Client(timeout=5.0) as c:
            for sym, key in (("XAU", "gold"), ("XAG", "silver")):
                r = c.get(SPOT_URL.format(symbol=sym))
                r.raise_for_status()
                price = float(r.json()["price"])
                if price <= 0:
                    return None
                out[key] = price
        out["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return out
    except Exception as exc:  # noqa: BLE001 - a missing floor beats a failed grade
        logger.warning("Spot price fetch failed: %s", exc)
        return None


def spot() -> dict | None:
    """{gold, silver} in USD per troy ounce, plus when they were read."""
    global _spot, _spot_at
    if _spot and time.monotonic() - _spot_at < SPOT_TTL:
        return _spot
    with _spot_lock:
        if _spot and time.monotonic() - _spot_at < SPOT_TTL:
            return _spot
        from . import settings
        fresh = None if os.environ.get("SPOT_OFFLINE") else _fetch_spot()
        if fresh:
            try:
                settings.set(_SETTINGS_KEY, json.dumps(fresh))
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                saved = settings.get(_SETTINGS_KEY)
                fresh = json.loads(saved) if saved else None
            except Exception:  # noqa: BLE001
                fresh = None
        _spot, _spot_at = fresh, time.monotonic()
        return _spot


def melt_value(info: dict | None, prices: dict | None = None) -> float | None:
    """What the metal in a lot is worth at spot, or None without weight or price."""
    if not info or not info.get("metal_grams"):
        return None
    prices = prices if prices is not None else spot()
    if not prices or not prices.get(info["metal"]):
        return None
    per_gram = prices[info["metal"]] / TROY_OZ_GRAMS
    return round(info["metal_grams"] * info["purity"] * per_gram, 2)
