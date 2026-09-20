"""Per-house estimate calibration: how promotional is this auctioneer?

Every closed lot that carried an estimate and sold is one measurement of the
house's honesty: estimate_low / hammer. The per-house MEDIAN of those is what
the UI shows next to any estimate from that house ("estimates run ~2.5x
hot") — the median because individual hammers are noisy (a stale last bid, a
sniped bargain) and a house's character is the middle of its distribution,
not its outliers.

The UI suppresses the ratio until MIN_OBS sales have been observed; the
server returns whatever it has, count included, so the threshold lives in
one place per surface rather than silently biasing the data.
"""

import logging

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import models

logger = logging.getLogger(__name__)

# Below this many observed sales, a ratio is an anecdote, not a calibration.
MIN_OBS = 20


def capture(db: Session, lots: list) -> int:
    """Record observations for closed lots on their way out of the database.

    Takes (Lot, auctioneer_id) pairs. A lot qualifies when the house
    published a low estimate and somebody actually paid something — a no-bid
    lot has no hammer to compare (a real signal, but a different one).
    Idempotent: HiBid's lot id dedupes, so re-flushing costs nothing.
    """
    rows = []
    for lot, auctioneer_id in lots:
        if not auctioneer_id or lot.estimate_low is None:
            continue
        hammer = float(lot.current_bid or 0)
        if hammer <= 0 or float(lot.estimate_low) <= 0:
            continue
        rows.append({"lot_id": lot.lot_id, "auctioneer_id": auctioneer_id,
                     "estimate_low": lot.estimate_low,
                     "estimate_high": lot.estimate_high, "hammer": hammer})
    if not rows:
        return 0
    stmt = (pg_insert(models.EstimateObservation)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["lot_id"]))
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def ratios(db: Session) -> dict[int, dict]:
    """auctioneer_id -> {"ratio": median estimate_low/hammer, "n": count}."""
    obs = models.EstimateObservation
    ratio_expr = obs.estimate_low / obs.hammer
    rows = (db.query(obs.auctioneer_id,
                     func.percentile_cont(0.5).within_group(ratio_expr.asc()),
                     func.count(obs.id))
              .group_by(obs.auctioneer_id)
              .all())
    return {aid: {"ratio": round(float(median), 2), "n": int(n)}
            for aid, median, n in rows if median is not None}
