"""invented_identifiers: what the AI title claims that the listing never said.

Pure - no network. Three real lots, one per family, each mispriced by an
order of magnitude on an identity the vision pass asserted with confidence:

  - a model number: "DR-M11" for a deck whose badge read DRM-555
  - a card number: "#221" (the Topps number) on an Upper Deck card
  - a quantity: "Huge Bulk Lot" for ten loose DVDs

And the fourth family, a brand, through the BOLO matcher.
"""

from app.services.pricing import invented_identifiers


def test_a_faithful_title_invents_nothing():
    src = "DeWalt DCF825 18V Cordless Impact Driver"
    assert invented_identifiers(src, "DeWalt DCF825 18V Cordless Impact Driver Tool Only") == []


def test_a_model_number_the_listing_never_had():
    src = "CASSETTE TAPE DECK ~ JN-6-23 (R48B)"
    got = invented_identifiers(src, "Vintage Denon DR-M11 Stereo Cassette Tape Deck Rack Mount Black")
    assert "DR-M11" in got
    # The listing's own tags are not "invented" - they are not in the AI
    # title at all, and if they were, they are present in the source.
    assert not any(t in got for t in ("JN-6-23", "R48B"))


def test_a_card_number_the_listing_never_had():
    src = "2003 NBA Draft LeBron James"
    got = invented_identifiers(src, "2003-04 Upper Deck LeBron James #221 RC Draft Rookie Card Cavaliers")
    assert got == ["#221"]            # "2003-04" is digits only, "RC" letters only


def test_a_quantity_the_listing_never_claimed():
    got = invented_identifiers("dvd movies", "Huge Bulk Lot DVD Movies Discs Only In Plastic Sleeves")
    assert any("Bulk Lot" in g for g in got)


def test_a_quantity_the_listing_did_claim_is_fine():
    assert invented_identifiers("Lot of 10 DVD movies", "Huge Bulk Lot of 10 DVDs Disc Only") == []


def test_presence_ignores_case_spacing_and_punctuation():
    assert invented_identifiers("Denon DRM555 deck", "Denon DRM-555 Cassette Deck") == []
    assert invented_identifiers("dewalt dcf 825 driver", "DeWalt DCF825 Impact Driver") == []


def test_the_description_counts_as_listing_text():
    src = "Cassette deck " + "Model DRM-555, rack ears, tested"
    assert invented_identifiers(src, "Denon DRM-555 Rack Mount Cassette Deck") == []


def test_a_brand_the_listing_never_named_via_the_bolo_matcher():
    class FakeBolo:
        def match(self, text, description=None):
            return {"brand": "Topps"} if "Topps" in (text or "") else None
    src = "2003 NBA Draft LeBron James"
    assert invented_identifiers(src, "2003 NBA Draft LeBron James Rookie Card Topps", FakeBolo()) == ["Topps"]
    assert invented_identifiers("2003 Topps NBA Draft LeBron James",
                                "2003 Topps NBA Draft LeBron James Rookie Card", FakeBolo()) == []


def test_a_broken_matcher_never_blocks():
    class Broken:
        def match(self, *a, **k):
            raise RuntimeError("no brand files")
    assert invented_identifiers("dvd movies", "DVD Movies Lot", Broken()) == []


def test_empty_inputs_are_safe_and_the_list_is_capped():
    assert invented_identifiers("", "") == []
    assert invented_identifiers("x", None) == []
    many = " ".join(f"ZX{i}00" for i in range(12))
    assert len(invented_identifiers("nothing here", many)) == 5
