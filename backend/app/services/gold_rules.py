"""Which rules make a gold mine, as the user sets them (Filters panel).

The user's call (2026-11-10): "I want to be able to select features that
makes a gold mine a gold mine". Four rules are switchable, two with an
amount; everything else the grader checks (red flags, identity, pickup
radius, vehicles, the audit) stays fixed. All on by default, at the values
they had before they were switchable. Kept as one JSON setting, so a save is
one write and a read is one (cached) lookup per lot.
"""

from __future__ import annotations

import json
import os

SETTINGS_KEY = "gold_rules"

DEFAULTS = {
    # Must fit the poly mailer everything ships in (services/mailer).
    "mailer": True,
    # Must weigh no more than max_weight_lb.
    "weight": True,
    "max_weight_lb": float(os.environ.get("MAX_WEIGHT_LB", "10")),
    # The max bid stops where profit would drop under min_profit_usd.
    "min_profit": True,
    "min_profit_usd": float(os.environ.get("MIN_GOLD_PROFIT", "10")),
    # At least 2 agreeing comps (a printed retail price, an audit or melt
    # value counts as the second source).
    "two_comps": True,
}
_SWITCHES = ("mailer", "weight", "min_profit", "two_comps")
_AMOUNTS = {"max_weight_lb": (0.1, 1000.0), "min_profit_usd": (0.0, 10000.0)}


def current() -> dict:
    """The rules in force: saved values over the defaults."""
    from . import settings
    try:
        saved = json.loads(settings.get(SETTINGS_KEY) or "{}")
    except Exception:  # noqa: BLE001 - settings table missing, or bad JSON
        saved = {}
    rules = dict(DEFAULTS)
    rules.update({k: v for k, v in saved.items() if k in DEFAULTS})
    return rules


def merge(patch: dict) -> dict:
    """The current rules with a partial change applied, validated.
    Raises ValueError naming the bad field."""
    rules = current()
    for key, value in (patch or {}).items():
        if key in _SWITCHES:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
            rules[key] = value
        elif key in _AMOUNTS:
            lo, hi = _AMOUNTS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise ValueError(f"{key} must be a number from {lo:g} to {hi:g}")
            rules[key] = float(value)
        else:
            raise ValueError(f"unknown gold-mine rule: {key}")
    return rules


def save(rules: dict) -> None:
    from . import settings
    settings.set(SETTINGS_KEY, json.dumps({k: rules[k] for k in DEFAULTS}))
