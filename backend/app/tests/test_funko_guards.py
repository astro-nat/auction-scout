"""services.funko: the Funko fraud and mispricing guards. Pure - no network.

The cases are real. Two signed pops were priced at $75 and $80 off comps
that were nearly all authenticated autographs, some of other people, and
neither listing named an authenticator.
"""

from app.services import funko
from app.services.pricing import invented_identifiers

PEDRO = "Pedro Martinez HOF Autographed Funko POP! #55"
CHEVY = 'Chevy Chase Signed Funko Pop! #242 "Christmas Vacation"'


def test_non_funko_lots_are_left_alone():
    assert funko.assess("Signed baseball, no COA") is None
    assert funko.comp_fits("DeWalt DCF825 impact driver", "DeWalt DCF825 signed") is True


def test_an_unauthenticated_signature_is_searched_as_the_plain_pop():
    a = funko.assess(PEDRO)
    assert a["signed_unverified"] and not a["block"]
    assert "not authenticated" in a["note"]
    q = funko.search_query(PEDRO, a)
    assert "autograph" not in q.lower()
    assert "Pedro Martinez" in q and "#55" in q


def test_a_named_authenticator_keeps_the_signature_but_asks_for_the_cert():
    a = funko.assess("Chevy Chase Signed Funko Pop #242 JSA COA")
    assert not a["signed_unverified"]
    assert a["authenticator"] == "JSA"
    assert "verify the cert" in a["note"]
    assert "Signed" in funko.search_query("Chevy Chase Signed Funko Pop #242", a)


def test_custom_and_bootleg_pops_are_blocked():
    for title in ("Custom Funko Pop Walter White", "Bootleg Funko Pop Goku",
                  "Funko Pop knock-off Spider-Man", "Fan made Funko pop Eleven"):
        a = funko.assess(title)
        assert a["block"], title
        assert "not a genuine Funko" in a["note"]
    assert not funko.assess("Funko Pop! Customer Service Rep")["block"]   # whole words only


def test_a_loose_pop_is_priced_against_loose_pops():
    a = funko.assess("Funko Pop Batman #01 out of box")
    assert a["loose"]
    assert funko.search_query("Funko Pop Batman #01", a).endswith("loose")


def test_a_plain_pop_is_not_priced_off_signed_or_variant_comps():
    q = funko.search_query(PEDRO, funko.assess(PEDRO))
    assert not funko.comp_fits(q, "Undertaker Autographed Funko Pop #69 JSA")
    assert not funko.comp_fits(q, "Pedro Martinez Signed Funko Pop #55 Beckett")
    assert not funko.comp_fits(q, "Funko Pop Pedro Martinez #55 Chase")
    assert funko.comp_fits(q, "Funko Pop Pedro Martinez #55 Red Sox")
    assert funko.comp_fits(q, "Funko Pop Pedro Martinez Red Sox")        # silent on number


def test_a_different_box_number_is_a_different_pop():
    q = "Funko Pop Larry Bird #55"
    assert not funko.comp_fits(q, "Funko Pop Larry Bird #77")
    assert funko.comp_fits(q, "Funko Pop Larry Bird #55")


def test_signed_comps_fit_a_signed_lot_whatever_word_they_use():
    q = "Chevy Chase Signed Funko Pop #242 JSA"
    assert funko.comp_fits(q, "Chevy Chase Autographed Funko Pop #242 JSA")
    assert not funko.comp_fits(q, "Chevy Chase Signed Funko Pop #242 Chase")   # the variant, not the name


def test_chase_the_name_is_not_chase_the_variant():
    q = "Chevy Chase Funko Pop #242"
    assert funko.comp_fits(q, "Funko Pop Chevy Chase #242 Christmas Vacation")
    assert not funko.comp_fits(q, "Funko Pop Chevy Chase #242 CHASE")
    assert funko.variant_claims("Paw Patrol Chase Funko Pop", "Funko Pop Paw Patrol Chase") == []
    assert funko.variant_claims("Paw Patrol Chase Funko Pop",
                                "Funko Pop Paw Patrol Chase Chase Variant") == ["Chase"]


def test_the_ai_cannot_invent_a_variant():
    assert funko.variant_claims("Funko Pop Batman #01", "Funko Pop Batman #01 Chase Glow") == ["Chase", "Glow"]
    assert funko.variant_claims("Funko Pop Batman GITD", "Funko Pop Batman Glow in the Dark") == []
    assert "Signed" in invented_identifiers("Funko Pop Batman #01",
                                            "Funko Pop Batman #01 Signed Figure")
