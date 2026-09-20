"""Closing-window digests: one push per auction as it enters the final hours.

The user's target rhythm is scanning twice a week and bidding in the last
hour or two before close — so the app pulls them in at that moment instead
of being polled all day. When an auction's close falls inside
WATCH_ALERT_HOURS, ONE ntfy notification goes out (https://ntfy.sh — free
pub/sub push: the phone app subscribes to a topic name, the server POSTs to
it; the topic name is the only secret, so it should be unguessable).

An auction earns a digest when it holds watched (★) lots — the user's own
shortlist — or when its audit-trusted gold mines clear the per-auction
floor, the same bar the auctions tab highlights. The body lists the lots
worth showing up for: current bid vs the walk-away ceiling, best first.

Disabled entirely when NTFY_TOPIC is unset. Each auction digests at most
once (closing_digest_sent_at), so a restart never re-spams; the per-lot
closing_alert_sent_at stamp is kept for the UI's re-watch semantics.
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
MAX_LOT_LINES = 8


def _push(title: str, body: str, click_url: str | None) -> bool:
    # Metadata rides in query params, NOT headers: HTTP headers are
    # ASCII-only in httpx, and titles carry whatever
    # an auction house typed. As headers, every push failed the encode,
    # _push swallowed it, and the digest retried forever — delivering
    # nothing. ntfy accepts ?title=&priority=&tags=&click= equivalently.
    params = {"title": title, "priority": "high"}
    if click_url:
        params["click"] = click_url
    try:
        r = httpx.post(f"{config.NTFY_URL}/{config.NTFY_TOPIC}",
                       params=params, content=body.encode(), timeout=10)
        r.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 — alerting must never crash the app
        logger.warning("ntfy push failed: %s", exc)
        return False


def _local_close(dt: datetime) -> str:
    """Naive-UTC closing time, shown on the user's (Central) clock."""
    from zoneinfo import ZoneInfo
    return (dt.replace(tzinfo=ZoneInfo("UTC"))
              .astimezone(ZoneInfo("America/Chicago"))
              .strftime("%I:%M %p").lstrip("0"))


def _lot_line(lot: models.Lot) -> str:
    e = lot.enrichment
    star = "★ " if lot.watched else ""
    num = f"#{lot.lot_number} " if lot.lot_number else ""
    title = (lot.title or "")[:40]
    bid = f"${float(lot.current_bid):.0f}" if lot.current_bid else "no bids"
    if e and e.max_bid is not None:
        money = f"bid {bid} / max ${float(e.max_bid):.0f}"
        if e.est_resale is not None:
            money += f" (resale ${float(e.est_resale):.0f})"
    else:
        money = f"bid {bid}"
    return f"{star}{num}{title} — {money}"


def _digest_lots(db, auction_id: int) -> list:
    """The lots worth showing up for: the user's ★ shortlist plus every
    audit-surviving gold mine. Watched first, then by profit."""
    from sqlalchemy import or_
    from sqlalchemy.orm import joinedload
    lots = (db.query(models.Lot)
              .options(joinedload(models.Lot.enrichment))
              .outerjoin(models.Enrichment,
                         models.Enrichment.lot_id == models.Lot.id)
              .filter(models.Lot.auction_id == auction_id,
                      models.Lot.hidden.isnot(True),
                      or_(models.Lot.watched.is_(True),
                          models.Enrichment.roi_status == "GOLD MINE"))
              .all())

    def profit(l):
        return (float(l.enrichment.profit)
                if l.enrichment and l.enrichment.profit is not None else 0.0)

    return sorted(lots, key=lambda l: (not l.watched, -profit(l)))


def check_closing_digests() -> int:
    """One pass: digest every qualifying auction that has entered the
    closing window. Returns how many digests went out (for tests/logs)."""
    from ..services import settings as settings_store
    db = SessionLocal()
    sent = 0
    try:
        now = datetime.now()
        cutoff = now + timedelta(hours=config.WATCH_ALERT_HOURS)
        floor = settings_store.money("auction_floor_usd",
                                     settings_store.AUCTION_FLOOR_DEFAULT)
        auctions = (db.query(models.Auction)
                      .filter(models.Auction.closing_date.isnot(None),
                              models.Auction.closing_date > now,
                              models.Auction.closing_date <= cutoff,
                              models.Auction.closing_digest_sent_at.is_(None))
                      .all())
        for auction in auctions:
            lots = _digest_lots(db, auction.id)
            watched = [l for l in lots if l.watched]
            gold_total = sum(float(l.enrichment.profit)
                             for l in lots
                             if l.enrichment and l.enrichment.profit is not None
                             and l.enrichment.roi_status == "GOLD MINE"
                             and float(l.enrichment.profit) > 0)
            # No shortlist and the board doesn't clear the floor: closing
            # without you is the correct outcome. Stamp it anyway so the
            # query stops revisiting a decided auction every five minutes.
            if not watched and gold_total < floor:
                auction.closing_digest_sent_at = now
                db.commit()
                continue
            head = f"Closes ~{_local_close(auction.closing_date)}"
            if gold_total:
                head += f" · ~${gold_total:.0f} trusted profit on the board"
            lines = [_lot_line(l) for l in lots[:MAX_LOT_LINES]]
            if len(lots) > MAX_LOT_LINES:
                lines.append(f"…and {len(lots) - MAX_LOT_LINES} more")
            body = "\n".join([head, *lines])
            if _push(f"Closing window: {auction.name[:60]}", body,
                     auction.source_url):
                auction.closing_digest_sent_at = now
                for l in lots:
                    l.closing_alert_sent_at = now
                db.commit()
                sent += 1
    finally:
        db.close()
    return sent


def start_notifier() -> None:
    """Spawn the polling thread. No-op (with a log line) when unconfigured."""
    if not config.NTFY_TOPIC:
        logger.info("Closing-window digests disabled — NTFY_TOPIC not set")
        return

    def loop():
        while True:
            try:
                n = check_closing_digests()
                if n:
                    print(f"Sent {n} closing-window digest(s)")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Closing-digest check failed: %s", exc)
            time.sleep(CHECK_EVERY_SECONDS)

    threading.Thread(target=loop, daemon=True, name="closing-digest-notifier").start()
    logger.info("Closing-window digests on: window=%.1fh topic=%s",
                config.WATCH_ALERT_HOURS, config.NTFY_TOPIC[:4] + "…")
