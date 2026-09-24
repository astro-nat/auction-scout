"""Same title, one pricing: the grouping key and the claim choice. Pure."""

from app.services import twins
from app.services.jobs import pick_claimable


def test_identical_titles_share_a_key_whatever_the_punctuation():
    a = twins.title_key("WZU Recordable Personal Safety Alarm - FCC Certified")
    b = twins.title_key("wzu recordable personal safety alarm, FCC certified")
    assert a is not None and a == b


def test_generic_titles_do_not_group():
    # Nine "Pyrex" lots in one sale were nine different dishes.
    assert twins.title_key("Pyrex") is None
    assert twins.title_key("Pyrex dish") is None
    assert twins.title_key("Lot of items") is None
    assert twins.title_key("Vintage Pyrex Butterprint Bowl") is not None


def test_different_titles_do_not_group():
    assert (twins.title_key("Hiccapop Convertible Crib Bed Rail, White")
            != twins.title_key("Hiccapop Convertible Crib Bed Rail, Grey"))


ALARM = "WZU Recordable Personal Safety Alarm"
POUCH = "Hiearcool Waterproof Phone Pouch"


def test_one_of_each_title_per_claim():
    got = pick_claimable([(1, ALARM), (2, ALARM), (3, POUCH), (4, ALARM)], set(), 5)
    assert got == [1, 3]


def test_a_title_already_being_worked_waits():
    got = pick_claimable([(1, ALARM), (2, POUCH)], {twins.title_key(ALARM)}, 5)
    assert got == [2]


def test_generic_titles_are_always_claimable_and_the_limit_holds():
    got = pick_claimable([(1, "Pyrex"), (2, "Pyrex"), (3, "Pyrex")], set(), 2)
    assert got == [1, 2]
