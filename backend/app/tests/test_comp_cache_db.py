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
    pricing._db_cache_put(QUERY, "soldcomps", [(40.0, "widget"), (42.0, "widget b")])
    pricing._cache.clear()          # simulate a restart
    assert pricing._db_cache_get(QUERY, "soldcomps") == [
        (40.0, "widget"), (42.0, "widget b")]


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
    pricing._db_cache_put(QUERY, "soldcomps", [(10.0, "x")])
    assert pricing._db_cache_get(QUERY, "soldcomps") is None


def test_rewriting_a_query_refreshes_its_age():
    pricing._db_cache_put(QUERY, "soldcomps", [(1.0, "old")])
    pricing._db_cache_put(QUERY, "soldcomps", [(2.0, "new")])
    assert pricing._db_cache_get(QUERY, "soldcomps") == [(2.0, "new")]


def test_hits_are_counted_so_the_saving_is_measurable():
    pricing._db_cache_put(QUERY, "soldcomps", [(5.0, "x")])
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
                          [(float(i), f"item {i}") for i in range(500)])
    got = pricing._db_cache_get(QUERY, "soldcomps")
    assert len(got) == pricing._CACHE_MAX_ITEMS


def test_a_broken_cache_never_breaks_the_lookup(monkeypatch):
    """A cache that can take down pricing is worse than no cache."""
    def explode(*a, **k):
        raise RuntimeError("no database")
    monkeypatch.setattr("app.database.SessionLocal", explode)
    assert pricing._db_cache_get(QUERY, "soldcomps") is None
    pricing._db_cache_put(QUERY, "soldcomps", [(1.0, "x")])   # must not raise
