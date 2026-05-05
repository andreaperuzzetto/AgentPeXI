"""Tests for _ResearchValidationMixin static/instance methods."""
from __future__ import annotations

import pytest

from apps.backend.agents._research.validation_mixin import _ResearchValidationMixin


class _BareResearch(_ResearchValidationMixin):
    pass


# ---------------------------------------------------------------------------
# _try_parse_json
# ---------------------------------------------------------------------------

def test_try_parse_json_valid_string():
    result = _ResearchValidationMixin._try_parse_json('{"key": "value"}')
    assert result == {"key": "value"}


def test_try_parse_json_strips_json_fence():
    text = '```json\n{"key": "value"}\n```'
    result = _ResearchValidationMixin._try_parse_json(text)
    assert result == {"key": "value"}


def test_try_parse_json_strips_plain_fence():
    text = '```\n{"key": "value"}\n```'
    result = _ResearchValidationMixin._try_parse_json(text)
    assert result == {"key": "value"}


def test_try_parse_json_invalid_text_returns_none():
    result = _ResearchValidationMixin._try_parse_json("not json at all")
    assert result is None


def test_try_parse_json_empty_string_returns_none():
    result = _ResearchValidationMixin._try_parse_json("")
    assert result is None


def test_try_parse_json_nested_object():
    text = '{"niches": [{"name": "Test", "viable": true}]}'
    result = _ResearchValidationMixin._try_parse_json(text)
    assert result["niches"][0]["name"] == "Test"


# ---------------------------------------------------------------------------
# _validate_and_fix_tags
# ---------------------------------------------------------------------------

def _make_niche(tags, keywords=None):
    return {
        "name": "Test Niche",
        "etsy_tags_13": list(tags),
        "keywords": list(keywords) if keywords else [],
    }


def test_validate_tags_exactly_13_no_change():
    tags = [f"tag{i}" for i in range(13)]
    niche = _make_niche(tags)
    result = _ResearchValidationMixin._validate_and_fix_tags(niche)
    assert len(result["etsy_tags_13"]) == 13


def test_validate_tags_15_truncated_to_13():
    tags = [f"tag{i}" for i in range(15)]
    niche = _make_niche(tags)
    result = _ResearchValidationMixin._validate_and_fix_tags(niche)
    assert len(result["etsy_tags_13"]) <= 13


def test_validate_tags_8_padded_from_keywords():
    tags = [f"tag{i}" for i in range(8)]
    keywords = [f"keyword{i}" for i in range(10)]
    niche = _make_niche(tags, keywords)
    result = _ResearchValidationMixin._validate_and_fix_tags(niche)
    assert len(result["etsy_tags_13"]) >= 8


def test_validate_tags_special_chars_sanitized():
    niche = _make_niche(["hello!", "world@", "foo#bar"] + [f"tag{i}" for i in range(10)])
    result = _ResearchValidationMixin._validate_and_fix_tags(niche)
    for tag in result["etsy_tags_13"]:
        import re
        assert re.match(r'^[a-z0-9\s\-]*$', tag), f"Tag not sanitized: {tag!r}"


def test_validate_tags_long_tag_truncated():
    long_tag = "a" * 25  # 25 chars, no spaces → truncated to 20
    niche = _make_niche([long_tag] + [f"tag{i}" for i in range(12)])
    result = _ResearchValidationMixin._validate_and_fix_tags(niche)
    for tag in result["etsy_tags_13"]:
        assert len(tag) <= 20


def test_validate_tags_deduped():
    tags = ["duplicate"] * 5 + [f"tag{i}" for i in range(8)]
    niche = _make_niche(tags)
    result = _ResearchValidationMixin._validate_and_fix_tags(niche)
    assert len(result["etsy_tags_13"]) == len(set(result["etsy_tags_13"]))


def test_validate_tags_too_many_single_word_adds_warning():
    # 9 single-word tags → warning
    tags = [f"word{i}" for i in range(9)] + ["multi word tag", "another phrase", "long tail tag", "four word phrase"]
    niche = _make_niche(tags)
    result = _ResearchValidationMixin._validate_and_fix_tags(niche)
    assert "troppi tag singola parola" in result.get("notes", "")


def test_validate_tags_few_longtail_adds_warning():
    # Only 2 multi-word tags → warning
    tags = [f"word{i}" for i in range(11)] + ["multi word one", "multi word two"]
    niche = _make_niche(tags)
    result = _ResearchValidationMixin._validate_and_fix_tags(niche)
    assert "pochi tag long-tail" in result.get("notes", "")


# ---------------------------------------------------------------------------
# _apply_viability_gate
# ---------------------------------------------------------------------------

