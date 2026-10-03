"""invented_identifiers: what the AI title claims that the listing never said.

Pure - no network. Three real lots, one per family, each mispriced by an
order of magnitude on an identity the vision pass asserted with confidence:

  - a model number: "DR-M11" for a deck whose badge read DRM-555
  - a card number: "#221" (the Topps number) on an Upper Deck card
  - a quantity: "Huge Bulk Lot" for ten loose DVDs

And the fourth family, a brand, through the BOLO matcher.
"""

import pytest

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



# --- 2026-10-03: 36 of 37 identity flags were false -----------------------

class _Bolo:
    """The real matcher's answers for these titles."""
    LABELS = {   # in the real matcher's precedence: brands before metals
        "pandora": ("James Avery + Brighton + Pandora", "modern_jewelry"),
        "american girl": ("American Girl Pleasant Company", "nostalgia_doll"),
        "sterling silver": ("Sterling silver", "precious_metal"),
        "14k": ("Solid gold (10K-24K)", "precious_metal"),
    }

    def match(self, text, *_):
        t = (text or "").lower()
        for key, (brand, cat) in self.LABELS.items():
            if key in t:
                return {"brand": brand, "category": cat}
        return None


@pytest.mark.parametrize("src,ai", [
    ("18K Yellow Gold True Ruby & Diamond Custom Earrings",
     "18K Yellow Gold Ruby Diamond Custom Earrings 4.42g"),
    ("18K Yellow Gold Fine Pearl Station Custom Necklace",
     "18K Yellow Gold Pearl Station Custom Necklace 15in"),
    ("Traditional Oriental Red Runner Rug", "Traditional Oriental Red Runner Rug 2x6.5 ft"),
    ("Fine Sterling Silver Garnet Bracelet", "Vintage 925 Sterling Silver 3-Row Garnet Bracelet"),
    ("Rare Sterling Larimar Chunky Bracelet", "Chunky Larimar Stone Bracelet 8-9in"),
])
def test_what_the_ai_saw_is_not_an_invention(src, ai):
    assert invented_identifiers(src, ai) == []


def test_a_material_label_is_not_a_brand():
    assert invented_identifiers("Antique Sterling & 14K Doctors Caduceus Ring",
                                "Vintage Sterling Silver 14K Gold Caduceus Ring", _Bolo()) == []


def test_a_brand_the_listing_already_names_is_not_invented():
    assert invented_identifiers("Limited Edition American Girl Frozen Anna Doll",
                                "American Girl Disney Frozen Anna Doll", _Bolo()) == []


def test_a_brand_the_listing_never_named_is_still_caught():
    claims = invented_identifiers("Sterling Silver Fish Pendant",
                                  "Pandora Retired Sterling Silver Fish Bead Charm", _Bolo())
    assert "James Avery + Brighton + Pandora" in claims


def test_a_model_number_is_still_caught():
    assert invented_identifiers("Taxco Sterling Silver Double Hoop Earrings",
                                "Taxco Sterling Silver Hoop Earrings TS-50") == ["TS-50"]
