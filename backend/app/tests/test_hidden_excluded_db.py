"""Hidden lots never earn another comp lookup or AI cent (`pytest -m db`).

The 🚫 button is the user saying "not interested" — every bulk path
(reprice, enrich-all, enrich-category, reinspect, blind recovery) must
leave those lots alone. Unhiding puts a lot back in scope.
"""

from datetime import datetime, timedelta

import pytest

from app import models
from app.database import SessionLocal
from app.routers.enrichment import _not_hidden

pytestmark = pytest.mark.db


@pytest.fixture
def seeded(request):
    db = SessionLocal()
    a = models.Auction(name="hidden-test auction", hibid_id=555001,
                       closing_date=datetime.utcnow() + timedelta(days=1))
    db.add(a)
    db.flush()
    lots = []
    for i, hidden in enumerate([False, True, None]):
        l = models.Lot(lot_id=f"hiddentest-{i}", auction_id=a.id,
                       title=f"Widget {i}", category="Gizmos", hidden=hidden)
        db.add(l)
        db.flush()
        db.add(models.Enrichment(lot_id=l.id, status="pending"))
        lots.append(l.id)
    db.commit()

    def cleanup():
        db.query(models.Enrichment).filter(
            models.Enrichment.lot_id.in_(lots)).delete(synchronize_session=False)
        db.query(models.Lot).filter(
            models.Lot.id.in_(lots)).delete(synchronize_session=False)
        db.query(models.Auction).filter(
            models.Auction.id == a.id).delete(synchronize_session=False)
        db.commit()
        db.close()

    request.addfinalizer(cleanup)
    return db, a.id, lots


def test_not_hidden_excludes_only_the_hidden_lot(seeded):
    db, auction_id, _ = seeded
    ids = [r[0] for r in
           db.query(models.Lot.lot_id)
             .filter(models.Lot.auction_id == auction_id, _not_hidden())
             .all()]
    # hidden=False and hidden=NULL stay; hidden=True is out
    assert sorted(ids) == ["hiddentest-0", "hiddentest-2"]
