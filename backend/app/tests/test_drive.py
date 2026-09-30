"""Drive times from the saved address to each auction (services/drive).

Pure: the OpenRouteService client is stubbed, so these check what is asked
and how the answers are read - not the network.
"""

import pytest

from app.services import drive


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def _client(handler):
    """Stub httpx.Client; `handler(method, url, params, json)` returns a _Resp."""
    sent = []

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, params=None):
            sent.append(("GET", url, params, None, headers))
            return handler("GET", url, params, None)

        def post(self, url, headers=None, json=None):
            sent.append(("POST", url, None, json, headers))
            return handler("POST", url, None, json)

    return FakeClient, sent


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(drive, "ORS_API_KEY", "test-key")


def test_geocode_reads_lng_lat_in_the_right_order(monkeypatch):
    body = {"features": [{"geometry": {"coordinates": [-95.09, 29.55]},
                          "properties": {"label": "Clear Lake, Houston, TX, USA"}}]}
    Client, sent = _client(lambda *a: _Resp(200, body))
    monkeypatch.setattr(drive.httpx, "Client", Client)
    place = drive.geocode("Clear Lake TX")
    assert (place["lat"], place["lng"]) == (29.55, -95.09)
    assert place["label"] == "Clear Lake, Houston, TX, USA"
    assert sent[0][2]["boundary.country"] == "US"
    assert sent[0][4]["Authorization"] == "test-key"


def test_an_address_with_no_match_says_so(monkeypatch):
    Client, _ = _client(lambda *a: _Resp(200, {"features": []}))
    monkeypatch.setattr(drive.httpx, "Client", Client)
    with pytest.raises(drive.DriveError, match="No match"):
        drive.geocode("nowhere at all")


def test_no_key_is_a_clear_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(drive, "ORS_API_KEY", "")
    with pytest.raises(drive.DriveError, match="ORS_API_KEY"):
        drive.geocode("77058")


@pytest.mark.parametrize("status, words", [(401, "API key"), (403, "API key"),
                                           (429, "daily limit"), (500, "HTTP 500")])
def test_ors_failures_become_messages_a_person_can_act_on(monkeypatch, status, words):
    Client, _ = _client(lambda *a: _Resp(status, {}))
    monkeypatch.setattr(drive.httpx, "Client", Client)
    with pytest.raises(drive.DriveError, match=words):
        drive.geocode("77058")


def test_durations_sends_lng_lat_origin_first_and_reads_minutes(monkeypatch):
    def handler(method, url, params, body):
        n = len(body["destinations"])
        return _Resp(200, {"durations": [[600.0 * (i + 1) for i in range(n)]]})
    Client, sent = _client(handler)
    monkeypatch.setattr(drive.httpx, "Client", Client)
    out = drive.durations((29.55, -95.09), [(7, 29.56, -95.28), (8, 29.50, -95.12)])
    assert out == {7: 10.0, 8: 20.0}
    body = sent[0][3]
    assert body["locations"][0] == [-95.09, 29.55]
    assert body["locations"][1] == [-95.28, 29.56]
    assert body["sources"] == [0] and body["destinations"] == [1, 2]
    assert body["metrics"] == ["duration"]


def test_many_auctions_go_in_batches_under_the_location_cap(monkeypatch):
    def handler(method, url, params, body):
        assert len(body["locations"]) <= drive._MATRIX_LOCATIONS
        return _Resp(200, {"durations": [[60.0] * len(body["destinations"])]})
    Client, sent = _client(handler)
    monkeypatch.setattr(drive.httpx, "Client", Client)
    dests = [(i, 29.0, -95.0) for i in range(120)]
    out = drive.durations((29.5, -95.1), dests)
    assert len(out) == 120 and set(out.values()) == {1.0}
    assert len(sent) == 3


def test_an_unroutable_auction_gets_no_time(monkeypatch):
    Client, _ = _client(lambda *a: _Resp(200, {"durations": [[None, 900.0]]}))
    monkeypatch.setattr(drive.httpx, "Client", Client)
    assert drive.durations((29.5, -95.1), [(1, 0.1, 0.1), (2, 29.6, -95.2)]) == {1: None, 2: 15.0}


def test_hibid_zero_coordinates_are_not_a_place():
    assert not drive.usable_coords(0, 0)
    assert not drive.usable_coords(None, -95.1)
    assert not drive.usable_coords("x", 1)
    assert drive.usable_coords(29.55, -95.09)


def test_the_origin_key_changes_when_the_address_does():
    a = drive.origin_key({"lat": 29.55, "lng": -95.09})
    b = drive.origin_key({"lat": 29.56, "lng": -95.09})
    assert a != b and a == drive.origin_key({"lat": 29.550001, "lng": -95.090001})


def test_hibid_locations_are_read_and_a_failed_batch_is_skipped(monkeypatch):
    import asyncio
    from app.services import hibid
    monkeypatch.setattr(hibid, "META_CHUNK", 2)
    calls = []

    async def fake_graphql(client, op, query, variables):
        calls.append(variables["eventIds"])
        if variables["eventIds"] == [3, 4]:
            raise RuntimeError("HiBid hiccup")
        return {"auctionMap": {"mapMarkers": [
            {"auction": {"id": i, "geoLat": 29.5, "geoLong": -95.1, "eventAddress": f"{i} Main St"}}
            for i in variables["eventIds"]]}}

    monkeypatch.setattr(hibid, "_graphql", fake_graphql)
    out = asyncio.run(hibid.fetch_locations([1, 2, 3, 4, 5]))
    assert calls == [[1, 2], [3, 4], [5]]
    assert sorted(out) == [1, 2, 5]
    assert out[1] == {"geo_lat": 29.5, "geo_lng": -95.1, "address": "1 Main St"}
