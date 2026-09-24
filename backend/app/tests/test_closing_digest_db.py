"""Closing-window digests (`pytest -m db`): one push per auction, the right
auctions, the right lots, exactly once.

_push is monkeypatched to capture instead of send, so these exercise the
whole pass — window query, floor gate, digest body, dedupe stamps — with no
network and no phone buzzing during a test run.
"""

from datetime import datetime, timedelta

import httpx
import pytest

from app.database import SessionLocal
from app import models
from app.workers import notify

pytestmark = pytest.mark.db

TEST_HIBID = 999999971
PREFIX = "pytest-digest-"


@pytest.fixture()
def pushes(monkeypatch):
    """Capture pushes; every capture 'succeeds' so stamps get written.

    Drains one pass first: the shared dev database can hold REAL auctions
    inside the closing window, and stamping them now (pushes discarded)
    leaves the test's own pass seeing only what the test seeded."""
    sent = []
    monkeypatch.setattr(notify, "_push",
                        lambda title, body, click: sent.append(
                            {"title": title, "body": body, "click": click}) or True)
    notify.check_closing_digests()
    sent.clear()
    return sent


def _mk_auction(db, offset_hibid, name, closes_in_hours):
    a = models.Auction(hibid_id=TEST_HIBID + offset_hibid, name=name,
                       closing_date=datetime.now() + timedelta(hours=closes_in_hours),
                       source_url="https://hibid.com/catalog/pytest",
                       imported_at=datetime.now())
    db.add(a)
    db.flush()
    return a


def _mk_lot(db, auction, suffix, *, watched=False, hidden=False, profit=None,
            roi_status=None, max_bid=None, resale=None, lot_number=None):
    lot = models.Lot(lot_id=f"{PREFIX}{suffix}", auction_id=auction.id,
                     title=f"pytest {suffix}", status="OPEN", current_bid=5,
                     watched=watched, hidden=hidden, lot_number=lot_number)
    db.add(lot)
    db.flush()
    db.add(models.Enrichment(lot_id=lot.id, status="success",
                             est_resale=resale, profit=profit,
                             roi_status=roi_status, max_bid=max_bid))
    return lot


@pytest.fixture()
def clean():
    yield
    db = SessionLocal()
    ids = [r[0] for r in db.query(models.Lot.id)
                           .filter(models.Lot.lot_id.like(f"{PREFIX}%")).all()]
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Auction).filter(
        models.Auction.hibid_id.between(TEST_HIBID, TEST_HIBID + 9)).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def test_one_digest_per_qualifying_auction(pushes, clean):
    db = SessionLocal()
    a = _mk_auction(db, 0, "PYTEST closing rich sale", closes_in_hours=1)
    _mk_lot(db, a, "gold-big", profit=120, roi_status="GOLD MINE",
            max_bid=80, resale=200, lot_number="7")
    _mk_lot(db, a, "gold-small", profit=95, roi_status="GOLD MINE",
            max_bid=40, resale=110, lot_number="12")
    _mk_lot(db, a, "watched-pass", watched=True, profit=-5, roi_status="PASS",
            max_bid=10, resale=30, lot_number="55")
    _mk_lot(db, a, "bystander", profit=4, roi_status="PASS")
    _mk_lot(db, a, "hidden-gold", hidden=True, profit=70, roi_status="GOLD MINE")
    db.commit()
    db.close()

    assert notify.check_closing_digests() == 1
    assert len(pushes) == 1
    p = pushes[0]
    assert "PYTEST closing rich sale" in p["title"]
    assert p["click"] == "https://hibid.com/catalog/pytest"
    body = p["body"]
    assert "~$215 trusted profit on the board" in body      # 120 + 95
    assert body.splitlines()[1].startswith("★ #55")         # shortlist first
    assert "#7 pytest gold-big — bid $5 / max $80 (resale $200)" in body
    assert "hidden-gold" not in body                        # hidden stays hidden
    assert "bystander" not in body                          # PASS and unwatched

    # Exactly once: a second pass finds the stamp and stays silent.
    assert notify.check_closing_digests() == 0
    assert len(pushes) == 1


def test_no_gold_unwatched_closes_in_silence(pushes, clean):
    db = SessionLocal()
    a = _mk_auction(db, 1, "PYTEST thin sale", closes_in_hours=1)
    _mk_lot(db, a, "thin-pass", profit=22, roi_status="PASS", max_bid=10)
    db.commit()
    db.close()

    assert notify.check_closing_digests() == 0
    assert pushes == []
    # …and it was stamped as decided, not left for the next pass to re-judge.
    db = SessionLocal()
    stamped = (db.query(models.Auction.closing_digest_sent_at)
                 .filter(models.Auction.hibid_id == TEST_HIBID + 1).scalar())
    db.close()
    assert stamped is not None


