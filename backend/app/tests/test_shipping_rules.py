"""services.shipping: does a Canadian house ship into the US? Pure - no
network. The wording is lifted from real HiBid terms pages."""

from app.services.shipping import is_canadian, resolve, ships_to_us_from_text


def test_provinces_are_recognised_however_hibid_spells_them():
    assert is_canadian("ON")
    assert is_canadian("On")
    assert is_canadian(" qc ")
    assert not is_canadian("TX")
    assert not is_canadian(None)
    assert not is_canadian("")


def test_plain_refusals_are_read_as_no():
    for text in (
        "Shipping available within Canada only.",
        "We ship in Canada. We do not ship to the USA.",
        "Canadian addresses only, no exceptions",
        "Sorry, no international shipping.",
        "We cannot ship outside of Canada",
        "Domestic shipping only",
        "Will not ship to the U.S.",
    ):
        assert ships_to_us_from_text(text, "") is False, text


def test_plain_offers_are_read_as_yes():
    for text in (
        "We ship to Canada and the US.",
        "Shipping available to the United States via UPS.",
        "US buyers welcome - shipping calculated after the sale.",
        "International shipping is available at the buyer's cost.",
        "We ship worldwide.",
    ):
        assert ships_to_us_from_text(text, "") is True, text


def test_the_pronoun_us_is_not_the_country():
    # "back to us" and "contact us" are everywhere in terms text.
    assert ships_to_us_from_text("Items must be shipped back to us within 7 days.", "") is None
    assert ships_to_us_from_text("Contact us to arrange shipping.", "") is None


def test_the_terms_blob_counts_too():
    assert ships_to_us_from_text("", "Pickup Tuesday. Shipping within Canada only.") is False


def test_a_contradiction_is_left_undecided():
    assert ships_to_us_from_text(
        "We ship to the US and Canada.", "No international shipping.") is None


def test_silence_is_unknown_not_no():
    assert ships_to_us_from_text("", "") is None
    assert ships_to_us_from_text("Shipping is available. Fees apply.", "") is None


def test_resolve_never_flags_a_us_house():
    assert resolve("TX", "Canada only", "", {"ships": False, "ships_to_us": False}) is None


def test_resolve_prefers_the_text_then_the_ai_then_pickup_only():
    assert resolve("ON", "Canada only", "", {"ships_to_us": True}) is False
    assert resolve("ON", "Shipping available.", "", {"ships_to_us": True}) is True
    assert resolve("ON", "Shipping available.", "", {"ships_to_us": None, "ships": False}) is False
    assert resolve("ON", "Shipping available.", "", {"ships_to_us": None, "ships": True}) is None
    assert resolve("ON", "", "", None) is None
