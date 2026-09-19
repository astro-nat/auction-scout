"""Auctions the user has forgotten.

The Auction row carries a `hidden` flag, but that row is not durable:
purge_stale_auctions drops closed auctions holding no lots, and the next scan
re-inserts whatever HiBid returns with hidden back at its default. A dismissal
recorded only there quietly expires. This list is keyed on the HiBid event id
and outlives the row, so a forgotten sale stays forgotten — it is skipped at
scan time and never stored again.

Sale-scoped on purpose: the same house's next sale is a new judgement. To
mute a whole auctioneer, unfavourite it (services/favorites.py).
"""

from typing import Optional

from sqlalchemy.orm import Session

from .. import models


def ids(db: Session) -> set[int]:
    """Every forgotten HiBid event id, for filtering scans and listings."""
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
    return (db.query(models.DismissedAuction)
              .order_by(models.DismissedAuction.created_at.desc())
              .all())
