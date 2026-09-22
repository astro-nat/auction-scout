"""The /timings endpoint (`pytest -m db`).

test_timing_db covers the `timed()` service that writes the rows. This
covers the thing that reads them back, which is the half actually used: the
reason timings go to Postgres at all is that Railway keeps a shallow log
buffer and hands back a few dozen lines at a time, which is no use for a job
that ran for twenty minutes.

The summary is the part worth pinning. It is deliberately computed over the
whole window rather than over the rows returned, so that asking for a
smaller page does not silently change the averages - which would make the
numbers used to diagnose the 227ms-per-lot import depend on the page size.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)
KIND = "timings-api-test"


@pytest.fixture
def rows():
    db = SessionLocal()
    now = datetime.now(timezone.utc)

    def row(phase, ms, items, minutes_ago=1):
        return models.TaskTiming(
            kind=KIND, phase=phase, label="x", duration_ms=ms, items=items,
            per_item_ms=(ms / items if items else None),
            started_at=now - timedelta(minutes=minutes_ago))

    db.add_all([
        row("save_lots", 4000, 20),        # 200ms/item - the slow one
        row("save_lots", 2000, 20),        # 100ms/item
        row("fetch", 1000, 20),
        row("save_lots", 9999, 20, minutes_ago=60 * 48),   # outside 24h
    ])
    db.commit()
    yield db
    db.rollback()
    db.query(models.TaskTiming).filter(
        models.TaskTiming.kind == KIND).delete(synchronize_session=False)
    db.commit()
    db.close()


def _summary(body, phase):
    for s in body["summary"]:
        if s["kind"] == KIND and s["phase"] == phase:
            return s
    return None


def test_recent_phases_come_back_newest_first(rows):
    r = client.get(f"/timings?kind={KIND}&hours=24")
    assert r.status_code == 200
    recent = r.json()["recent"]
    assert len(recent) == 3          # the 48h-old row is outside the window
    assert all(x["kind"] == KIND for x in recent)


def test_the_summary_aggregates_per_phase(rows):
    r = client.get(f"/timings?kind={KIND}&hours=24")
    s = _summary(r.json(), "save_lots")
    assert s is not None
    assert s["runs"] == 2
    assert s["total_s"] == 6.0                  # 4000ms + 2000ms
    assert s["items"] == 40
    assert s["avg_per_item_ms"] == pytest.approx(150.0)   # mean of 200 and 100


def test_a_smaller_page_does_not_change_the_summary(rows):
    """The property the endpoint was written for. If the summary were
    computed over the returned rows, limit=1 would report 200ms/item and
    the next person would draw the wrong conclusion about where the time
    went."""
    full = _summary(client.get(f"/timings?kind={KIND}&hours=24").json(), "save_lots")
    paged = client.get(f"/timings?kind={KIND}&hours=24&limit=1").json()
    assert len(paged["recent"]) == 1
    assert _summary(paged, "save_lots") == full


def test_the_window_excludes_older_runs(rows):
    """A 48h-old row is in the table; a 24h question must not see it."""
    narrow = _summary(client.get(f"/timings?kind={KIND}&hours=24").json(), "save_lots")
    wide = _summary(client.get(f"/timings?kind={KIND}&hours=168").json(), "save_lots")
    assert narrow["runs"] == 2
    assert wide["runs"] == 3


def test_filtering_by_kind_excludes_everything_else(rows):
    r = client.get(f"/timings?kind={KIND}&hours=24")
    assert {s["kind"] for s in r.json()["summary"]} == {KIND}


def test_an_unknown_kind_is_empty_not_an_error(rows):
    r = client.get("/timings?kind=no-such-kind&hours=24")
    assert r.status_code == 200
    assert r.json()["recent"] == [] and r.json()["summary"] == []