def _viable_niche(name="Good Niche", sweet_spot=9.99):
    return {
        "name": name,
        "pricing": {"conversion_sweet_spot_usd": sweet_spot},
        "demand": {"level": "medium", "trend": "stable"},
        "competition": {"level": "medium"},
        "entry_difficulty": "medium",
        "etsy_tags_13": ["tag1", "tag2"],
        "keywords": ["kw1"],
    }


def test_viability_gate_all_viable_returns_result():
    result = {"niches": [_viable_niche("A"), _viable_niche("B")]}
    filtered, discarded = _ResearchValidationMixin._apply_viability_gate(result)
    assert filtered is not None
    assert discarded == []


def test_viability_gate_llm_marked_not_viable_added_to_discarded():
    niche = _viable_niche("Bad Niche")
    niche["viable"] = False
    niche["viability_reason"] = "Not profitable"
    result = {"niches": [niche, _viable_niche("Good Niche")]}
    filtered, discarded = _ResearchValidationMixin._apply_viability_gate(result)
    assert filtered is not None
    assert any(d["name"] == "Bad Niche" for d in discarded)


def test_viability_gate_low_price_discarded():
    niche = _viable_niche("Cheap Niche", sweet_spot=1.99)
    result = {"niches": [niche]}
    filtered, discarded = _ResearchValidationMixin._apply_viability_gate(result)
    assert filtered is None  # all discarded
    assert len(discarded) == 1
    assert discarded[0]["name"] == "Cheap Niche"


def test_viability_gate_high_difficulty_low_demand_discarded():
    niche = _viable_niche("Hard Low Niche")
    niche["entry_difficulty"] = "high"
    niche["demand"]["level"] = "low"
    result = {"niches": [niche]}
    filtered, discarded = _ResearchValidationMixin._apply_viability_gate(result)
    assert filtered is None
    assert discarded[0]["name"] == "Hard Low Niche"


def test_viability_gate_declining_high_competition_discarded():
    niche = _viable_niche("Declining Niche")
    niche["demand"]["trend"] = "declining"
    niche["competition"]["level"] = "high"
    result = {"niches": [niche]}
    filtered, discarded = _ResearchValidationMixin._apply_viability_gate(result)
    assert filtered is None
    assert discarded[0]["name"] == "Declining Niche"


def test_viability_gate_no_etsy_tags_discarded():
    niche = _viable_niche("No Tags Niche")
    niche["etsy_tags_13"] = []
    result = {"niches": [niche]}
    filtered, discarded = _ResearchValidationMixin._apply_viability_gate(result)
    assert filtered is None
    assert discarded[0]["name"] == "No Tags Niche"


def test_viability_gate_all_discarded_returns_none():
    n1 = _viable_niche("Niche1", sweet_spot=0.99)
    n2 = _viable_niche("Niche2", sweet_spot=1.50)
    result = {"niches": [n1, n2]}
    filtered, discarded = _ResearchValidationMixin._apply_viability_gate(result)
    assert filtered is None
    assert len(discarded) == 2


def test_viability_gate_mixed_one_viable_one_not():
    good = _viable_niche("Good Niche")
    bad = _viable_niche("Bad Niche", sweet_spot=1.00)
    result = {"niches": [good, bad]}
    filtered, discarded = _ResearchValidationMixin._apply_viability_gate(result)
    assert filtered is not None
    assert any(d["name"] == "Bad Niche" for d in discarded)
    assert not any(d["name"] == "Good Niche" for d in discarded)


# ---------------------------------------------------------------------------
# _enforce_failure_constraints
# ---------------------------------------------------------------------------

def _make_output_with_niche(name="Test Niche", pricing_sweet_spot=9.99):
    return {
        "niches": [
            {
                "name": name,
                "pricing": {"conversion_sweet_spot_usd": pricing_sweet_spot},
                "viable": True,
            }
        ]
    }


def test_enforce_failure_no_match_keeps_niche_viable():
    agent = _BareResearch()
    output = _make_output_with_niche("Watercolor Prints")
    failure_context = [
        {
            "metadata": {
                "niche": "bullet journal completely different",
                "failure_type": "no_views",
                "avoid_in_future": "generic tags",
            },
            "document": "...",
        }
    ]
    result, violations = agent._enforce_failure_constraints(output, failure_context)
    assert violations == []
    assert result["niches"][0]["viable"] is True


    agent = _BareResearch()
    output = _make_output_with_niche()
    result, violations = agent._enforce_failure_constraints(output, [])
    assert violations == []
    assert result["niches"][0]["viable"] is True


def test_enforce_failure_fatal_marks_not_viable():
    agent = _BareResearch()
    output = _make_output_with_niche("Bullet Journal")
    failure_context = [
        {
            "metadata": {
                "niche": "bullet journal",
                "failure_type": "no_views_no_sales",
                "avoid_in_future": "generic minimalist style",
            },
            "document": "...",
        }
    ]
    result, violations = agent._enforce_failure_constraints(output, failure_context)
    assert result["niches"][0]["viable"] is False
    assert len(violations) == 1
    assert "no_views_no_sales" in violations[0]


