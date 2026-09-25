"""Reordering the work inside a running job (`pytest -m db`).

The queue the user actually looks at is one long job holding a few hundred
lots. The move controls only ever worked on PENDING jobs and on lots queued
individually - and the worker claims a pending job within seconds, so there
is almost never anything pending to reorder. Meanwhile the Queue view lists
121 lots under "Next up" with no way to pull one forward.

Only the part not yet done may move: `current` counts what is finished, so
reordering something already priced would make the checkpoint mean
something else.
"""

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.services import jobs

pytestmark = pytest.mark.db

client = TestClient(app)


@pytest.fixture
def job():
    db = SessionLocal()
    ids = [901, 902, 903, 904, 905]
    jid = jobs.start("reprice", "in-job reorder test", total=len(ids),
                     payload={"lot_ids": list(ids)})
    jobs.update(jid, current=2)           # 901, 902 are done
    yield db, jid, ids
    db.query(models.Job).filter(models.Job.id == jid).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _tail(db, jid):
    db.expire_all()
    row = db.query(models.Job).filter(models.Job.id == jid).one()
    return (row.payload or {})["lot_ids"][(row.current or 0):]


def _move(jid, item, to):
    return client.post(f"/queue/jobs/{jid}/items/{item}/move", params={"to": to})


def test_an_upcoming_item_moves_to_the_front_of_what_is_left(job):
    db, jid, ids = job
    r = _move(jid, 905, "top")
    assert r.status_code == 200, r.text
    assert r.json() == {"position": 1, "remaining": 3}
    assert _tail(db, jid) == [905, 903, 904]


def test_the_finished_part_of_the_list_is_untouched(job):
    db, jid, ids = job
    _move(jid, 905, "top")
    db.expire_all()
    row = db.query(models.Job).filter(models.Job.id == jid).one()
    assert (row.payload or {})["lot_ids"][:2] == [901, 902]
    assert row.current == 2


def test_up_and_down_move_one_place(job):
    db, jid, ids = job
    _move(jid, 905, "up")
    assert _tail(db, jid) == [903, 905, 904]
    _move(jid, 905, "down")
    assert _tail(db, jid) == [903, 904, 905]


def test_an_item_already_done_cannot_be_moved(job):
    db, jid, ids = job
    r = _move(jid, 901, "top")
    assert r.status_code == 409
    assert "done already" in r.json()["detail"]
    assert _tail(db, jid) == [903, 904, 905]


def test_an_item_that_was_never_in_the_job_is_refused(job):
    db, jid, ids = job
    assert _move(jid, 999999, "top").status_code == 409


def test_a_bad_direction_is_refused(job):
    db, jid, ids = job
    assert _move(jid, 905, "sideways").status_code == 422


def test_an_unknown_job_is_a_404(job):
    assert _move("no-such-job", 905, "top").status_code == 404


def test_a_kind_whose_runner_would_ignore_the_change_is_refused():
    """import-all reads its list once, so accepting a reorder it will not
    honour until a resume is worse than saying no."""
    jid = jobs.start("import-all", "wrong kind", total=2,
                     payload={"auction_ids": [11, 12]})
    try:
        r = _move(jid, 12, "top")
        assert r.status_code == 409
        assert "can't be changed while it runs" in r.json()["detail"]
    finally:
        db = SessionLocal()
        db.query(models.Job).filter(models.Job.id == jid).delete(
            synchronize_session=False)
        db.commit()
        db.close()