def test_watched_lot_alone_earns_the_digest(pushes, clean):
    """★ is the user's own shortlist — it needs no gold to matter."""
    db = SessionLocal()
    a = _mk_auction(db, 2, "PYTEST watched-only sale", closes_in_hours=1)
    _mk_lot(db, a, "just-watched", watched=True, profit=None, max_bid=None,
            lot_number="3")
    db.commit()
    db.close()

    assert notify.check_closing_digests() == 1
    assert "★ #3 pytest just-watched — bid $5" in pushes[0]["body"]


def _validating_post(url, params=None, content=None, headers=None, timeout=None):
    """No network, real validation: constructing the Request runs httpx's
    header/URL encoding — which is exactly where non-ASCII dies."""
    req = httpx.Request("POST", url, params=params, headers=headers,
                        content=content)
    return httpx.Response(200, request=req)


def test_push_survives_the_titles_we_actually_send(monkeypatch):
    """The digest title leads with ⏱ and auction names carry anything.
    HTTP headers are ASCII-only in httpx, so metadata must not ride there."""
    monkeypatch.setattr(notify.config, "NTFY_TOPIC", "pytest-topic")
    monkeypatch.setattr(notify.httpx, "post", _validating_post)
    assert notify._push("⏱ Sépt Sale — closing window", "★ #7 lot — bid $5",
                        "https://hibid.com/catalog/x") is True


def test_push_swallows_transport_errors(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("ntfy down")
    monkeypatch.setattr(notify.config, "NTFY_TOPIC", "pytest-topic")
    monkeypatch.setattr(notify.httpx, "post", boom)
    assert notify._push("t", "b", None) is False    # never raises


def test_failed_push_is_retried_next_pass(pushes, clean, monkeypatch):
    """ntfy being down for one poll must not eat the digest: the stamp is
    only written on a successful push."""
    db = SessionLocal()
    a = _mk_auction(db, 5, "PYTEST flaky-push sale", closes_in_hours=1)
    _mk_lot(db, a, "flaky-gold", profit=300, roi_status="GOLD MINE", max_bid=90)
    db.commit()
    db.close()

    attempts = {"n": 0}

    def flaky(title, body, click):
        attempts["n"] += 1
        return attempts["n"] > 1        # first delivery fails, second lands

    monkeypatch.setattr(notify, "_push", flaky)
    assert notify.check_closing_digests() == 0      # failed → not stamped
    assert notify.check_closing_digests() == 1      # retried → delivered
    assert notify.check_closing_digests() == 0      # …and only once
    assert attempts["n"] == 2


def test_digest_caps_at_eight_lot_lines(pushes, clean):
    db = SessionLocal()
    a = _mk_auction(db, 6, "PYTEST deep sale", closes_in_hours=1)
    for i in range(10):
        _mk_lot(db, a, f"deep-{i}", profit=100 + i, roi_status="GOLD MINE",
                max_bid=50, lot_number=str(i))
    db.commit()
    db.close()

    assert notify.check_closing_digests() == 1
    lines = pushes[0]["body"].splitlines()
    assert len(lines) == 1 + notify.MAX_LOT_LINES + 1   # head + lots + more
    assert lines[-1] == "…and 2 more"
    assert "#9 " in lines[1]                            # best profit leads


def test_watched_lot_without_enrichment_row_still_digests(pushes, clean):
    """★ on a lot the enricher never touched — the outer join must keep it."""
    db = SessionLocal()
    a = _mk_auction(db, 7, "PYTEST bare-watch sale", closes_in_hours=1)
    lot = models.Lot(lot_id=f"{PREFIX}bare-watch", auction_id=a.id,
                     title="pytest bare-watch", status="OPEN",
                     current_bid=5, watched=True)
    db.add(lot)
    db.commit()
    db.close()

    assert notify.check_closing_digests() == 1
    assert "★ pytest bare-watch — bid $5" in pushes[0]["body"]


def test_outside_the_window_nothing_happens(pushes, clean):
    db = SessionLocal()
    far = _mk_auction(db, 3, "PYTEST tomorrow sale", closes_in_hours=30)
    _mk_lot(db, far, "far-gold", profit=500, roi_status="GOLD MINE")
    done = _mk_auction(db, 4, "PYTEST finished sale", closes_in_hours=-1)
    _mk_lot(db, done, "done-gold", profit=500, roi_status="GOLD MINE")
    db.commit()
    db.close()

    assert notify.check_closing_digests() == 0
    assert pushes == []
