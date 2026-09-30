"""Auctions the user has forgotten.

The Auction row carries a `hidden` flag, but that row is not durable:
purge_stale_auctions drops closed auctions holding no lots, and the next scan
re-inserts whatever HiBid returns with hidden back at its default. A dismissal
recorded only there quietly expires. This list is keyed on the HiBid event id
and outlives the row, so a forgotten sale stays forgotten — it is skipped at
scan time and never stored again.

Sale-scoped on purpose: the same house's next sale is a new judgement. To
mute a whole auctioneer, unfavourite it (services/favorites.py).

The list keeps itself short: an entry forgotten more than a week ago goes
(see prune), since by then the sale is almost always over and the entry
only clutters the restore list. One whose auction is known to still be
running stays until it closes - dropping it early would let the next scan
bring the sale straight back.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .. import models


PRUNE_AFTER_DAYS = 7


def prune(db: Session, now: Optional[datetime] = None) -> int:
    """Drop entries forgotten more than PRUNE_AFTER_DAYS ago, except those
    whose auction we know is still open. Returns how many went."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    from . import open_state
    still_open = (db.query(models.Auction.hibid_id)
                    .filter(models.Auction.hibid_id.isnot(None),
                            open_state.still_open(now)))
    gone = (db.query(models.DismissedAuction)
              .filter(models.DismissedAuction.created_at < now - timedelta(days=PRUNE_AFTER_DAYS),
                      ~models.DismissedAuction.hibid_id.in_(still_open))
              .delete(synchronize_session=False))
    if gone:
        db.commit()
    return gone


def ids(db: Session) -> set[int]:
    """Every forgotten HiBid event id, for filtering scans and listings."""
    prune(db)
    return {row[0] for row in db.query(models.DismissedAuction.hibid_id).all()}


def add(db: Session, hibid_id: int, name: Optional[str] = None) -> models.DismissedAuction:
    row = (db.query(models.DismissedAuction)
             .filter(models.DismissedAuction.hibid_id == hibid_id).first())
    if row:
        if name and not row.name:
            row.name = name
            db.commit()
        return row
    # Fall back to the auction's own name so the restore list reads as titles
    # rather than bare event ids, even after the auction row is purged.
    if not name:
        known = (db.query(models.Auction.name)
                   .filter(models.Auction.hibid_id == hibid_id).first())
        name = known[0] if known else None
    row = models.DismissedAuction(hibid_id=hibid_id, name=name)
    db.add(row)
    db.commit()
    return row


def remove(db: Session, hibid_id: int) -> bool:
    deleted = (db.query(models.DismissedAuction)
                 .filter(models.DismissedAuction.hibid_id == hibid_id)
                 .delete(synchronize_session=False))
    db.commit()
    return bool(deleted)


def listed(db: Session) -> list[models.DismissedAuction]:
    prune(db)
    return (db.query(models.DismissedAuction)
              .order_by(models.DismissedAuction.created_at.desc())
              .all())
