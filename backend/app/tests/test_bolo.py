"""BOLO brand matching — the accumulated regression cases from live audits.

The matcher loads all 20+ JSON files and compiles thousands of regexes, so
one session-scoped instance serves every test.
"""

import pytest

from app.services.bolo import BoloMatcher, DEFAULT_BOLO_PATHS


@pytest.fixture(scope="session")
def matcher():
    return BoloMatcher(DEFAULT_BOLO_PATHS)


SHOULD_MATCH = [
    ("Intel Core i7-10700K CPU Processor LGA1200", "Intel CPUs"),
    ("Noctua NH-D15 chromax.black CPU Cooler", "Noctua"),
    ("NZXT Kraken Z73 360mm AIO Liquid Cooler", "NZXT Kraken coolers"),
    ("Cooler Master Hyper 212 Black Edition", "Cooler Master coolers"),
    ("Ed Hardy Rhinestone Tiger Tee Size L", "Y2K fashion"),
    ("JNCO Wide Leg Jeans 90s", "Y2K fashion"),
    ("Lot of Vintage Graphic Tees Single Stitch", "Graphic tees"),
    ("Torrid Floral Dress Size 2", "Torrid"),
    ("Eloquii Wrap Dress 18", "Plus-size"),
    ("Moncler Maya Puffer Jacket Sz 4", "Luxury outerwear"),
    ("Zimmermann Silk Floral Midi Dress Sz 1", "Contemporary designer"),
    ("Lululemon Align Leggings Size 6", "Lululemon"),
]

# collision guards: these must NOT hit the bracketed entries
SHOULD_NOT_MATCH_THESE = [
    "Off White Ceramic Table Lamp",
    "Canada Goose Hunting Decoys Lot of 6",
    "Zimmermann Upright Piano",
    "Golden Goose Egg Decorative Ornament",
    "Lian Li O11 Dynamic Evo Mid Tower Case",
    "Fractal Design Meshify 2 ATX Case",
    "Cooler Master NR200P Mini ITX Case",
]
_GUARDED_BRANDS = ("Y2K", "Graphic tees", "Plus-size", "Luxury outerwear",
                   "Contemporary designer", "Cooler Master", "NZXT",
                   "Mini-ITX motherboards")


@pytest.mark.parametrize("title,expected_prefix", SHOULD_MATCH)
def test_matches(matcher, title, expected_prefix):
    r = matcher.match(title)
    assert r is not None, f"no match for {title!r}"
    assert expected_prefix.lower() in r["brand"].lower()


@pytest.mark.parametrize("title", SHOULD_NOT_MATCH_THESE)
def test_collision_guards(matcher, title):
    r = matcher.match(title)
    brand = (r or {}).get("brand", "")
    assert not any(g in brand for g in _GUARDED_BRANDS), \
        f"{title!r} wrongly matched {brand!r}"
