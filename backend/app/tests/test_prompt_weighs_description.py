"""The prompts must let a written disclosure beat a tidy photo. Pure.

A Dell Precision 7750 was graded "normal wear and tear" while its own
listing said "**Does not have Hard Drive. **No power cord." The
description had been fetched and passed in all along - both prompts told
the model, in so many words, to "trust the photo over it".
"""

from app.workers.enrich import GOLD_CHECK_PROMPT, INSPECT_PROMPT, VISION_PROMPT

DELL = "**Does not have Hard Drive. **No power cord."


def _vision(description=DELL, title="Used Dell Precision 7750 Laptop"):
    return VISION_PROMPT.format(title=title, description=description, photo_note="")


def test_neither_prompt_tells_the_model_to_trust_the_photo_over_the_words():
    for text in (_vision(), INSPECT_PROMPT):
        assert "trust the photo over" not in text


def test_the_description_reaches_the_model_intact():
    assert DELL in _vision()


def test_the_words_lead_on_condition_and_the_photo_on_identity():
    p = _vision()
    assert "WHAT IT IS: the photo leads" in p
    assert "WHAT CONDITION IT IS IN: the words lead" in p
    # The specific disclosures that a photo cannot show.
    for phrase in ("No hard drive", "missing parts", "untested", "as-is"):
        assert phrase.lower() in p.lower(), phrase
    assert "A tidy photo never overrides them" in p


def test_the_itemized_pass_is_told_the_same_thing():
    assert "WHAT CONDITION IT IS IN: the words lead" in INSPECT_PROMPT


def test_the_audit_is_told_to_read_the_description():
    assert "Read the description before you answer" in GOLD_CHECK_PROMPT
    assert "missing parts" in GOLD_CHECK_PROMPT


def test_an_empty_description_still_formats():
    assert "(none)" in _vision(description="(none)")
