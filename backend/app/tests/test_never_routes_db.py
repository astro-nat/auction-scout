"""The never list end to end: seeded, editable, and stamped on lots
(`pytest -m db`).

The pure tests (test_never_list.py) cover the matching. These cover the part
that makes it worth having in the database rather than in a file: the user
adds a category, the next page of lots reports it, and no deploy happened.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)
TEST_HIBID = 999999862
MINE = "never-routes-test-label"


def _purge(db):
    for a in db.query(models.Auction).filter(
            models.Auction.hibid_id == TEST_HIBID).all():
        db.query(models.Lot).filter(models.Lot.auction_id == a.id).delete(
            synchronize_session=False)
        db.query(models.Auction).filter(models.Auction.id == a.id).delete(
            synchronize_session=False)
    db.query(models.NeverRule).filter(
        models.NeverRule.label == MINE).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def sale():
    scrub = SessionLocal()
    _purge(scrub)
    scrub.close()
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="never-routes-test",
                       source="Ship", buyer_premium_mult=1.15,
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.flush()
    for lid, title in (
            ("nr-knife", "Chinese Style Cleaver Knife with Sheath"),
            ("nr-dvd", "HD DVD lot of 6 includes King Kong, Blade Runner"),
            ("nr-mine", "NordicTrack Treadmill C700 Folding"),
            ("nr-plain", "Griswold No 8 Cast Iron Skillet")):
        db.add(models.Lot(lot_id=lid, title=title, auction_id=a.id,
                          current_bid=5, next_bid=6, status="OPEN",
                          logistics_ease="EASY", source="Ship"))
    db.commit()
    yield db, a
    db.rollback()
    _purge(db)
    db.close()


def _labels(auction_id):
    r = client.get("/lots", params={"limit": 2000, "auction_id": auction_id})
    assert r.status_code == 200, r.text
    body = r.json()
    lots = body.get("items", body) if isinstance(body, dict) else body
    return {l["lot_id"]: l.get("never_label") for l in lots}


def test_the_shipped_rules_install_themselves(sale):
    """The user should not have to seed anything to get the three we agreed."""
    got = client.get("/never")
    assert got.status_code == 200, got.text
    labels = {r["label"] for r in got.json()}
    for expected in ("Sharp items", "Chandeliers and light fixtures",
                     "Vacuum cleaners"):
        assert expected in labels
    assert all(r["seeded"] for r in got.json()
               if r["label"] in labels and r["label"].startswith("Sharp"))


def test_seeding_twice_does_not_duplicate(sale):
    first = client.get("/never").json()
    second = client.get("/never").json()
    assert len(first) == len(second)
    assert [r["id"] for r in first] == [r["id"] for r in second]


def test_lots_carry_the_label_and_the_exceptions_hold(sale):
    _db, a = sale
    client.get("/never")            # ensure seeded
    got = _labels(a.id)
    assert got["nr-knife"] == "Sharp items"
    assert got["nr-dvd"] is None, "Blade Runner is a film"
    assert got["nr-plain"] is None


def test_the_user_can_add_a_category_with_no_deploy(sale):
    """The whole reason this is data: a new kind of unwanted thing takes a
    POST, and the next page of lots already knows about it."""
    _db, a = sale
    assert _labels(a.id)["nr-mine"] is None
    made = client.post("/never", json={
        "label": MINE, "phrases": ["treadmill", "elliptical"],
        "except_phrases": ["treadmill desk"]})
    assert made.status_code == 201, made.text
    assert _labels(a.id)["nr-mine"] == MINE


def test_turning_a_rule_off_stops_it_labelling(sale):
    _db, a = sale
    made = client.post("/never", json={"label": MINE, "phrases": ["treadmill"]}).json()
    assert _labels(a.id)["nr-mine"] == MINE
    off = client.patch(f"/never/{made['id']}", json={"enabled": False})
    assert off.status_code == 200, off.text
    assert _labels(a.id)["nr-mine"] is None


def test_deleting_a_rule_stops_it_labelling(sale):
    _db, a = sale
    made = client.post("/never", json={"label": MINE, "phrases": ["treadmill"]}).json()
    assert client.delete(f"/never/{made['id']}").json()["deleted"] == 1
    assert _labels(a.id)["nr-mine"] is None


def test_an_empty_rule_is_refused(sale):
    """A rule with no phrases matches nothing, but saving one is a mistake
    worth reporting rather than silently keeping."""
    assert client.post("/never", json={"label": MINE, "phrases": []}).status_code == 422
    assert client.post(
        "/never", json={"label": MINE, "phrases": ["  "]}).status_code == 422


def test_preview_shows_what_a_rule_would_hide_before_saving(sale):
    """Writing "blade" without looking is how Blade Runner gets hidden."""
    r = client.post("/never/preview",
                    json={"label": "scratch", "phrases": ["treadmill"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] >= 1
    assert any("Treadmill" in t for t in body["titles"])
    # and it saved nothing
    assert MINE not in {x["label"] for x in client.get("/never").json()}


def test_preview_respects_the_exceptions(sale):
    both = client.post("/never/preview", json={
        "label": "scratch", "phrases": ["blade"]}).json()["matched"]
    fewer = client.post("/never/preview", json={
        "label": "scratch", "phrases": ["blade"],
        "except_phrases": ["dvd"]}).json()["matched"]
    assert fewer < both


def test_patching_to_an_empty_phrase_list_is_refused(sale):
    made = client.post("/never", json={"label": MINE, "phrases": ["treadmill"]}).json()
    assert client.patch(f"/never/{made['id']}", json={"phrases": []}).status_code == 422
    assert client.patch("/never/99999999", json={"enabled": False}).status_code == 404


def test_a_removed_seed_stays_removed(sale):
    """The first version reseeded per label, so deleting "Vacuum cleaners"
    put it back on the next page load - no way to be rid of it short of a
    code change, which is the thing this feature exists to avoid."""
    rules = client.get("/never").json()
    vac = next(r for r in rules if r["label"] == "Vacuum cleaners")
    try:
        assert client.delete(f"/never/{vac['id']}").json()["deleted"] == 1
        again = {r["label"] for r in client.get("/never").json()}
        assert "Vacuum cleaners" not in again
        assert "Sharp items" in again, "the others are untouched"
    finally:
        client.post("/never", json={
            "label": "Vacuum cleaners", "phrases": vac["phrases"],
            "except_phrases": vac["except_phrases"]})
