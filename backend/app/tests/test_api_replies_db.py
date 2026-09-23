"""SoldComps replies are readable from a different process (`pytest -m db`).

The first version of this trace was an in-memory ring buffer. It passed
every test and could not be read at all in production: the worker makes
the SoldComps calls and the backend serves /status, and they are different
containers. So the trace lives in Postgres, and this test writes through
the worker-side function and reads through the backend-side endpoint - the
same two-process path production takes.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.services import pricing

pytestmark = pytest.mark.db

client = TestClient(app)
PREFIX = "api-replies-test "          # every row this file writes starts with it


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        db = SessionLocal()
        db.query(models.ApiReply).filter(
            models.ApiReply.query.like(PREFIX + "%")).delete(synchronize_session=False)
        db.commit()
        db.close()
    wipe()
    yield
    wipe()


def test_a_reply_written_by_the_worker_is_served_by_the_backend():
    """The whole point: written through one function, read through the
    endpoint, no shared memory anywhere in between."""
    pricing._note_response(PREFIX + "dewalt dcf825 tool", 200,
                           total_items=0, items=0, parsed=0)
    r = client.get("/status")
    assert r.status_code == 200
    ours = [x for x in r.json()["soldcomps"] if (x["query"] or "").startswith(PREFIX)]
    assert ours, "the reply never reached /status"
    assert ours[0] == {**ours[0], "status": 200, "total_items": 0,
                       "items": 0, "parsed": 0}


def test_newest_first_and_capped():
    for i in range(pricing._REPLIES_SHOWN + 5):
        pricing._note_response(PREFIX + f"q{i}", 200, total_items=i, items=0, parsed=0)
    got = pricing.soldcomps_recent()
    assert len(got) == pricing._REPLIES_SHOWN
    ours = [x for x in got if x["query"].startswith(PREFIX)]
    assert ours[0]["query"] == PREFIX + f"q{pricing._REPLIES_SHOWN + 4}"


def test_a_failure_with_no_request_is_recorded_as_null_status():
    """No key, or the client raised before sending: status NULL, note says
    why. Distinct from a real HTTP failure, which carries its code."""
    pricing._note_response(PREFIX + "nokey", None, note="no SOLDCOMPS_API_KEY in this process")
    ours = [x for x in pricing.soldcomps_recent() if x["query"].startswith(PREFIX)]
    assert ours[0]["status"] is None
    assert "no SOLDCOMPS_API_KEY" in ours[0]["note"]


def test_the_endpoint_never_carries_the_key(monkeypatch):
    """/status is readable by anyone with the app open."""
    monkeypatch.setattr(pricing, "SOLDCOMPS_API_KEY", "sk-live-should-never-appear")
    pricing._note_response(PREFIX + "leakcheck", 200, total_items=1, items=1, parsed=1)
    assert "sk-live-should-never-appear" not in json.dumps(client.get("/status").json())


def test_a_broken_table_does_not_take_status_down(monkeypatch):
    """The trace is a convenience. /status is not allowed to fail because
    of it."""
    def explode(*a, **k):
        raise RuntimeError("no database")
    monkeypatch.setattr("app.database.SessionLocal", explode)
    assert pricing.soldcomps_recent() == []
    pricing._note_response(PREFIX + "x", 200)          # must not raise
