"""Tests for _DesignValidationMixin._validate_and_normalize_input and _extract_research_context."""
from __future__ import annotations

import pytest

from apps.backend.agents._design.validation_mixin import _DesignValidationMixin
from apps.backend.agents._design.presets import AVAILABLE_TEMPLATES


class _Bare(_DesignValidationMixin):
    pass


@pytest.fixture
def mixin():
    return _Bare()


# ---------------------------------------------------------------------------
# _validate_and_normalize_input — missing fields
# ---------------------------------------------------------------------------

async def test_validate_missing_niche_returns_error(mixin):
    result, err = await mixin._validate_and_normalize_input({"product_type": "printable_pdf"})
    assert result is None
    assert "niche" in err


async def test_validate_missing_product_type_returns_error(mixin):
    result, err = await mixin._validate_and_normalize_input({"niche": "planner"})
    assert result is None
    assert "product_type" in err


async def test_validate_empty_niche_returns_error(mixin):
    result, err = await mixin._validate_and_normalize_input({"niche": "", "product_type": "printable_pdf"})
    assert result is None
    assert err is not None


async def test_validate_invalid_product_type_returns_error(mixin):
    result, err = await mixin._validate_and_normalize_input({"niche": "planner", "product_type": "video_course"})
    assert result is None
    assert "video_course" in err


# ---------------------------------------------------------------------------
# _validate_and_normalize_input — valid input normalization
# ---------------------------------------------------------------------------

async def test_validate_minimal_valid_input_succeeds(mixin):
    valid_type = next(iter(AVAILABLE_TEMPLATES.keys()))
    result, err = await mixin._validate_and_normalize_input({"niche": "planner", "product_type": valid_type})
    assert err is None
    assert result is not None
    assert result["niche"] == "planner"


async def test_validate_default_num_variants_is_2(mixin):
    valid_type = next(iter(AVAILABLE_TEMPLATES.keys()))
    result, _ = await mixin._validate_and_normalize_input({"niche": "planner", "product_type": valid_type})
    assert result["num_variants"] == 2


async def test_validate_num_variants_clamped_to_5(mixin):
    valid_type = next(iter(AVAILABLE_TEMPLATES.keys()))
    result, _ = await mixin._validate_and_normalize_input(
        {"niche": "planner", "product_type": valid_type, "num_variants": 99}
    )
    assert result["num_variants"] == 5


async def test_validate_num_variants_invalid_type_defaults_to_2(mixin):
    valid_type = next(iter(AVAILABLE_TEMPLATES.keys()))
    result, _ = await mixin._validate_and_normalize_input(
        {"niche": "planner", "product_type": valid_type, "num_variants": "two"}
    )
    assert result["num_variants"] == 2


async def test_validate_num_variants_zero_defaults_to_2(mixin):
    valid_type = next(iter(AVAILABLE_TEMPLATES.keys()))
    result, _ = await mixin._validate_and_normalize_input(
        {"niche": "planner", "product_type": valid_type, "num_variants": 0}
    )
    assert result["num_variants"] == 2


async def test_validate_default_color_schemes(mixin):
    valid_type = next(iter(AVAILABLE_TEMPLATES.keys()))
    result, _ = await mixin._validate_and_normalize_input({"niche": "planner", "product_type": valid_type})
    assert result["color_schemes"] == ["neutral", "warm"]


async def test_validate_color_schemes_sliced_to_num_variants(mixin):
    valid_type = next(iter(AVAILABLE_TEMPLATES.keys()))
    result, _ = await mixin._validate_and_normalize_input(
        {"niche": "planner", "product_type": valid_type, "num_variants": 1, "color_schemes": ["dark", "light", "warm"]}
    )
    assert result["color_schemes"] == ["dark"]


async def test_validate_invalid_template_cleared(mixin):
    valid_type = next(iter(AVAILABLE_TEMPLATES.keys()))
    result, _ = await mixin._validate_and_normalize_input(
        {"niche": "planner", "product_type": valid_type, "template": "nonexistent_template_xyz"}
    )
    assert result["template"] is None


async def test_validate_valid_template_kept(mixin):
    valid_type, templates = next((k, v) for k, v in AVAILABLE_TEMPLATES.items() if v)
    result, err = await mixin._validate_and_normalize_input(
        {"niche": "planner", "product_type": valid_type, "template": templates[0]}
    )
    assert err is None
    assert result["template"] == templates[0]


# ---------------------------------------------------------------------------
# _extract_research_context
# ---------------------------------------------------------------------------

def test_extract_research_context_returns_none_when_missing(mixin):
    assert mixin._extract_research_context({}) is None


def test_extract_research_context_from_research_result_key(mixin):
    ctx = mixin._extract_research_context(
        {"research_result": {"top_keywords": ["planner"], "confidence": 0.9, "market_insights": {}}}
    )
    assert ctx is not None
    assert ctx["top_keywords"] == ["planner"]
    assert ctx["confidence"] == 0.9


def test_extract_research_context_from_research_context_key(mixin):
    ctx = mixin._extract_research_context(
        {"research_context": {"top_keywords": [], "confidence": 0.5, "market_insights": {}}}
    )
    assert ctx is not None
    assert ctx["confidence"] == 0.5


def test_extract_research_context_market_fields(mixin):
    ctx = mixin._extract_research_context({
        "research_result": {
            "top_keywords": ["a", "b"],
            "confidence": 0.8,
            "market_insights": {
                "avg_price": 7.99,
                "competition_level": "medium",
                "target_audience": "teachers",
                "gaps": ["gap1"],
                "trending_styles": ["minimal"],
            },
        }
    })
    assert ctx["avg_price"] == 7.99
    assert ctx["competition_level"] == "medium"
    assert ctx["target_audience"] == "teachers"
    assert ctx["gaps"] == ["gap1"]
    assert ctx["trending_styles"] == ["minimal"]


def test_extract_research_context_keywords_capped_at_10(mixin):
    ctx = mixin._extract_research_context({
        "research_result": {
            "top_keywords": [f"kw{i}" for i in range(20)],
            "market_insights": {},
        }
    })
    assert len(ctx["top_keywords"]) == 10


def test_extract_research_context_missing_fields_default_to_none(mixin):
    ctx = mixin._extract_research_context({"research_result": {"market_insights": {}}})
    assert ctx["avg_price"] is None
    assert ctx["competition_level"] is None
    assert ctx["target_audience"] is None
    assert ctx["confidence"] == 0.0
