"""services.publicsurplus.parse_detail: the seller's own text for one item.

Pure - no network. The fragment is the shape publicsurplus.com served for
auction 4085372 on 2026-09-25, the Dell Precision 7750 the app had called
"normal wear and tear" while the page said it has no hard drive and no
power cord, because the listing grid the scan reads carries no description
at all.
"""

from app.services.publicsurplus import DESCRIPTION_MAX, parse_detail

PAGE = """
<div class="w-auto mt-2">
  <div>
    <span class="auctitle">Condition:</span>
    <span>
        UNKNOWN
    </span>
  </div>
</div>
<div style="overflow-wrap: anywhere">
  <div><div>Used Dell Precision 7750 Laptop Core i5 (Qty. 1)<br /><br /></div>
  <p>FXA 92561 Svc. Tag JWRGZ33</p>
  <div><br /><span><span style="color: #000000;"><strong>**Does&nbsp;not have Hard Drive.</strong></span>
  <br /><span><strong><span>**No power cord.</span><br /></strong></span><br /><br />
  *** ITEMS ARE SOLD "AS-IS, WHERE-IS ", WITHOUT WARRANTIES.&nbsp;</span>
  <span>Winning bidder must pay and take possession of complete lots.***</span>
  <br /><strong>WE DO NOT SHIP ITEMS. Bidders may contact a local shipping company.</strong>
  </div></div>
</div>
<!-- DOCUMENTS -->
<img src="/sms/docviewer/aucdoc/IMG_1490.jpeg?auc=4085372" />
"""


def test_the_disclosure_that_changes_the_price_is_kept():
    d = parse_detail(PAGE)
    assert "Does not have Hard Drive" in d["description"]
    assert "No power cord" in d["description"]
    assert "Svc. Tag JWRGZ33" in d["description"]


def test_the_page_s_own_condition_rides_along():
    d = parse_detail(PAGE)
    assert d["condition"] == "UNKNOWN"
    assert d["description"].startswith("Condition: UNKNOWN")


def test_the_agency_boilerplate_is_dropped():
    """Identical on every lot from the seller, and most of the text: it
    would crowd the real disclosure out of the model's prompt."""
    d = parse_detail(PAGE)
    for junk in ("AS-IS", "WE DO NOT SHIP", "Winning bidder", "WITHOUT WARRANTIES"):
        assert junk not in d["description"], junk
    assert len(d["description"]) < 200


def test_the_pictures_after_the_block_are_not_description():
    assert "IMG_1490" not in parse_detail(PAGE)["description"]


def test_a_page_with_no_description_is_empty_not_an_error():
    assert parse_detail("<html><body>nothing here</body></html>") == {
        "description": "", "condition": ""}
    assert parse_detail("") == {"description": "", "condition": ""}


def test_a_runaway_description_is_capped():
    long_page = ('<div style="overflow-wrap: anywhere">'
                 + "word " * 2000 + "</div><!-- DOCUMENTS -->")
    assert len(parse_detail(long_page)["description"]) <= DESCRIPTION_MAX
