"""Box lots under a BOLO-only import (`pytest -m db`).

A brand-only filter drops "Lot of Assorted Cameras" because no brand is
named - which is backwards, since that is exactly the lot worth opening
when cameras are on the list. A multi-item lot is kept when its title lands
in a BOLO category.

The risk is the opposite one: keeping every junk box in the sale. A
warehouse auction is full of "Large Box of Miscellaneous Items", and those
must still be dropped, so the category tokens carry a stop list.
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services.bolo import category_hint
from app.workers.import_all import save_lots

pytestmark = pytest.mark.db

TEST_HIBID = 999999971


def _lot(lot_id, title):
    return {
        "lot_id": lot_id, "title": title, "category": "General",
        "description": "", "current_bid": 1, "next_bid": 2, "bid_count": 0,
        "est_cost": 1.15, "status": "OPEN", "time_left": "2d", "closes_at": None,
        "lot_number": None, "estimate_low": None, "estimate_high": None,
        "source": "Ship", "logistics_ease": "EASY", "unreachable_pickup": False,
        "lot_link": None, "thumbnail_url": None, "hd_thumbnail_url": None,
        "fullsize_url": None, "image_count": 0,
    }


@pytest.fixture
def auction():
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="boxlot-test",
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.commit()
    yield db, a
    ids = [r[0] for r in db.query(models.Lot.id)
                           .filter(models.Lot.auction_id == a.id).all()]
    if ids:
        db.query(models.Enrichment).filter(
            models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.auction_id == a.id).delete(
        synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == a.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _kept(db, a):
    return sorted(r[0] for r in db.query(models.Lot.title)
                                  .filter(models.Lot.auction_id == a.id).all())


@pytest.mark.parametrize("title,expected_hint", [
    ("Lot of Assorted Cameras", "cameras"),
    ("Box of Costume Jewelry", "costume jewelry"),
    ("Bundle of Vintage Calculators", "calculators"),
    ("Lot of Fishing Reels and Tackle", "fishing"),
    ("Assorted Cast Iron Cookware Lot", "cast iron"),
])
def test_box_lots_in_a_bolo_category_are_recognised(title, expected_hint):
    assert category_hint(title) == expected_hint


def test_the_hint_is_stable_across_calls():
    """The slugs yield both "camera" and "cameras". Iterating a set meant
    the same title reported a different category run to run."""
    seen = {category_hint("Lot of Assorted Cameras") for _ in range(5)}
    assert len(seen) == 1, f"category_hint is non-deterministic: {seen}"


@pytest.mark.parametrize("title", [
    "Lot of Assorted Plastic Totes",
    "Large Box of Miscellaneous Items",
    "Bundle of Used Coat Hangers",
    "Box of Assorted Paperwork and Files",
])
def test_junk_boxes_still_hint_at_nothing(title):
    """The stop list is what stops a warehouse sale importing entirely."""
    assert category_hint(title) is None


def test_a_box_lot_is_imported_even_though_it_names_no_brand(auction):
    db, a = auction
    created, _, _ = save_lots(db, a, [
        _lot("bx-1", "Lot of Assorted Cameras and Lenses"),
        _lot("bx-2", "Lot of Assorted Plastic Totes"),
    ], bolo_only=True)
    assert created == 1
    assert _kept(db, a) == ["Lot of Assorted Cameras and Lenses"]


def test_a_single_item_in_a_category_is_not_enough(auction):
    """Only MULTI-ITEM lots get the category route. A lone "Camera Bag"
    naming no brand is not evidence of anything worth importing."""
    db, a = auction
    created, _, _ = save_lots(db, a, [_lot("bx-3", "Camera Bag Black")],
                              bolo_only=True)
    assert created == 0


def test_brand_matches_still_come_through_alongside(auction):
    db, a = auction
    created, _, _ = save_lots(db, a, [
        _lot("bx-4", "McIntosh MC275 Tube Amplifier"),      # brand
        _lot("bx-5", "Lot of Assorted Cameras"),            # category box lot
        _lot("bx-6", "Box of Used Coat Hangers"),           # neither
    ], bolo_only=True)
    assert created == 2
    assert "Box of Used Coat Hangers" not in _kept(db, a)


def test_an_unfiltered_import_is_unaffected(auction):
    db, a = auction
    created, _, _ = save_lots(db, a, [
        _lot("bx-7", "Lot of Assorted Plastic Totes"),
        _lot("bx-8", "Box of Used Coat Hangers"),
    ])
    assert created == 2, "the filter leaked into an unfiltered import"
