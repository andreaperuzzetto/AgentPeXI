"""Tests for _AnalyticsFailureMixin._parse_analysis_json static method."""
from __future__ import annotations

import json

import pytest

from apps.backend.agents._analytics.failure_mixin import _AnalyticsFailureMixin


# ---------------------------------------------------------------------------
# _parse_analysis_json
# ---------------------------------------------------------------------------

def _valid_json_str(cause="bad tags", recs=None, avoid="avoid generic"):
    if recs is None:
        recs = ["fix tags", "use long-tail keywords", "change title"]
    return json.dumps({
        "cause": cause,
        "recommendations": recs,
        "avoid_in_future": avoid,
    })


def test_parse_analysis_json_valid_string():
    text = _valid_json_str()
    result = _AnalyticsFailureMixin._parse_analysis_json(text)
    assert result is not None
    assert result["cause"] == "bad tags"
    assert isinstance(result["recommendations"], list)
    assert result["avoid_in_future"] == "avoid generic"


def test_parse_analysis_json_strips_markdown_fence():
    payload = _valid_json_str("missing keywords")
    text = f"```json\n{payload}\n```"
    result = _AnalyticsFailureMixin._parse_analysis_json(text)
    assert result is not None
    assert result["cause"] == "missing keywords"


def test_parse_analysis_json_strips_plain_fence():
    payload = _valid_json_str("price too high")
    text = f"```\n{payload}\n```"
    result = _AnalyticsFailureMixin._parse_analysis_json(text)
    assert result is not None
    assert result["cause"] == "price too high"


def test_parse_analysis_json_invalid_json_returns_none():
    result = _AnalyticsFailureMixin._parse_analysis_json("this is not json")
    assert result is None


def test_parse_analysis_json_empty_string_returns_none():
    result = _AnalyticsFailureMixin._parse_analysis_json("")
    assert result is None


def test_parse_analysis_json_missing_required_fields_returns_none():
    # Missing avoid_in_future
    text = json.dumps({"cause": "x", "recommendations": ["a"]})
    result = _AnalyticsFailureMixin._parse_analysis_json(text)
    assert result is None


def test_parse_analysis_json_missing_cause_returns_none():
    text = json.dumps({"recommendations": ["a"], "avoid_in_future": "b"})
    result = _AnalyticsFailureMixin._parse_analysis_json(text)
    assert result is None


def test_parse_analysis_json_all_required_fields_present():
    text = _valid_json_str("cause here", ["rec1", "rec2", "rec3"], "avoid this")
    result = _AnalyticsFailureMixin._parse_analysis_json(text)
    assert "cause" in result
    assert "recommendations" in result
    assert "avoid_in_future" in result


def test_parse_analysis_json_extra_fields_allowed():
    data = {
        "cause": "generic tags",
        "recommendations": ["fix1"],
        "avoid_in_future": "avoid broad",
        "extra_field": "some extra data",
    }
    result = _AnalyticsFailureMixin._parse_analysis_json(json.dumps(data))
    assert result is not None
    assert result["extra_field"] == "some extra data"


def test_parse_analysis_json_recommendations_is_list():
    text = _valid_json_str(recs=["action 1", "action 2", "action 3"])
    result = _AnalyticsFailureMixin._parse_analysis_json(text)
    assert isinstance(result["recommendations"], list)
    assert len(result["recommendations"]) == 3