def test_enforce_failure_no_views_modifies_tag_strategy():
    agent = _BareResearch()
    output = _make_output_with_niche("Bullet Journal")
    output["niches"][0]["tag_strategy"] = "original strategy"
    failure_context = [
        {
            "metadata": {
                "niche": "bullet journal",
                "failure_type": "no_views",
                "avoid_in_future": "too generic tags",
            },
            "document": "...",
        }
    ]
    result, violations = agent._enforce_failure_constraints(output, failure_context)
    assert "FAILURE-ADJUSTED" in result["niches"][0].get("tag_strategy", "")
    assert len(violations) == 1


def test_enforce_failure_no_conversion_modifies_price_reasoning():
    agent = _BareResearch()
    output = _make_output_with_niche("Bullet Journal")
    failure_context = [
        {
            "metadata": {
                "niche": "bullet journal",
                "failure_type": "no_conversion",
                "avoid_in_future": "price above 12",
            },
            "document": "...",
        }
    ]
    result, violations = agent._enforce_failure_constraints(output, failure_context)
    assert "FAILURE-ADJUSTED" in result["niches"][0]["pricing"].get("price_reasoning", "")
    assert len(violations) == 1


def test_enforce_failure_substring_match_works():
    agent = _BareResearch()
    # Niche name "journal" should match failure for "bullet journal"
    output = _make_output_with_niche("journal")
    failure_context = [
        {
            "metadata": {
                "niche": "bullet journal planner",
                "failure_type": "no_views_no_sales",
                "avoid_in_future": "broad niche",
            },
            "document": "...",
        }
    ]
    result, violations = agent._enforce_failure_constraints(output, failure_context)
    assert len(violations) >= 1


# ---------------------------------------------------------------------------
# PA-10: output schema contract tests (audience_target + expansion_potential)
# ---------------------------------------------------------------------------

from apps.backend.agents._research.scoring_mixin import _ResearchScoringMixin  # noqa: E402


def test_confidence_capped_when_audience_target_absent():
    """Contract (schema v2): audience_target absent → confidence score capped at 0.40."""
    niche_sample = {
        # Full quality signals that would push score well above 0.40 without the cap
        "etsy_tags_13": [f"tag{i}" for i in range(13)],
        "selling_signals": {
            "thumbnail_style": "flat lay",
            "conversion_triggers": ["urgency"],
            "bundle_vs_single": "single",
            "first_listing_recommendation": "planner A4",
        },
        "pricing": {"conversion_sweet_spot_usd": 7.99, "launch_price_usd": 5.99},
        "demand": {"peak_months": ["January"], "publish_timing_advice": "Publish now"},
        # audience_target deliberately absent to trigger the cap
    }
    # _calculate_confidence expects output = {"niches": [niche_dict, ...]}
    output = {"niches": [niche_sample]}
    data_sources = {
        "pricing": "etsy_api",
        "trend": "google_trends",
        "keywords": "erank_content",
        "competitors": "etsy_api",
    }
    score, missing = _ResearchScoringMixin._calculate_confidence(data_sources, output)
    assert score <= 0.40, f"Expected score ≤ 0.40 when audience_target absent, got {score}"
    assert any("audience_target" in m for m in missing), (
        "Expected 'audience_target' warning in missing list"
    )


def test_niche_item_expansion_potential_is_string_enum():
    """Contract: expansion_potential must be Literal['high','medium','low'] | None.
    The LLM research prompt emits string values, not integers.
    """
    from pydantic import ValidationError
    from apps.backend.api.routers.etsy import NicheItemResponse

    _BASE = dict(
        niche="planner",
        product_type="printable_pdf",
        performance_score=0.5,
        confidence_level="medium",
        avg_ctr=None,
        total_orders=None,
        total_listings=None,
        total_revenue_eur=None,
        last_updated_at=None,
        entry_score=None,
        tier=None,
        avg_price_eur=None,
        google_trend_score=None,
    )
    # Valid: None (field is optional)
    assert NicheItemResponse(**_BASE, expansion_potential=None).expansion_potential is None
    # Valid: all three literal string values
    for val in ("high", "medium", "low"):
        assert NicheItemResponse(**_BASE, expansion_potential=val).expansion_potential == val
    # Invalid: integer must raise ValidationError (LLM never emits ints)
    with pytest.raises(ValidationError):
        NicheItemResponse(**_BASE, expansion_potential=42)
    # Invalid: unrecognised string must raise ValidationError
    with pytest.raises(ValidationError):
        NicheItemResponse(**_BASE, expansion_potential="very_high")
