"""HiBid response parsing — time-left, JSON-from-model, sane-estimate guard."""

from datetime import datetime

import pytest

from app.services.hibid import _closes_at_from_time_left
from app.workers.enrich import _parse_json_response, _sane_estimate


class TestTimeLeft:
    def test_full_form(self):
        r = _closes_at_from_time_left("2d  6h  30m ")
        delta = (r - datetime.now()).total_seconds()
        assert abs(delta - (2 * 86400 + 6 * 3600 + 30 * 60)) < 5

    def test_minutes_only(self):
        r = _closes_at_from_time_left("45m")
        assert 0 < (r - datetime.now()).total_seconds() <= 45 * 60 + 5

    @pytest.mark.parametrize("s", ["Bidding Closed", "", None])
    def test_unparseable_is_none(self, s):
        assert _closes_at_from_time_left(s) is None


class TestParseJsonResponse:
    def test_plain_json(self):
        assert _parse_json_response('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert _parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}

    def test_commentary_around_json(self):
        assert _parse_json_response('Sure! {"a": 1} hope that helps') == {"a": 1}

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            _parse_json_response("no json here")


class TestSaneEstimate:
    def test_valid_number(self):
        assert _sane_estimate(25) == 25.0

    @pytest.mark.parametrize("v", [None, "25", True, 0, -5, 6000])
    def test_junk_rejected(self, v):
        assert _sane_estimate(v) is None
