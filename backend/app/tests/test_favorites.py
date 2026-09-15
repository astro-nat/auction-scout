"""Favourited auction houses, keyed by HiBid's company id."""

import pytest

from app.services import favorites


@pytest.mark.parametrize("value,expected", [
    ("https://hibid.com/company/149798/budget-barn", 149798),
    ("http://hibid.com/company/149798/budget-barn", 149798),
    ("hibid.com/company/149798", 149798),
    ("https://hibid.com/company/147417/diamond-k-armory?tab=auctions", 147417),
    ("149798", 149798),
    (149798, 149798),
])
def test_reads_the_company_id_from_whatever_is_pasted(value, expected):
    assert favorites.parse_company_id(value) == expected


@pytest.mark.parametrize("value", [
    "", None, "budget barn", "https://hibid.com/auction/774265", 0, -5,
])
def test_rejects_things_that_are_not_a_company_reference(value):
    assert favorites.parse_company_id(value) is None


def test_auction_urls_are_not_company_urls():
    """/auction/ and /company/ both carry a number; only one is a house."""
    assert favorites.parse_company_id("https://hibid.com/auction/774265") is None
