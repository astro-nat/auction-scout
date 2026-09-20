"""Pacing, caching and retry around the SoldComps API.

A 429 here is not a harmless miss. An empty sold-comps result sends the lot
to eBay active listings, so being rate limited quietly downgrades a real
sold-price estimate to an asking-price one — the weaker signal the Pearland
audit was full of. In production nearly every lookup was coming back 429.
"""

import threading
import time

import pytest

from app.services import pricing


class _Resp:
    def __init__(self, status, items=None, retry_after=None):
        self.status_code = status
        self._items = items or []
        self.headers = {"Retry-After": retry_after} if retry_after else {}

    def json(self):
        return {"items": self._items}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Each test gets a clean cache, breaker and key."""
    monkeypatch.setattr(pricing, "SOLDCOMPS_API_KEY", "test-key")
    monkeypatch.setattr(pricing, "_cache", {})
    monkeypatch.setattr(pricing, "_consecutive_429", 0)
    monkeypatch.setattr(pricing, "_blocked_until", 0.0)
    monkeypatch.setattr(pricing, "_throttle", pricing._Throttle(0))
    # Zero the BACKOFF rather than patching time.sleep: sleep is what the
    # throttle test is measuring, and stubbing it globally made that test
    # pass trivially against a throttle that wasn't working.
    monkeypatch.setattr(pricing, "_retry_after_seconds", lambda *a: 0.0)


def _client(responses):
    """Stub httpx.Client, recording every query it is asked for."""
    calls = []

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, params=None):
            calls.append(params["keyword"])
            return responses[min(len(calls) - 1, len(responses) - 1)]

    return FakeClient, calls


def test_a_rate_limited_call_is_retried_not_abandoned(monkeypatch):
    sold = [{"soldPrice": "$40.00", "title": "widget"}]
    FakeClient, calls = _client([_Resp(429), _Resp(429), _Resp(200, sold)])
    monkeypatch.setattr(pricing.httpx, "Client", FakeClient)
    out = pricing._soldcomps_lookup("widget")
    assert len(calls) == 3, "gave up instead of retrying"
    assert [(c["price"], c["title"]) for c in out] == [(40.0, "widget")]


def test_giving_up_returns_empty_rather_than_raising(monkeypatch):
    FakeClient, calls = _client([_Resp(429)])
    monkeypatch.setattr(pricing.httpx, "Client", FakeClient)
    assert pricing._soldcomps_lookup("widget") == []
    assert len(calls) == pricing.SOLDCOMPS_MAX_RETRIES


def test_the_servers_own_retry_after_is_honoured(monkeypatch):
    monkeypatch.undo()   # this test is ABOUT _retry_after_seconds
    assert pricing._retry_after_seconds(_Resp(429, retry_after="7"), 0) == 7.0
    # …and capped, so a hostile value can't park a worker for an hour
    assert pricing._retry_after_seconds(_Resp(429, retry_after="9999"), 0) == 30.0
    # …with exponential backoff when the server says nothing
    assert pricing._retry_after_seconds(_Resp(429), 0) == 1.0
    assert pricing._retry_after_seconds(_Resp(429), 2) == 4.0


def test_repeat_queries_are_served_from_cache(monkeypatch):
    sold = [{"soldPrice": "$25.00", "title": "thing"}]
    FakeClient, calls = _client([_Resp(200, sold)])
    monkeypatch.setattr(pricing.httpx, "Client", FakeClient)
    pricing._soldcomps_lookup("thing")
    pricing._soldcomps_lookup("thing")
    pricing._soldcomps_lookup("thing")
    assert len(calls) == 1, "hit the API for a query it already knew"


def test_an_empty_result_is_cached_too(monkeypatch):
    """'Nothing sold matching this' is an answer. Re-asking it per variant
    is what built the burst in the first place."""
    FakeClient, calls = _client([_Resp(200, [])])
    monkeypatch.setattr(pricing.httpx, "Client", FakeClient)
    assert pricing._soldcomps_lookup("nothing") == []
    assert pricing._soldcomps_lookup("nothing") == []
    assert len(calls) == 1


def test_sustained_rate_limiting_opens_the_breaker(monkeypatch):
    FakeClient, calls = _client([_Resp(429)])
    monkeypatch.setattr(pricing.httpx, "Client", FakeClient)
    for i in range(pricing._BREAKER_THRESHOLD + 2):
        pricing._soldcomps_lookup(f"query-{i}")
    assert pricing._breaker_open(), "kept hammering a limit we were over"
    before = len(calls)
    pricing._soldcomps_lookup("another")
    assert len(calls) == before, "called the API while the breaker was open"


def test_a_success_closes_the_breaker_again(monkeypatch):
    sold = [{"soldPrice": "$10.00", "title": "x"}]
    FakeClient, _ = _client([_Resp(429), _Resp(200, sold)])
    monkeypatch.setattr(pricing.httpx, "Client", FakeClient)
    pricing._soldcomps_lookup("x")
    assert pricing._consecutive_429 == 0
    assert not pricing._breaker_open()


def test_the_throttle_spaces_requests_across_threads():
    """The limit belongs to the process, not to one call site — three
    workers share it."""
    throttle = pricing._Throttle(rps=50)     # 20ms apart
    stamps = []
    lock = threading.Lock()

    def worker():
        throttle.wait()
        with lock:
            stamps.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(5)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - start
    # 5 requests at 50/s can't finish faster than ~80ms
    assert elapsed >= 0.07, f"throttle let everything through at once ({elapsed:.3f}s)"


def test_no_api_key_short_circuits(monkeypatch):
    monkeypatch.setattr(pricing, "SOLDCOMPS_API_KEY", "")
    assert pricing._soldcomps_lookup("anything") == []


def test_a_long_retry_after_is_treated_as_quota_not_pace(monkeypatch):
    """The real incident: the FIRST call from a cold container came back 429
    with a ~30s Retry-After. No amount of pacing fixes an empty quota, and
    retrying it burned a minute of worker time per query variant."""
    monkeypatch.undo()
    monkeypatch.setattr(pricing, "SOLDCOMPS_API_KEY", "test-key")
    monkeypatch.setattr(pricing, "_cache", {})
    monkeypatch.setattr(pricing, "_blocked_until", 0.0)
    monkeypatch.setattr(pricing, "_throttle", pricing._Throttle(0))
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    FakeClient, calls = _client([_Resp(429, retry_after="30")])
    monkeypatch.setattr(pricing.httpx, "Client", FakeClient)

    assert pricing._soldcomps_lookup("widget") == []
    assert len(calls) == 1, "retried a wall it could not get past"
    assert not slept, "slept waiting for a quota that resets in hours"
    assert pricing._breaker_open(), "kept asking after hitting the quota wall"


def test_a_short_retry_after_is_still_retried(monkeypatch):
    """Genuine burst throttling — a couple of seconds — should still retry."""
    monkeypatch.undo()
    monkeypatch.setattr(pricing, "SOLDCOMPS_API_KEY", "test-key")
    monkeypatch.setattr(pricing, "_cache", {})
    monkeypatch.setattr(pricing, "_blocked_until", 0.0)
    monkeypatch.setattr(pricing, "_consecutive_429", 0)
    monkeypatch.setattr(pricing, "_throttle", pricing._Throttle(0))
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    sold = [{"soldPrice": "$12.00", "title": "widget"}]
    FakeClient, calls = _client([_Resp(429, retry_after="2"), _Resp(200, sold)])
    monkeypatch.setattr(pricing.httpx, "Client", FakeClient)

    out = pricing._soldcomps_lookup("widget")
    assert [(c["price"], c["title"]) for c in out] == [(12.0, "widget")]
    assert len(calls) == 2


def test_the_breaker_logs_once_not_per_call(monkeypatch, caplog):
    """A wall of identical warnings buries the one line that matters."""
    monkeypatch.undo()
    monkeypatch.setattr(pricing, "_blocked_until", 0.0)
    with caplog.at_level("WARNING"):
        pricing._open_breaker("first")
        pricing._open_breaker("second")
    assert sum("pausing sold-comp" in r.message for r in caplog.records) == 1
