"""Musical instruments on the BOLO list. Pure.

Restored 2026-10-01 and widened past the shippable gear: guitars, amps,
synths, horns and drums are worth driving for. Every big-instrument
pattern names an instrument or a model, because the bare brands collide -
"fender" is a car part, "moog" a suspension brand, "gibson" an appliance
maker, "martin" and "taylor" people, "yamaha" outboard motors.
"""

import pytest

from app.services.bolo import BoloMatcher

m = BoloMatcher()


def _brand(title):
    return (m.match(title, "") or {}).get("brand")


@pytest.mark.parametrize("title,brand", [
    ("Fender Stratocaster Electric Guitar Sunburst", "Guitars and basses (name brands)"),
    ("Gibson Les Paul Studio w/ Hard Case", "Guitars and basses (name brands)"),
    ("Martin D-28 Acoustic Guitar", "Guitars and basses (name brands)"),
    ("Epiphone Les Paul Special II", "Guitars and basses (name brands)"),
    ("Marshall JCM800 2203 Head", "Guitar and bass amplifiers"),
    ("Fender Blues Junior Tube Amp", "Guitar and bass amplifiers"),
    ("Korg Minilogue Polyphonic Analog Synthesizer", "Synthesizers and keyboards"),
    ("Selmer Mark VI Tenor Saxophone", "Band instruments (brass and woodwind)"),
    ("Zildjian 20in Ride Cymbal", "Drums and cymbals"),
    ("Ludwig Supraphonic Snare Drum", "Drums and cymbals"),
    ("Student Violin 4/4 with Case and Bow", "Musical instruments (general)"),
    ("Boss DS-1 Distortion Pedal", "Effects pedals (guitar/bass)"),
    ("Hohner Marine Band Harmonica in C", "Premium harmonicas"),
])
def test_instruments_are_flagged(title, brand):
    assert _brand(title) == brand


@pytest.mark.parametrize("title", [
    "2015 Ford F-150 Front Fender Driver Side",
    "MOOG K80673 Front Lower Control Arm",
    "Gibson Everyday Dinnerware Set 16 pc",
    "Set of 6 Crystal Champagne Flutes",
    "Yamaha 9.9 HP Outboard Motor",
    "Taylor Made Golf Driver",
    "Angel Trumpet Plant in Pot",
    "Accordion File Folder Organizer",
    "Guitar Hero Controller for Xbox 360",
])
def test_look_alikes_are_not(title):
    assert _brand(title) not in {
        "Guitars and basses (name brands)", "Guitar and bass amplifiers",
        "Synthesizers and keyboards", "Band instruments (brass and woodwind)",
        "Drums and cymbals", "Musical instruments (general)",
    }
