"""PublicSurplus listing parser — no network.

The HTML shapes here are lifted from a real /browse/search page (captured
2026-09-21): the title attribute, the val_{id}searchGrid price, the
cloudfront thumb, and the countdown script whose fifth argument is the
close time in epoch millis. If the site reshapes its grid, these fail
loudly instead of importing 400 empty lots.
"""

from datetime import datetime

from app.services import publicsurplus

PAGE = '''
<div class="auction-item" id="4085421searchGrid">
 <a href="/sms/all,tx/auction/view?auc=4085421">
  <img class="lazy-img-loading" loading="lazy"
   src="https://d37qv0n5b4mbzm.cloudfront.net/sms/docviewer/cdnmainaucdoc/thumb-b/4085421/71821503" alt="Picture" />
 </a>
 <a href="/sms/all,tx/auction/view?auc=4085421" title="#4085421 - Pots &amp; Pans">#4085421 - Pots &amp; Pans</a>
 Price: <b id="val_4085421searchGrid"> $1,202.50 </b>
 <script> updateTimeLeftSpan(timeLeftInfoMap, 4085421, "4085421searchGrid",
   1789961324417, 1789999200000, 0, 0, "", "", "searchList", timeLeftCallback); </script>
</div>
<div class="auction-item" id="4085411searchGrid">
 <a href="/sms/all,tx/auction/view?auc=4085411" title="#4085411 - Mixed Pile 1">#4085411 - Mixed Pile 1</a>
 Price: <b id="val_4085411searchGrid"> $26.00 </b>
</div>
<span class="me-2" role="button" onclick="srchPage('5');"> 6 </span>
'''


def test_rows_parse_into_items():
    items, last_page = publicsurplus.parse_page(PAGE)
    by_id = {i["auction_id"]: i for i in items}
    assert set(by_id) == {4085421, 4085411}
    pots = by_id[4085421]
    assert pots["lot_id"] == "ps-4085421"
    # The "#4085421 - " prefix stays out of the title: it would poison
    # every comps search built from it. Entities are unescaped.
    assert pots["title"] == "Pots & Pans"
    assert pots["current_bid"] == 1202.50                 # comma handled
    # Epoch millis 1789999200000 → 2026-09-21 14:00:00 UTC.
    assert pots["closes_at"] == datetime(2026, 9, 21, 14, 0, 0)
    assert pots["thumbnail_url"].endswith("/thumb-b/4085421/71821503")
    assert "auc=4085421" in pots["lot_link"]
    assert last_page == 5


def test_rows_missing_extras_still_parse():
    """A row with only a title (no price/thumb/countdown) imports at $0
    with no close time rather than vanishing."""
    items, _ = publicsurplus.parse_page(PAGE)
    pile = next(i for i in items if i["auction_id"] == 4085411)
    assert pile["current_bid"] == 26.0
    assert pile["closes_at"] is None
    assert pile["thumbnail_url"] is None


def test_an_empty_page_parses_to_nothing():
    items, last_page = publicsurplus.parse_page("<html><body></body></html>")
    assert items == []
    assert last_page == 0
