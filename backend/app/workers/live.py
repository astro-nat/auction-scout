"""Live Auction mode: while an auction is live, its bids refresh every
minute, and a watched lot whose bid passes your max bid sends one alert.

Bids otherwise move only when someone presses Refresh bids. During a sale
that is the wrong cadence - the bid that matters changes minute to minute,
and the one question is whether it has gone past what the lot is worth to
you. So a switched-on auction is read from HiBid every LIVE_INTERVAL
seconds (about ten catalogue pages for a 900-lot sale), re-graded at its
new bids, and switched off by itself once its last lot has closed.

The alert is for watched lots only: they are the ones you are bidding on,
and a 900-lot sale would otherwise alert on hundreds of lots nobody meant
to buy. It fires on the crossing - at or under max before this refresh,
over it after - once per lot (Lot.max_passed_alert_at), by the same ntfy
push the closing digest uses, and never if NTFY_TOPIC is unset.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Iterable

from ..database import SessionLocal
from .. import config, models
from ..services import open_state

logger = logging.getLogger(__name__)

LIVE_INTERVAL = 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def newly_past_max(before: dict, lots: Iterable) -> list:
    """Watched lots whose bid was at or under their max before, and over it
    now - each alerted once. `before` maps lot id to its bid before the
    refresh. Pure, so the rule is tested without HiBid."""
    out = []
    for lot in lots:
        e = lot.enrichment
        if not lot.watched or not e or e.max_bid is None or lot.max_passed_alert_at:
            continue
        was, now = before.get(lot.id), lot.current_bid
        if was is None or now is None:
            continue
        if float(was) <= float(e.max_bid) < float(now):
            out.append(lot)
    return out


def _alert(auction, lot) -> None:
    from .notify import _push
    e = lot.enrichment
    title = f"Past your max: {lot.title[:60]}"
    body = (f"Bid ${float(lot.current_bid):.2f} is over your max ${float(e.max_bid):.2f}"
            f" - lot {lot.lot_number or ''} at {auction.name[:60]}")
    if config.NTFY_TOPIC:
        _push(title, body, lot.lot_link)


def run_once() -> int:
    """One pass over every live auction. Returns how many alerts went out."""
    from .refresh import run_bid_refresh
    db = SessionLocal()
    sent = 0
    try:
        now = _utcnow()
        live = db.query(models.Auction).filter(models.Auction.live.is_(True)).all()
        for auction in live:
            over = (db.query(models.Auction)
                      .filter(models.Auction.id == auction.id, open_state.is_over(now))
                      .count())
            if over:
                auction.live = False
                db.commit()
                logger.info("Live mode off for %s: its last lot has closed", auction.name)
                continue
            watched = (db.query(models.Lot)
                         .filter(models.Lot.auction_id == auction.id,
                                 models.Lot.watched.is_(True)).all())
            before = {l.id: l.current_bid for l in watched}
            # Quiet: a job row every minute would bury the real work in the
            # Queue tab. The refresh itself is the same one the button runs.
            run_bid_refresh([auction.id], track_job=False)
            db.expire_all()
            watched = (db.query(models.Lot)
                         .filter(models.Lot.auction_id == auction.id,
                                 models.Lot.watched.is_(True)).all())
            for lot in newly_past_max(before, watched):
                _alert(auction, lot)
                lot.max_passed_alert_at = _utcnow()
                sent += 1
            auction.live_refreshed_at = _utcnow()
            db.commit()
    finally:
        db.close()
    return sent


def start_live_loop() -> None:
    def loop():
        while True:
            started = time.monotonic()
            try:
                n = run_once()
                if n:
                    print(f"Live mode: {n} past-your-max alert(s)")
            except Exception as exc:  # noqa: BLE001 - the loop outlives its own bugs
                logger.warning("Live refresh failed: %s", exc)
            time.sleep(max(5, LIVE_INTERVAL - (time.monotonic() - started)))

    threading.Thread(target=loop, daemon=True, name="live-auctions").start()
    logger.info("Live Auction mode on: every %ds", LIVE_INTERVAL)
