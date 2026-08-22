"""Closing-soon alerts for watched lots.

A daemon thread polls the database every few minutes; when a watched lot's
auction closes within WATCH_ALERT_HOURS, it pushes a notification to the
user's phone via ntfy (https://ntfy.sh — free pub/sub push: the phone app
subscribes to a topic name, the server POSTs to it; the topic name is the
only secret, so it should be unguessable).

Disabled entirely when NTFY_TOPIC is unset. Each lot alerts at most once
(closing_alert_sent_at), so a restart never re-spams.
"""

import logging
import threading
import time
from datetime import datetime, timedelta

import httpx

from ..database import SessionLocal
from .. import config, models

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 300


def _push(title: str, body: str, click_url: str | None) -> bool:
    headers = {"Title": title, "Priority": "high", "Tags": "hourglass_flowing_sand"}
    if click_url:
        headers["Click"] = click_url
    try:
        r = httpx.post(f"{config.NTFY_URL}/{config.NTFY_TOPIC}",
                       content=body.encode(), headers=headers, timeout=10)
        r.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 — alerting must never crash the app
        logger.warning("ntfy push failed: %s", exc)
        return False


def check_closing_watches() -> int:
    """One pass: alert every watched, un-alerted lot closing inside the
    window. Returns how many alerts went out (for tests/logs)."""
    db = SessionLocal()
    sent = 0
    try:
        now = datetime.now()
        cutoff = now + timedelta(hours=config.WATCH_ALERT_HOURS)
        lots = (db.query(models.Lot)
                  .join(models.Auction, models.Lot.auction_id == models.Auction.id)
                  .filter(models.Lot.watched.is_(True),
                          models.Lot.closing_alert_sent_at.is_(None),
                          models.Auction.closing_date.isnot(None),
                          models.Auction.closing_date > now,
                          models.Auction.closing_date <= cutoff)
                  .all())
        for lot in lots:
            closes = lot.auction.closing_date.strftime("%I:%M %p").lstrip("0")
            bid = f"${float(lot.current_bid):.2f}" if lot.current_bid else "no bids"
            body = (f"{lot.title}\n"
                    f"Current bid: {bid} — auction closes ~{closes}")
            if _push("Watched lot closing soon", body,
                     lot.lot_link or (lot.auction.source_url if lot.auction else None)):
                lot.closing_alert_sent_at = now
                db.commit()
                sent += 1
    finally:
        db.close()
    return sent


def start_notifier() -> None:
    """Spawn the polling thread. No-op (with a log line) when unconfigured."""
    if not config.NTFY_TOPIC:
        logger.info("Watched-lot alerts disabled — NTFY_TOPIC not set")
        return

    def loop():
        while True:
            try:
                n = check_closing_watches()
                if n:
                    print(f"Sent {n} closing-soon alert(s)")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Closing-watch check failed: %s", exc)
            time.sleep(CHECK_EVERY_SECONDS)

    threading.Thread(target=loop, daemon=True, name="closing-watch-notifier").start()
    logger.info("Watched-lot alerts on: window=%.1fh topic=%s",
                config.WATCH_ALERT_HOURS, config.NTFY_TOPIC[:4] + "…")
