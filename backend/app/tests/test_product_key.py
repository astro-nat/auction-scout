"""Grouping a product across its individual units. Pure.

Hiding was the most-used action in the app and 57 of 68 came in bursts, so
"hide everything like this" needs to know when two listings are the same
product. A liquidation house lists one product many times and fences the
unit identifier off with punctuation, because a human has to read past it
too.

Every title here is a real one from the inventory.
"""

from app.services.twins import product_key, title_key


def test_a_fenced_asset_tag_is_dropped():
    keys = {product_key(t) for t in (
        "HARD DRIVE REMOVED ~ LAPTOP ~ FB-9-120 (R17D)",
        "HARD DRIVE REMOVED ~ LAPTOP ~ FB-9-111 (R17D)",
        "HARD DRIVE REMOVED ~ LAPTOP ~ FB-19-65 (R13B)",
    )}
    assert keys == {"hard drive removed laptop"}


def test_a_digit_counting_rule_would_have_missed_those():
    """FB-9-120 has no run of four digits anywhere in it, which is why the
    cut is made at the fence and not by counting digits."""
    import re
    assert not re.search(r"\d{4,}", "FB-9-120 (R17D)")


def test_a_two_word_product_still_groups():
    assert (product_key("Drieaz Humidifier ~ IA-25158")
            == product_key("Drieaz Humidifier ~ IA-25157")
            == "drieaz humidifier")
    # The pricing key refuses it: two words is under MIN_WORDS.
    assert title_key("Drieaz Humidifier ~ IA-25158") != title_key(
        "Drieaz Humidifier ~ IA-25157")


def test_a_parenthesised_quantity_and_a_bare_serial_both_go():
    assert (product_key("Used Dell Precision 7750 Laptop (Qty. 1) FXA 92561")
            == product_key("Used Dell Precision 7750 Laptop (Qty. 1) FXA 92608")
            == "used dell precision 7750 laptop")


def test_a_model_number_mid_title_is_never_touched():
    assert (product_key("Used Dell Precision 7750 Laptop (Qty. 1) FXA 92561")
            != product_key("Used Dell Precision 7760 Laptop (Qty. 1) FXA 93228"))


def test_a_trailing_spec_that_is_not_an_identifier_stays():
    """The guard that protects this: a fenced segment is only dropped when it
    contains a digit, and "13 Pro Max 256GB" has no fence at all."""
    assert product_key("iPhone 13 Pro Max 256GB") != product_key(
        "iPhone 14 Pro Max 256GB")
    assert "13" in product_key("iPhone 13 Pro Max 256GB")


def test_a_variant_in_parentheses_is_a_different_product():
    assert product_key("Pyrex Bowl (Large)") != product_key("Pyrex Bowl (Small)")


def test_a_bare_category_does_not_group():
    for t in ("Pyrex", "", None, "Lot", "WORKSTATION * JN-17-1"):
        assert product_key(t) is None, t


def test_identical_titles_group_without_any_stripping():
    assert (product_key("$291.00 Remanufactured A/C Compressor")
            == product_key("$291.00 Remanufactured A/C Compressor"))
