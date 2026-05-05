"""Tests for _ResearchScoringMixin._calculate_confidence (pure static method)."""
from __future__ import annotations

import pytest

from apps.backend.agents._research.scoring_mixin import _ResearchScoringMixin


def _make_output(n_tags=13, has_selling=True, has_pricing=True, has_timing=True, viable=True):
    """Build a minimal output dict with one niche (schema v2: includes audience_target)."""
    selling = (
        {
            "thumbnail_style": "clean",
            "conversion_triggers": "bestseller badge",
            "bundle_vs_single": "single",
            "first_listing_recommendation": "weekly planner",
        }
        if has_selling
        else {}
    )
    pricing = (
        {"conversion_sweet_spot_usd": 9.99, "launch_price_usd": 7.99}
        if has_pricing
        else {}
    )
    demand = (
        {"peak_months": ["January"], "publish_timing_advice": "publish in December"}
        if has_timing
        else {}
    )
    return {
        "niches": [
            {
                "name": "Test Niche",
                "viable": viable,
                "audience_target": "women 25-40 interested in planning",
                "expansion_potential": "high",
                "etsy_tags_13": [f"tag{i}" for i in range(n_tags)],
                "selling_signals": selling,
                "pricing": pricing,
                "demand": demand,
            }
        ]
    }


def _full_sources():
    return {
        "entry_point": "market_signals",
        "pricing": "etsy_api",
        "trend": "google_trends",
        "keywords": "erank_content",
        "competitors": "etsy_api",
    }


# ---------------------------------------------------------------------------
# Data sources — Part 1 (55%)
# ---------------------------------------------------------------------------

def test_calculate_confidence_all_best_sources():
    score, missing = _ResearchScoringMixin._calculate_confidence(
        _full_sources(), _make_output()
    )
    assert score >= 0.90
    assert missing == []


def test_calculate_confidence_no_entry_point_still_scores():
    sources = _full_sources()
    sources["entry_point"] = "none"
    score, missing = _ResearchScoringMixin._calculate_confidence(sources, _make_output())
    # entry_point doesn't directly affect score; all other sources still good
    assert score >= 0.85
    assert isinstance(missing, list)


def test_calculate_confidence_blog_inference_pricing_adds_missing():
    sources = _full_sources()
    sources["pricing"] = "blog_inference"
    _, missing = _ResearchScoringMixin._calculate_confidence(sources, _make_output())
    assert any("prezzi" in m for m in missing)


def test_calculate_confidence_llm_pricing_adds_missing():
    sources = _full_sources()
    sources["pricing"] = "llm_inference"
    _, missing = _ResearchScoringMixin._calculate_confidence(sources, _make_output())
    assert any("prezzo" in m for m in missing)


def test_calculate_confidence_no_trend_data_adds_missing():
    sources = _full_sources()
    sources["trend"] = "llm_guess"
    _, missing = _ResearchScoringMixin._calculate_confidence(sources, _make_output())
    assert any("trend" in m.lower() for m in missing)


def test_calculate_confidence_community_keywords():
    sources = _full_sources()
    sources["keywords"] = "community_search"
    score, _ = _ResearchScoringMixin._calculate_confidence(sources, _make_output())
    assert score > 0


def test_calculate_confidence_llm_keywords_adds_missing():
    sources = _full_sources()
    sources["keywords"] = "llm_inference"
    _, missing = _ResearchScoringMixin._calculate_confidence(sources, _make_output())
    assert any("keyword" in m.lower() for m in missing)


def test_calculate_confidence_blog_competitor_adds_missing():
    sources = _full_sources()
    sources["competitors"] = "blog_mention"
    _, missing = _ResearchScoringMixin._calculate_confidence(sources, _make_output())
    assert any("competitor" in m.lower() for m in missing)


# ---------------------------------------------------------------------------
# Output completeness — Part 2 (45%)
# ---------------------------------------------------------------------------

def test_calculate_confidence_no_viable_niches():
    output = _make_output(viable=False)
    _, missing = _ResearchScoringMixin._calculate_confidence(_full_sources(), output)
    assert any("viable" in m for m in missing)


def test_calculate_confidence_partial_tags_8():
    output = _make_output(n_tags=8)
    _, missing = _ResearchScoringMixin._calculate_confidence(_full_sources(), output)
    assert any("tag" in m.lower() for m in missing)


