"""Durable comp cache (`pytest -m db`).

Measured on the real catalogue: 7,474 lots generate 26,159 query variants,
19.5% of them exact duplicates - one padlock query repeats 90 times across
identical lots in a single liquidation auction. A cache catches those, but
only if it outlives the process: the in-memory one has a 15-minute TTL and
dies on every deploy, so a re-price paid full price every time.
"""

import pytest

from app import models
from app.database import SessionLocal
from app.services import pricing

pytestmark = pytest.mark.db

QUERY = "comp-cache-test widget|120"


def _comp(price, title):
    """A comp as the lookup returns it. These tests used bare (price, title)
    pairs for years after comps became records, which is how a cache that
    failed every real write still passed."""
    return {"price": price, "title": title, "url": f"https://ebay.example/{title}",
            "date": "2026-11-01", "kind": "sold", "ship": None, "ship_cost": None}


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        db = SessionLocal()
        db.query(models.CompCache).filter(
            models.CompCache.query == QUERY).delete(synchronize_session=False)
        db.commit()
        db.close()
    wipe()
    pricing._cache.clear()
    yield
    wipe()
    pricing._cache.clear()


def test_an_answer_survives_the_process_that_fetched_it():
    """The whole point: the in-memory cache dies on deploy, this does not."""
    comps = [_comp(40.0, "widget"), _comp(42.0, "widget b")]
    pricing._db_cache_put(QUERY, "soldcomps", comps)
    pricing._cache.clear()          # simulate a restart
    assert pricing._db_cache_get(QUERY, "soldcomps") == comps


def test_an_empty_answer_is_cached_too():
    """'Nothing sold matching this' is an answer. Re-asking it once per
    query variant is what burned the quota."""
    pricing._db_cache_put(QUERY, "soldcomps", [])
    assert pricing._db_cache_get(QUERY, "soldcomps") == []


def test_a_miss_is_distinguishable_from_an_empty_hit():
    """None means 'never asked'; [] means 'asked, nothing there'. Confusing
    the two would either re-ask forever or never ask at all."""
    assert pricing._db_cache_get("never-seen-query", "soldcomps") is None
    pricing._db_cache_put(QUERY, "soldcomps", [])
    assert pricing._db_cache_get(QUERY, "soldcomps") == []


def test_an_expired_row_reads_as_a_miss(monkeypatch):
    monkeypatch.setattr(pricing, "COMP_CACHE_DAYS", -1)   # everything is stale
    pricing._db_cache_put(QUERY, "soldcomps", [_comp(10.0, "x")])
    assert pricing._db_cache_get(QUERY, "soldcomps") is None


def test_rewriting_a_query_refreshes_its_age():
    pricing._db_cache_put(QUERY, "soldcomps", [_comp(1.0, "old")])
    pricing._db_cache_put(QUERY, "soldcomps", [_comp(2.0, "new")])
    assert pricing._db_cache_get(QUERY, "soldcomps") == [_comp(2.0, "new")]


def test_hits_are_counted_so_the_saving_is_measurable():
    pricing._db_cache_put(QUERY, "soldcomps", [_comp(5.0, "x")])
    pricing._db_cache_get(QUERY, "soldcomps")
    pricing._db_cache_get(QUERY, "soldcomps")
    db = SessionLocal()
    row = db.query(models.CompCache).filter(
        models.CompCache.query == QUERY).one()
    assert row.hits >= 2
    db.close()


def test_the_payload_is_capped():
    """A cache, not an archive."""
    pricing._db_cache_put(QUERY, "soldcomps",
                          [_comp(float(i), f"item {i}") for i in range(500)])
    got = pricing._db_cache_get(QUERY, "soldcomps")
    assert len(got) == pricing._CACHE_MAX_ITEMS


def test_a_broken_cache_never_breaks_the_lookup(monkeypatch):
    """A cache that can take down pricing is worse than no cache."""
    def explode(*a, **k):
        raise RuntimeError("no database")
    monkeypatch.setattr("app.database.SessionLocal", explode)
    assert pricing._db_cache_get(QUERY, "soldcomps") is None
    pricing._db_cache_put(QUERY, "soldcomps", [_comp(1.0, "x")])   # must not raise


def test_a_row_from_before_comps_were_records_still_reads():
    db = SessionLocal()
    db.add(models.CompCache(query=QUERY, source="soldcomps", payload=[[12.5, "old widget"]]))
    db.commit()
    db.close()
    assert pricing._db_cache_get(QUERY, "soldcomps") == [
        {"price": 12.5, "title": "old widget", "url": None, "date": None, "kind": "sold"}]


def test_a_full_lookup_is_kept_whole():
    """A lookup asks for up to 120; a 60-row cap priced repeats thinner."""
    comps = [_comp(float(i), f"w{i}") for i in range(120)]
    pricing._db_cache_put(QUERY, "soldcomps", comps)
    assert len(pricing._db_cache_get(QUERY, "soldcomps")) == 120


def test_shipping_survives_the_cache():
    comps = [{**_comp(30.0, "free one"), "ship": "free", "ship_cost": None},
             {**_comp(30.0, "paid one"), "ship": "paid", "ship_cost": 8.5}]
    pricing._db_cache_put(QUERY, "soldcomps", comps)
    got = pricing._db_cache_get(QUERY, "soldcomps")
    assert [(c["ship"], c["ship_cost"]) for c in got] == [("free", None), ("paid", 8.5)]
