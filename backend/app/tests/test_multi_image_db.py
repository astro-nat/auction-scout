"""How many photos the model gets (`pytest -m db`).

The first photo is often a stock or catalogue shot; condition judged from
it is condition judged from someone else's product. So the deliberate AI
look - the one the user aims at lots comps have already priced - reads up
to DEEP_IMAGE_MAX photos, while the cheap first pass still reads one.
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services import pricing
from app.workers import enrich

pytestmark = pytest.mark.db

TEST_HIBID = 999999970
SHOTS = [f"https://example.com/lot/{i}.jpg" for i in range(6)]
FOUND = {"est_resale": 50, "price_low": 40, "price_high": 60,
         "comp_count": 5, "price_source": "sold (SoldComps)", "comps": []}


@pytest.fixture
def lot(monkeypatch):
    """One lot with six photos. The vision call is recorded, never sent."""
    seen = {"images": None, "prompt": None, "calls": 0}

    def fake_vision(prompt, image_bytes, max_tokens):
        seen["calls"] += 1
        seen["images"] = list(image_bytes) if isinstance(image_bytes, list) else [image_bytes]
        seen["prompt"] = prompt
        return {"enriched_title": "A Real Thing", "verdict": "normal wear and tear",
                "confident": True, "notes": "n", "ship": "EASY"}

    monkeypatch.setattr(enrich, "_vision_json", fake_vision)
    monkeypatch.setattr(enrich, "_call_text", lambda *a, **k: None)   # force the photo path
    monkeypatch.setattr(enrich, "_download_image",
                        lambda url: (f"bytes:{url}".encode() if url else None))
    monkeypatch.setattr(enrich, "_verify_gold", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "verified_title_price", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "retail_from_title", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "lookup_comps", lambda t: dict(FOUND))

    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="multi-image-test",
                             buyer_premium_mult=1.15, source="Ship",
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    row = models.Lot(lot_id="mi-1", title="Dell Precision 7750 Laptop",
                     description="x" * 200, auction_id=auction.id,
                     current_bid=1, next_bid=2, logistics_ease="EASY", source="Ship",
                     thumbnail_url="https://example.com/thumb.jpg",
                     fullsize_url=SHOTS[0], image_urls=SHOTS, image_count=len(SHOTS))
    db.add(row)
    db.flush()
    db.add(models.Enrichment(lot_id=row.id, status="queued", queued_task="enrich",
                             claimed_at=datetime.now(timezone.utc), user_overrides=[]))
    db.commit()
    yield db, row.id, seen
    db.rollback()
    db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id == row.id).delete(synchronize_session=False)
    db.query(models.TaskTiming).filter(
        models.TaskTiming.auction_id == auction.id).delete(synchronize_session=False)
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id == row.id).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id == row.id).delete(synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == auction.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _price_it(db, lot_id, value):
    e = db.query(models.Enrichment).filter(models.Enrichment.lot_id == lot_id).one()
    e.est_resale = value
    e.status = "queued"
    e.claimed_at = datetime.now(timezone.utc)
    db.commit()


def test_the_cheap_first_pass_still_reads_one_photo(lot):
    db, lot_id, seen = lot
    enrich.run_enrichment(lot_id)
    assert len(seen["images"]) == 1
    assert seen["images"][0] == f"bytes:{SHOTS[0]}".encode()
    assert "photos of the SAME lot" not in seen["prompt"]


def test_the_deliberate_look_reads_up_to_the_cap(lot):
    """Comps have priced it, so this is the look the user paid for."""
    db, lot_id, seen = lot
    _price_it(db, lot_id, 80)
    enrich.run_enrichment(lot_id)
    assert len(seen["images"]) == enrich.DEEP_IMAGE_MAX == 4
    assert seen["images"] == [f"bytes:{u}".encode() for u in SHOTS[:4]]


def test_the_prompt_warns_that_the_first_photo_may_be_stock(lot):
    db, lot_id, seen = lot
    _price_it(db, lot_id, 80)
    enrich.run_enrichment(lot_id)
    for phrase in ("4 photos of the SAME lot", "stock or catalogue image",
                   "WORST thing any photo shows"):
        assert phrase in seen["prompt"], phrase


def test_a_photo_that_will_not_download_is_skipped_not_fatal(lot, monkeypatch):
    db, lot_id, seen = lot
    monkeypatch.setattr(enrich, "_download_image",
                        lambda url: None if url == SHOTS[1] else f"bytes:{url}".encode())
    _price_it(db, lot_id, 80)
    enrich.run_enrichment(lot_id)
    assert len(seen["images"]) == 3
    assert f"bytes:{SHOTS[1]}".encode() not in seen["images"]


def test_a_lot_with_no_photo_list_falls_back_to_its_one_url(lot):
    db, lot_id, seen = lot
    row = db.query(models.Lot).filter(models.Lot.id == lot_id).one()
    row.image_urls = None
    db.commit()
    _price_it(db, lot_id, 80)
    enrich.run_enrichment(lot_id)
    assert seen["images"] == [f"bytes:{SHOTS[0]}".encode()]


def test_lot_image_urls_prefers_the_list_and_honours_the_cap(lot):
    db, lot_id, _ = lot
    row = db.query(models.Lot).filter(models.Lot.id == lot_id).one()
    assert enrich.lot_image_urls(row, 4) == SHOTS[:4]
    assert enrich.lot_image_urls(row, 99) == SHOTS
    assert enrich.lot_image_urls(row, 0) == SHOTS[:1]      # always at least one
