"""Overload behaviour: one heavy job at a time, and bookkeeping that can't
take a worker down with it.

Both are from a real incident — the connection pool filled, a progress write
timed out, and because that call sits outside the per-lot try/except it
unwound run_reprice entirely and left a job row showing progress forever.
"""

import pytest

from app.services import jobs


def test_heavy_job_blocks_another_heavy_job(monkeypatch):
    monkeypatch.setattr(jobs, "active",
                        lambda: [{"kind": "reprice", "cancelled": False}])
    assert jobs.heavy_running() == "reprice"


def test_cancelled_job_does_not_block(monkeypatch):
    monkeypatch.setattr(jobs, "active",
                        lambda: [{"kind": "reprice", "cancelled": True}])
    assert jobs.heavy_running() is None


def test_light_job_does_not_block(monkeypatch):
    """A regrade is seconds of arithmetic with no network — never a blocker."""
    monkeypatch.setattr(jobs, "active",
                        lambda: [{"kind": "regrade", "cancelled": False}])
    assert jobs.heavy_running() is None


def test_a_job_does_not_block_itself(monkeypatch):
    monkeypatch.setattr(jobs, "active",
                        lambda: [{"kind": "bid-refresh", "cancelled": False}])
    assert jobs.heavy_running(ignore="bid-refresh") is None


def test_every_long_network_job_counts_as_heavy():
    for kind in ("reprice", "ship-analysis", "bid-refresh", "import", "scan"):
        assert kind in jobs.HEAVY_KINDS


class _DeadPool:
    """Stands in for SessionLocal when the pool has nothing left to give."""
    def __call__(self):
        raise TimeoutError("QueuePool limit of size 10 overflow 20 reached")


def test_progress_update_survives_a_dead_pool(monkeypatch):
    monkeypatch.setattr(jobs, "SessionLocal", _DeadPool())
    jobs.update("abc123", current=5)      # must not raise


def test_cancel_check_keeps_working_when_it_cannot_ask(monkeypatch):
    """Returning True here would silently abandon every long job whenever the
    pool got busy — a missing row means cancelled, a failed lookup does not."""
    monkeypatch.setattr(jobs, "SessionLocal", _DeadPool())
    assert jobs.is_cancelled("abc123") is False


def test_status_still_answers_when_the_pool_is_the_problem(monkeypatch):
    monkeypatch.setattr(jobs, "SessionLocal", _DeadPool())
    assert jobs.active() == []


def test_finish_does_not_raise_out_of_a_finally_block(monkeypatch):
    monkeypatch.setattr(jobs, "SessionLocal", _DeadPool())
    jobs.finish("abc123")                 # must not raise
