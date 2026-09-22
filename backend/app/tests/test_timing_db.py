"""Phase timing storage (`pytest -m db`).

Exists because Railway keeps a shallow log buffer and the CLI returns a few
dozen lines at a time, which is useless for a job that ran for twenty
minutes. These rows survive and /timings reads them back.

The important property is that instrumentation never breaks the work it
measures: a failed timing write, or a failure inside the timed block, must
leave the caller's behaviour unchanged.
"""

import pytest

from app import models
from app.database import SessionLocal
from app.services import timing
from app.services.timing import timed

pytestmark = pytest.mark.db

LABEL = "timing-unit-test"


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        db = SessionLocal()
        db.query(models.TaskTiming).filter(
            models.TaskTiming.label == LABEL).delete(synchronize_session=False)
        db.commit()
        db.close()
    wipe()
    yield
    wipe()


def _rows():
    db = SessionLocal()
    rows = (db.query(models.TaskTiming)
              .filter(models.TaskTiming.label == LABEL)
              .order_by(models.TaskTiming.id).all())
    db.expunge_all()
    db.close()
    return rows


def test_a_phase_records_its_duration():
    with timed("test", "phase", label=LABEL) as ph:
        ph.add(10)
    row = _rows()[0]
    assert row.kind == "test" and row.phase == "phase"
    assert row.duration_ms >= 0
    assert row.items == 10
    assert row.per_item_ms is not None


def test_sub_steps_aggregate_instead_of_writing_a_row_each():
    """A per-lot database call happens thousands of times. One row per call
    would cost more than the thing being measured."""
    with timed("test", "phase", label=LABEL) as ph:
        for _ in range(50):
            with ph.sub("lookup"):
                pass
        ph.add(50)
    rows = _rows()
    assert len(rows) == 1, "sub-steps should not create their own rows"
    step = rows[0].detail["steps"]["lookup"]
    assert step["calls"] == 50
    assert "total_ms" in step and "avg_ms" in step


def test_notes_ride_along_with_the_duration():
    with timed("test", "phase", label=LABEL) as ph:
        ph.note(created=7, bolo_only=True)
    assert _rows()[0].detail["created"] == 7
    assert _rows()[0].detail["bolo_only"] is True


def test_a_failure_inside_the_block_is_recorded_and_re_raised():
    """Timing must not swallow the error it was wrapped around."""
    with pytest.raises(ValueError):
        with timed("test", "phase", label=LABEL):
            raise ValueError("boom")
    row = _rows()[0]
    assert row.detail["failed"] == "ValueError"


def test_a_broken_timing_store_does_not_break_the_caller(monkeypatch):
    """The whole point of instrumentation is that it is safe to leave on."""
    def explode():
        raise RuntimeError("no database")
    monkeypatch.setattr(timing, "SessionLocal", explode)
    ran = []
    with timed("test", "phase", label=LABEL):
        ran.append(True)
    assert ran == [True], "work did not complete when the timing write failed"


def test_items_are_optional():
    """Not every phase has a natural item count."""
    with timed("test", "phase", label=LABEL):
        pass
    row = _rows()[0]
    assert row.items is None
    assert row.per_item_ms is None
