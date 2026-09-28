"""Lots the user has hidden.

The Lot row carries a `hidden` flag, but that row is not durable. A lot is
deleted an hour after its own closing time (flush_closed_now), and the next
import of a live sale re-creates it from the catalogue with `hidden` back at
its default - so the decision quietly expires and the lot returns.

It showed up as "I hide items and they come back": 27 hide presses in 48
hours with the hidden count unchanged at 28, every one of those 28 a
PublicSurplus lot that does not close until October and so never flushes.

This is the same fix dismissed.py already makes for auctions: key the
decision to the SOURCE's id, not our row, so it outlives the row.
"""

from typing import Optional

from sqlalchemy.orm import Session

from .. import models


def ids(db: Session) -> set[str]:
    """Every remembered lot_id, for re-applying at import time."""
    return {row[0] for row in db.query(models.HiddenLot.lot_id).all()}


def add(db: Session, lot_id: str, title: Optional[str] = None) -> None:
    """Remember that this lot is hidden. Idempotent."""
    if not lot_id:
        return
    exists = (db.query(models.HiddenLot)
                .filter(models.HiddenLot.lot_id == lot_id).first())
    if exists:
        return
    db.add(models.HiddenLot(lot_id=lot_id, title=(title or None)))


def add_many(db: Session, rows) -> None:
    """Remember a batch of lots - the bulk hides go through here."""
    known = ids(db)
    for lot in rows:
        if lot.lot_id and lot.lot_id not in known:
            db.add(models.HiddenLot(lot_id=lot.lot_id, title=lot.title))
            known.add(lot.lot_id)


def remove(db: Session, lot_id: str) -> None:
    """Unhiding is a decision too: forget it, so an import does not re-hide."""
    (db.query(models.HiddenLot)
       .filter(models.HiddenLot.lot_id == lot_id).delete(synchronize_session=False))


def remove_many(db: Session, lot_ids) -> None:
    lot_ids = [i for i in lot_ids if i]
    if lot_ids:
        (db.query(models.HiddenLot)
           .filter(models.HiddenLot.lot_id.in_(lot_ids))
           .delete(synchronize_session=False))
