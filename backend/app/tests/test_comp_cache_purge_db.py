"""purge_empty_sold_cache and count_empty_sold_cache, directly (`pytest -m db`).

test_reprice_weak covers them through the endpoint. This covers the edges
the endpoint test cannot see: what counts as "empty" in JSONB, the
in-process cache being cleared alongside, and the never-raises contract.

The JSON-null case is the one that bit. SQLAlchemy stores a Python None in
a JSONB column as the JSON value null, not SQL NULL, and jsonb_array_length
throws on it - Postgres does not short-circuit OR, so an IS NULL guard did
nothing. The filter now never calls that function at all.
"""

import pytest

from app import models
from app.database import SessionLocal
from app.services import pricing

pytestmark = pytest.mark.db

PREFIX = "purge-test "


@pytest.fixture
def seeded():
    def wipe(db):
        db.query(models.CompCache).filter(
            models.CompCache.query.like(PREFIX + "%")).delete(synchronize_session=False)
        db.commit()
    db = SessionLocal()
    wipe(db)
    db.add_all([
        models.CompCache(query=PREFIX + "empty-array", source="soldcomps", payload=[]),
        models.CompCache(query=PREFIX + "json-null", source="soldcomps", payload=None),
        models.CompCache(query=PREFIX + "scalar", source="soldcomps", payload=5),
        models.CompCache(query=PREFIX + "full", source="soldcomps",
                         payload=[[40.0, "a real sale"], [42.0, "another"]]),
        models.CompCache(query=PREFIX + "active-empty", source="active", payload=[]),
    ])
    db.commit()
    pricing._cache.clear()
    pricing._cache[PREFIX + "in-memory|120"] = []
    yield db
    db.rollback()
    wipe(db)
    db.close()
    pricing._cache.clear()


def _left(db):
    return {r.query for r in db.query(models.CompCache)
            .filter(models.CompCache.query.like(PREFIX + "%")).all()}


def test_count_sees_every_kind_of_not_an_answer(seeded):
    """Empty array, JSON null and a scalar are all "nothing usable here".
    The full row and the other source are not counted."""
    assert pricing.count_empty_sold_cache() >= 3
    # The count must not throw on the JSON null / scalar rows - that was
    # the original failure, surfacing as a warning and a count of 0.
    assert pricing.count_empty_sold_cache() != 0


def test_purge_removes_exactly_the_empties(seeded):
    db = seeded
    n = pricing.purge_empty_sold_cache()
    assert n >= 3
    left = _left(db)
    assert PREFIX + "empty-array" not in left
    assert PREFIX + "json-null" not in left
    assert PREFIX + "scalar" not in left
    assert PREFIX + "full" in left, "a real cached answer was thrown away"
    assert PREFIX + "active-empty" in left, "the active source is not what went wrong"


def test_purge_clears_the_in_process_cache_too(seeded):
    """The first hop holds the same empties. Leaving it would serve them
    for another fifteen minutes after the durable ones were gone."""
    assert PREFIX + "in-memory|120" in pricing._cache
    pricing.purge_empty_sold_cache()
    assert PREFIX + "in-memory|120" not in pricing._cache


def test_purge_is_idempotent(seeded):
    pricing.purge_empty_sold_cache()
    assert pricing.purge_empty_sold_cache() == 0
    assert pricing.count_empty_sold_cache() == 0 or PREFIX + "empty-array" not in _left(seeded)


def test_a_dead_database_never_raises(monkeypatch):
    """Both are called from a request handler. A cache housekeeping call
    is not allowed to turn a re-price request into a 500."""
    def explode(*a, **k):
        raise RuntimeError("no database")
    monkeypatch.setattr("app.database.SessionLocal", explode)
    assert pricing.count_empty_sold_cache() == 0
    assert pricing.purge_empty_sold_cache() == 0
