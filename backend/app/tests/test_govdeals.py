"""GovDeals normalization and grouping — pure functions, no network.

The maestro search API's rows (captured live 2026-09-21) become the lot
fields the importer stores; sellers become synthetic auctions. The row
shape here is a trimmed real response, so a field rename on their side
shows up as a test failure, not a silent import of empty lots.
"""

from datetime import datetime

from app.services import govdeals

ROW = {
    "accountId": 16144,
    "assetId": 247,
    "assetShortDescription": "Rolls Royce Thruster, Unused",
    "categoryDescription": "Boats, Marine Vessels and Supplies",
    "locationCity": "Galveston", "locationState": "TX", "locationZip": "77554",
    "currentBid": 400000.0,
    "assetBidIncrement": 10000.0,
    "bidCount": None,
    "companyName": "Valaris - US (SM)",
    "photo": "16144_247_10594ae1.jpg?cb=260325124222",
    "assetAuctionEndDateUtc": "2026-10-11T20:15:20Z",
}


def test_a_row_becomes_a_lot():
    lot = govdeals.normalize_asset(ROW)
    assert lot["lot_id"] == "gd-16144-247"
    assert lot["title"] == "Rolls Royce Thruster, Unused"
    assert lot["seller"] == "Valaris - US (SM)"
    assert lot["current_bid"] == 400000.0
    # What the next bidder pays: current + the increment.
    assert lot["next_bid"] == 410000.0
    assert lot["bid_count"] == 0                       # null → 0
    # UTC 'Z' timestamp → naive UTC, the closing_date convention.
    assert lot["closes_at"] == datetime(2026, 10, 11, 20, 15, 20)
    assert lot["lot_link"] == "https://www.govdeals.com/en/asset/247/16144"
    assert lot["fullsize_url"] == ("https://webassets.lqdt1.com/assets/photos/"
                                   "16144/16144_247_10594ae1.jpg?cb=260325124222")
    assert lot["thumbnail_url"].endswith("&w=350")


def test_no_increment_means_next_bid_is_current():
    lot = govdeals.normalize_asset(dict(ROW, assetBidIncrement=None))
    assert lot["next_bid"] == lot["current_bid"] == 400000.0


def test_rows_without_identity_or_title_are_dropped():
    assert govdeals.normalize_asset(dict(ROW, assetId=None)) is None
    assert govdeals.normalize_asset(dict(ROW, assetShortDescription="  ")) is None


def test_a_missing_photo_is_not_a_broken_url():
    lot = govdeals.normalize_asset(dict(ROW, photo=None))
    assert lot["thumbnail_url"] is None
    assert lot["fullsize_url"] is None


def test_an_unparseable_end_date_is_none_not_a_crash():
    lot = govdeals.normalize_asset(dict(ROW, assetAuctionEndDateUtc="soon"))
    assert lot["closes_at"] is None


def test_sellers_group_into_synthetic_auctions():
    a = govdeals.normalize_asset(ROW)
    b = govdeals.normalize_asset(dict(
        ROW, assetId=300, assetAuctionEndDateUtc="2026-10-20T18:00:00Z"))
    other = govdeals.normalize_asset(dict(
        ROW, accountId=999, assetId=1, companyName="City of Houston"))
    groups = govdeals.group_by_seller([a, b, other])
    assert set(groups) == {16144, 999}
    g = groups[16144]
    assert g["external_id"] == "gd-16144"
    assert len(g["assets"]) == 2
    # The auction stays open until the seller's LAST asset closes.
    assert g["closing_date"] == datetime(2026, 10, 20, 18, 0, 0)
    assert groups[999]["seller"] == "City of Houston"


def test_a_nameless_seller_still_gets_a_name():
    lot = govdeals.normalize_asset(dict(ROW, companyName="  "))
    assert lot["seller"] == "Seller 16144"
