"""UI usage events (`pytest -m db`).

What the user actually does in the UI, kept in the app's own Postgres so
the interface can be shaped around the real workflow. Three contracts: a
batch is recorded with its specifics intact, the server caps what one
call may write, and the summary answers "which features, how often" over
a window - the reason the table exists.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)
VIEW = "ui-events-test"          # every row this file writes carries it


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        db = SessionLocal()
        db.query(models.UiEvent).filter(models.UiEvent.view == VIEW).delete(
            synchronize_session=False)
        db.commit()
        db.close()
    wipe()
    yield
    wipe()


def _post(events):
    return client.post("/events", json={"events": events})


def _ours(body):
    return [t for t in body["top"] if t["view"] == VIEW]


def test_a_batch_is_recorded_with_its_specifics(db=None):
    r = _post([{"name": "button", "props": {"name": "Price all N with AI"}, "view": VIEW},
               {"name": "filter", "props": {"key": "roi", "value": ">100"}, "view": VIEW},
               {"name": "filter", "props": {"key": "roi", "value": ">100"}, "view": VIEW}])
    assert r.status_code == 202
    assert r.json() == {"recorded": 3, "dropped": 0}
    top = _ours(client.get("/events/summary?days=1").json())
    roi = next(t for t in top if t["name"] == "filter")
    assert roi["props"] == {"key": "roi", "value": ">100"}
    assert roi["count"] == 2
    assert next(t for t in top if t["name"] == "button")["props"]["name"] == "Price all N with AI"


def test_the_server_caps_one_call():
    """A runaway client must not fill the table: 200 per call, names to 60
    characters, a props blob past 2,000 characters replaced by a marker,
    a blank name skipped."""
    big = [{"name": "button", "props": {"i": i}, "view": VIEW} for i in range(205)]
    body = _post(big).json()
    assert body == {"recorded": 200, "dropped": 5}

    body = _post([{"name": "x" * 100, "view": VIEW},
                  {"name": "   ", "view": VIEW},
                  {"name": "huge", "props": {"blob": "y" * 5000}, "view": VIEW}]).json()
    assert body["recorded"] == 2
    recent = [e for e in client.get("/events/recent?limit=20").json() if e["view"] == VIEW]
    names = {e["name"] for e in recent}
    assert "x" * 60 in names and "huge" in names
    assert next(e for e in recent if e["name"] == "huge")["props"] == {"_truncated": True}


def test_the_summary_counts_by_name_over_a_window():
    _post([{"name": "sort", "props": {"key": "roi"}, "view": VIEW},
           {"name": "sort", "props": {"key": "bid"}, "view": VIEW},
           {"name": "view", "props": {"view": "items"}, "view": VIEW}])
    # Age one row out of a 1-day window.
    db = SessionLocal()
    row = (db.query(models.UiEvent).filter(models.UiEvent.view == VIEW,
                                           models.UiEvent.name == "view").one())
    row.created_at = datetime.now(timezone.utc) - timedelta(days=3)
    db.commit()
    db.close()
    narrow = client.get("/events/summary?days=1").json()
    wide = client.get("/events/summary?days=30").json()
    assert sum(t["count"] for t in _ours(narrow) if t["name"] == "sort") == 2
    assert not any(t["name"] == "view" for t in _ours(narrow))
    assert any(t["name"] == "view" for t in _ours(wide))
    assert narrow["by_name"].get("sort", 0) >= 2


def test_an_empty_batch_is_fine():
    assert _post([]).json() == {"recorded": 0, "dropped": 0}
