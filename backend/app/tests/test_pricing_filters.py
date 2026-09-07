"""Comp-relevance filters — each one exists because a real wrong price shipped."""

from app.services.pricing import (
    _audience_match, _model_match, _quantity_match, _relevant, _iqr_filter,
)


class TestAudienceMatch:
    def test_kids_query_rejects_adult_comps(self):
        assert not _audience_match("Nike Kids Tech Fleece Hoodie Youth M",
                                   "Nike Tech Fleece Hoodie Mens Large Grey")

    def test_adult_query_rejects_kids_comps(self):
        assert not _audience_match("Nike Mens Tech Fleece Hoodie L",
                                   "Nike Tech Fleece Kids Hoodie")

    def test_silent_comps_still_count(self):
        assert _audience_match("Nike Kids Hoodie Youth M", "Nike Tech Fleece Hoodie Grey")

    def test_no_audience_in_query_matches_anything(self):
        assert _audience_match("Nike Tech Fleece Hoodie", "Nike Hoodie Mens L")

    def test_known_collisions_survive(self):
        # "jr" is deliberately not an audience token (sports cards),
        # "baby" in franchise names must not nuke toy comps
        assert _audience_match("Ken Griffey Jr Rookie Card", "Ken Griffey Jr 1989 Upper Deck")
        assert _audience_match("Baby Yoda Grogu Plush", "Star Wars Grogu Plush Toy")


class TestModelMatch:
    def test_model_code_must_appear_in_comp(self):
        # the Holy Stone drone priced off the wrong model in production
        assert not _model_match("Holy Stone U818A Drone", "Holy Stone F181W Drone RC")
        assert _model_match("Holy Stone U818A Drone", "Holy Stone U818A HD Camera Drone")

    def test_no_model_code_matches_anything(self):
        assert _model_match("Vintage Brass Candlesticks", "Pair Brass Candlesticks")


class TestQuantityMatch:
    def test_multipack_comp_rejected_for_single_item(self):
        # the Sterilite hamper priced off a "3 Pack" listing in production
        assert not _quantity_match("Sterilite Wheeled Laundry Hamper",
                                   "3 Pack Sterilite Wheeled Hamper")

    def test_multipack_query_matches_multipack_comp(self):
        assert _quantity_match("2 Pack Poster Frames 22x34", "Poster Frames 2 Pack Black")


class TestIqrFilter:
    def test_outliers_trimmed(self):
        prices = [10, 11, 12, 11, 10, 12, 500]
        assert 500 not in _iqr_filter(prices)

    def test_small_samples_untouched(self):
        assert _iqr_filter([5, 900]) == [5, 900]


class TestRelevant:
    def test_unrelated_comp_rejected(self):
        assert not _relevant("Waterpik Complete Care Sonic Toothbrush",
                             "Vintage Cast Iron Skillet Lodge")

    def test_close_match_accepted(self):
        assert _relevant("Waterpik Complete Care Sonic 5.0",
                         "Waterpik Complete Care 5.0 Sonic Toothbrush NIB")
