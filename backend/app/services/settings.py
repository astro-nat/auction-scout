"""Tiny key-value settings store, DB-backed so changes survive deploys.

Env vars stay the fallback — a row here overrides them. Reads are cached
briefly because financials consults the ROI target once per lot in tight
reprice loops.
"""

import time

from sqlalchemy import text

from ..database import engine

_CACHE: dict[str, tuple[float, str | None]] = {}
_TTL_SECONDS = 15


def get(key: str) -> str | None:
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _TTL_SECONDS:
        return hit[1]
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT value FROM settings WHERE key = :k"), {"k": key}
        ).first()
    value = row[0] if row else None
    _CACHE[key] = (time.monotonic(), value)
    return value


def set(key: str, value: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO settings (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = :v"), {"k": key, "v": value})
    _CACHE[key] = (time.monotonic(), value)


# The per-auction floor, shared by the settings API and the closing-digest
# notifier — defined here so neither imports the other's module.
AUCTION_FLOOR_DEFAULT = 200.0

# Titled vehicles (cars, buses, boats) never earn the GOLD MINE badge while
# this is on — the fleet listings on the government platforms price out
# "profitably" and are still not this business.
EXCLUDE_VEHICLES_DEFAULT = True


def flag(key: str, default: bool) -> bool:
    """A boolean setting, falling back on first boot or a garbage row."""
    try:
        v = get(key)
        return v.strip().lower() in ("1", "true", "yes", "on") if v else default
    except Exception:  # noqa: BLE001 — settings table missing on first boot
        return default


def money(key: str, default: float) -> float:
    """A dollar setting, falling back on first boot or a garbage row."""
    try:
        v = get(key)
        return float(v) if v else default
    except Exception:  # noqa: BLE001 — settings table missing on first boot
        return default