def test_calculate_confidence_few_tags_penalized():
    output = _make_output(n_tags=3)
    _, missing = _ResearchScoringMixin._calculate_confidence(_full_sources(), output)
    assert any("tag" in m.lower() for m in missing)


def test_calculate_confidence_missing_selling_signals():
    output = _make_output(has_selling=False)
    _, missing = _ResearchScoringMixin._calculate_confidence(_full_sources(), output)
    assert any("selling" in m.lower() for m in missing)


def test_calculate_confidence_partial_selling_signals():
    output = _make_output()
    output["niches"][0]["selling_signals"] = {"thumbnail_style": "clean"}  # partial
    _, missing = _ResearchScoringMixin._calculate_confidence(_full_sources(), output)
    assert any("selling" in m.lower() for m in missing)


def test_calculate_confidence_missing_pricing():
    output = _make_output(has_pricing=False)
    _, missing = _ResearchScoringMixin._calculate_confidence(_full_sources(), output)
    assert any("pricing" in m.lower() or "prezzo" in m.lower() for m in missing)


def test_calculate_confidence_partial_pricing_sweet_spot_only():
    output = _make_output()
    output["niches"][0]["pricing"] = {"conversion_sweet_spot_usd": 9.99}  # no launch price
    _, missing = _ResearchScoringMixin._calculate_confidence(_full_sources(), output)
    assert any("launch" in m.lower() for m in missing)


def test_calculate_confidence_missing_timing():
    output = _make_output(has_timing=False)
    _, missing = _ResearchScoringMixin._calculate_confidence(_full_sources(), output)
    assert any("timing" in m.lower() or "stagionale" in m.lower() for m in missing)


def test_calculate_confidence_score_capped_at_1():
    score, _ = _ResearchScoringMixin._calculate_confidence(_full_sources(), _make_output())
    assert score <= 1.0


def test_calculate_confidence_score_is_float():
    score, missing = _ResearchScoringMixin._calculate_confidence(_full_sources(), _make_output())
    assert isinstance(score, float)
    assert isinstance(missing, list)


def test_calculate_confidence_cached_sources():
    sources = {
        "entry_point": "market_signals",
        "pricing": "cached",
        "trend": "cached",
        "keywords": "cached",
        "competitors": "cached",
    }
    score, _ = _ResearchScoringMixin._calculate_confidence(sources, _make_output())
    assert score >= 0.85


def _make_complete_niche() -> dict:
    return {
        "viable": True,
        "audience_target": "parents of toddlers",
        "etsy_tags_13": [f"tag{i}" for i in range(13)],
        "selling_signals": {
            "thumbnail_style": "clean",
            "conversion_triggers": "bestseller badge",
            "bundle_vs_single": "single",
            "first_listing_recommendation": "weekly planner",
        },
        "pricing": {"conversion_sweet_spot_usd": 9.99, "launch_price_usd": 7.99},
        "demand": {"peak_months": ["Jan"], "publish_timing_advice": "publish in Dec"},
    }


def _make_incomplete_niche() -> dict:
    return {
        "viable": True,
        "audience_target": "",
        "etsy_tags_13": [],
        "selling_signals": {},
        "pricing": {},
        "demand": {},
    }


def test_calculate_confidence_evaluates_all_viable_niches_not_just_first():
    """M5: _calculate_confidence deve valutare TUTTE le viable_niches per la
    completezza dell'output, non solo viable_niches[0].

    Con niche[0] completa e niche[1] incompleta, la confidence deve essere
    inferiore a quella ottenuta quando entrambe le nicchie sono complete.
    """
    sources = _full_sources()

    conf_both_complete, _ = _ResearchScoringMixin._calculate_confidence(
        sources, {"niches": [_make_complete_niche(), _make_complete_niche()]}
    )
    conf_mixed, _ = _ResearchScoringMixin._calculate_confidence(
        sources, {"niches": [_make_complete_niche(), _make_incomplete_niche()]}
    )

    assert conf_mixed < conf_both_complete, (
        f"conf_mixed={conf_mixed} should be < conf_both_complete={conf_both_complete} "
        "— _calculate_confidence only checks viable_niches[0], ignoring the rest"
    )
