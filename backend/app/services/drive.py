"""Driving time from your address to each auction, via OpenRouteService.

A pickup trip is the fixed cost behind the "By auction" view: once you're
driving to a sale for a gold mine, weaker lots there are worth adding. How
far the drive is decides whether the trip is worth it at all, and miles
from a zip code are a poor stand-in for time in the car.

Two calls, both on ORS's free tier:
  - geocode: the address you type -> coordinates, once per change.
  - matrix:  one origin to many auctions -> seconds of driving, in batches.

HiBid already sends each auction's coordinates with the scan, so auctions
cost nothing to locate. Times are for typical conditions; ORS has no live
traffic.

Nothing here raises into a scan: a missing key, a quota wall or an outage
leaves drive times blank and says why in the log.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from . import settings as settings_store

logger = logging.getLogger(__name__)

ORS_API_KEY = os.environ.get("ORS_API_KEY", "")
_BASE = "https://api.openrouteservice.org"
# Locations per matrix request, the origin included. ORS caps a request by
# locations and by routes; 50 stays inside both on the free plan.
_MATRIX_LOCATIONS = 50
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)

# Settings keys for the saved origin.
KEY_ADDRESS = "drive_from_address"
KEY_LABEL = "drive_from_label"
KEY_LAT = "drive_from_lat"
KEY_LNG = "drive_from_lng"


class DriveError(Exception):
    """Something the person can act on: a bad key, no match, a quota."""


def _headers() -> dict:
    return {"Authorization": ORS_API_KEY, "Accept": "application/json"}


def _check(r: httpx.Response, what: str) -> None:
    if r.status_code in (401, 403):
        raise DriveError("OpenRouteService refused the API key. "
                         "Check ORS_API_KEY on the backend.")
    if r.status_code == 429:
        raise DriveError("OpenRouteService's daily limit is used up. "
                         "Drive times will fill in after it resets.")
    if r.status_code != 200:
        raise DriveError(f"OpenRouteService {what} failed (HTTP {r.status_code}).")


def geocode(text: str) -> dict:
    """The best match for an address: {"lat", "lng", "label"}."""
    if not ORS_API_KEY:
        raise DriveError("Drive times need an OpenRouteService key: "
                         "set ORS_API_KEY on the backend.")
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(f"{_BASE}/geocode/search", headers=_headers(),
                       params={"text": text, "size": 1, "boundary.country": "US"})
    _check(r, "address lookup")
    features = (r.json() or {}).get("features") or []
    if not features:
        raise DriveError(f"No match for “{text}”. Try a street address with a city, or a zip code.")
    lng, lat = features[0]["geometry"]["coordinates"][:2]
    return {"lat": float(lat), "lng": float(lng),
            "label": (features[0].get("properties") or {}).get("label") or text}


def durations(origin: tuple[float, float],
              destinations: list[tuple[int, float, float]]) -> dict[int, float | None]:
    """Minutes of driving from `origin` (lat, lng) to each (id, lat, lng).

    None where ORS found no route (an island, bad coordinates).
    """
    out: dict[int, float | None] = {}
    step = _MATRIX_LOCATIONS - 1
    with httpx.Client(timeout=_TIMEOUT) as client:
        for i in range(0, len(destinations), step):
            batch = destinations[i:i + step]
            # ORS wants [lng, lat], origin first.
            locations = [[origin[1], origin[0]]] + [[lng, lat] for _, lat, lng in batch]
            r = client.post(f"{_BASE}/v2/matrix/driving-car", headers=_headers(), json={
                "locations": locations,
                "sources": [0],
                "destinations": list(range(1, len(locations))),
                "metrics": ["duration"],
            })
            _check(r, "drive-time lookup")
            row = ((r.json() or {}).get("durations") or [[]])[0]
            for (aid, _, _), seconds in zip(batch, row):
                out[aid] = round(seconds / 60, 1) if seconds is not None else None
    return out


def origin() -> dict | None:
    """The saved drive-from point, or None when none is set."""
    try:
        lat, lng = settings_store.get(KEY_LAT), settings_store.get(KEY_LNG)
        if not lat or not lng:
            return None
        return {"lat": float(lat), "lng": float(lng),
                "address": settings_store.get(KEY_ADDRESS) or "",
                "label": settings_store.get(KEY_LABEL) or ""}
    except Exception:  # noqa: BLE001 - settings table missing on first boot
        return None


def origin_key(o: dict) -> str:
    """Which origin a stored drive time was measured from."""
    return f"{o['lat']:.5f},{o['lng']:.5f}"


def usable_coords(lat, lng) -> bool:
    """HiBid sends 0,0 (and sometimes nothing) for an auction it can't place."""
    try:
        return lat is not None and lng is not None and not (float(lat) == 0 and float(lng) == 0)
    except (TypeError, ValueError):
        return False


def refresh(db, *, raise_errors: bool = False) -> int:
    """Fill in drive times for auctions not yet measured from the saved
    origin. Returns how many were updated.

    Skips auctions that closed more than two days ago: nobody is driving
    to those. Measured-but-unroutable auctions keep a blank time and are
    not asked again until the origin or their coordinates change.
    """
    from .. import models
    o = origin()
    if not o or not ORS_API_KEY:
        return 0
    key = origin_key(o)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
    rows = (db.query(models.Auction)
              .filter(models.Auction.geo_lat.isnot(None),
                      models.Auction.geo_lng.isnot(None),
                      (models.Auction.drive_from.is_(None))
                      | (models.Auction.drive_from != key),
                      (models.Auction.closing_date.is_(None))
                      | (models.Auction.closing_date >= cutoff))
              .all())
    rows = [a for a in rows if usable_coords(a.geo_lat, a.geo_lng)]
    if not rows:
        return 0
    try:
        mins = durations((o["lat"], o["lng"]),
                         [(a.id, a.geo_lat, a.geo_lng) for a in rows])
    except (DriveError, httpx.HTTPError) as exc:
        logger.warning("Drive times not updated: %s", exc)
        if raise_errors:
            raise DriveError(str(exc)) from exc
        return 0
    for a in rows:
        if a.id in mins:
            a.drive_minutes = mins[a.id]
            a.drive_from = key
    db.commit()
    return len(mins)


def set_origin(db, address: str, label: str | None = None) -> dict:
    """Look up an address, save it as the drive-from point, and measure
    every auction from it. Raises DriveError with a message for the person."""
    from .. import models
    place = geocode(address)
    settings_store.set(KEY_ADDRESS, address)
    settings_store.set(KEY_LABEL, (label or "").strip()[:40])
    settings_store.set(KEY_LAT, str(place["lat"]))
    settings_store.set(KEY_LNG, str(place["lng"]))
    # Times from the old origin are wrong now; blank them before refilling.
    db.query(models.Auction).update({"drive_minutes": None, "drive_from": None},
                                    synchronize_session=False)
    db.commit()
    updated = refresh(db, raise_errors=True)
    return {"address": address, "label": (label or "").strip()[:40],
            "matched": place["label"], "updated": updated}


def clear_origin(db) -> None:
    from .. import models
    for k in (KEY_ADDRESS, KEY_LABEL, KEY_LAT, KEY_LNG):
        settings_store.set(k, "")
    db.query(models.Auction).update({"drive_minutes": None, "drive_from": None},
                                    synchronize_session=False)
    db.commit()
