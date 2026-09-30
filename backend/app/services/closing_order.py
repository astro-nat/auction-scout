"""Bulk pricing works soonest-closing first.

A price is worth most on a lot you can still bid on, and least on one that
closes before its turn comes. So every bulk path - the AI batch, the
reinspect sweep, the comps-only reprice - hands its lots over in closing
order: soonest first, lots with no closing time last, and lots closing at
the same moment in the order the caller gave (usually most valuable
first). Manual reordering in the Queue still wins; this only sets where a
batch starts.
"""

from datetime import datetime

from .. import models

_NEVER = datetime.max


def _order(ids: list, closes: dict) -> list:
    position = {v: i for i, v in enumerate(ids)}
    return sorted(ids, key=lambda v: (closes.get(v) or _NEVER, position[v]))


def by_db_id(db, lot_db_ids: list[int]) -> list[int]:
    """Lot primary keys, soonest-closing first."""
    if not lot_db_ids:
        return []
    closes = dict(db.query(models.Lot.id, models.Lot.closes_at)
                    .filter(models.Lot.id.in_(lot_db_ids)).all())
    return _order(list(lot_db_ids), closes)


def by_lot_id(db, lot_ids: list[str]) -> list[str]:
    """HiBid lot ids (the API's lot_id strings), soonest-closing first."""
    if not lot_ids:
        return []
    closes = dict(db.query(models.Lot.lot_id, models.Lot.closes_at)
                    .filter(models.Lot.lot_id.in_(lot_ids)).all())
    return _order(list(lot_ids), closes)
