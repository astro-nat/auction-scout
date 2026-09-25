"""count_matching_lots carries a keyword alongside the category, so a
plain-keyword scan (no category picked) can still tell the UI how many
lots in each auction actually match — the "Import N of 'query'" count.

No network — _graphql is monkeypatched with canned response shapes.
"""

import asyncio

from app.services import hibid


def _count_page(n: int) -> dict:
    return {"lotSearch": {"pagedResults": {"totalCount": n}}}


def test_the_keyword_rides_the_same_variables_as_category(monkeypatch):
    seen: list = []

    async def fake_graphql(client, operation, query, variables):
        seen.append(variables)
        return _count_page(7)

    monkeypatch.setattr(hibid, "_graphql", fake_graphql)
    out = asyncio.run(hibid.count_matching_lots([111], -1, "pyrex"))
    assert out == {111: 7}
    assert seen == [{"auctionId": 111, "category": -1, "searchText": "pyrex"}]


def test_a_bare_category_count_sends_an_empty_keyword(monkeypatch):
    """The pre-existing category-only call shape keeps working — an empty
    string, not None, matches what the GraphQL schema expects."""
    seen: list = []

    async def fake_graphql(client, operation, query, variables):
        seen.append(variables)
        return _count_page(3)

    monkeypatch.setattr(hibid, "_graphql", fake_graphql)
    asyncio.run(hibid.count_matching_lots([222], 40252))
    assert seen == [{"auctionId": 222, "category": 40252, "searchText": ""}]


def test_the_two_filters_compose_in_one_call(monkeypatch):
    seen: list = []

    async def fake_graphql(client, operation, query, variables):
        seen.append(variables)
        return _count_page(2)

    monkeypatch.setattr(hibid, "_graphql", fake_graphql)
    asyncio.run(hibid.count_matching_lots([333], 40252, "pyrex"))
    assert seen == [{"auctionId": 333, "category": 40252, "searchText": "pyrex"}]


def test_a_failed_auction_is_simply_omitted(monkeypatch):
    async def fake_graphql(client, operation, query, variables):
        if variables["auctionId"] == 1:
            raise RuntimeError("HiBid flaked")
        return _count_page(5)

    monkeypatch.setattr(hibid, "_graphql", fake_graphql)
    out = asyncio.run(hibid.count_matching_lots([1, 2], -1, "pyrex"))
    assert out == {2: 5}
