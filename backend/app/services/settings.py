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
