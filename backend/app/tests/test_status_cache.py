"""/status can't pile up database connections. Pure - the build is stubbed."""

import threading
import time

from app.routers import status


def _stub(monkeypatch, delay=0.0):
    calls = []

    def build(db):
        calls.append(1)
        time.sleep(delay)
        return {"n": len(calls)}

    class _Db:
        def close(self):
            pass

    monkeypatch.setattr(status, "_build_status", build)
    monkeypatch.setattr(status, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(status, "_status_cache", None)
    return calls


def test_one_answer_serves_a_second_of_callers(monkeypatch):
    calls = _stub(monkeypatch)
    assert status.get_status() == {"n": 1}
    assert status.get_status() == {"n": 1}
    assert len(calls) == 1


def test_a_stale_answer_is_rebuilt(monkeypatch):
    calls = _stub(monkeypatch)
    status.get_status()
    status._status_cache = (time.monotonic() - 5, status._status_cache[1])
    assert status.get_status() == {"n": 2}
    assert len(calls) == 2


def test_callers_during_a_slow_build_get_the_last_answer(monkeypatch):
    """The pile-up: many tabs polling while one answer is slow. Only one is
    built at a time; the rest return at once instead of queueing."""
    calls = _stub(monkeypatch)
    status.get_status()                                   # an answer to fall back on
    status._status_cache = (time.monotonic() - 5, status._status_cache[1])
    old = status._status_cache

    def slow(db):
        calls.append(1)
        time.sleep(0.5)
        return {"n": len(calls)}
    monkeypatch.setattr(status, "_build_status", slow)
    monkeypatch.setattr(status, "_status_cache", old)
    t = threading.Thread(target=status.get_status)
    t.start()
    time.sleep(0.1)
    started = time.monotonic()
    got = [status.get_status() for _ in range(5)]
    assert time.monotonic() - started < 0.3              # nobody waited on the slow one
    assert all(g == {"n": 1} for g in got)
    t.join()
    assert len(calls) == 2                               # the first, and the one slow rebuild
