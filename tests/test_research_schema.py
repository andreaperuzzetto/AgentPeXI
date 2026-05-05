"""Tests for PA-4 — Research Agent schema versioning."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# Task 1 — RESEARCH_SCHEMA_VERSION constant + prompt fields
# ---------------------------------------------------------------------------

def test_research_schema_version_constant():
    from apps.backend.agents._research.prompts import RESEARCH_SCHEMA_VERSION
    assert RESEARCH_SCHEMA_VERSION == "2"


def test_prompt_contains_audience_target():
    from apps.backend.agents._research.prompts import SYSTEM_PROMPT
    assert "audience_target" in SYSTEM_PROMPT


def test_prompt_contains_expansion_potential():
    from apps.backend.agents._research.prompts import SYSTEM_PROMPT
    assert "expansion_potential" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Task 2 — confidence cap at 0.4 when audience_target missing
# ---------------------------------------------------------------------------

from apps.backend.agents._research.scoring_mixin import _ResearchScoringMixin

_FULL_SOURCES = {
    "pricing":     "etsy_api",
    "competitors": "etsy_api",
    "trend":       "google_trends",
    "keywords":    "erank_content",
    "entry_point": "market_signals",
}
_FULL_NICHE = {
    "viable": True,
    "etsy_tags_13": [f"tag{i}" for i in range(13)],
    "selling_signals": {
        "thumbnail_style": "flat lay",
        "conversion_triggers": ["mockup"],
        "bundle_vs_single": "single",
        "first_listing_recommendation": "planner A4",
    },
    "pricing": {"conversion_sweet_spot_usd": 5.0, "launch_price_usd": 4.0},
    "demand": {"peak_months": [1], "publish_timing_advice": "now"},
}


def test_confidence_capped_at_04_when_audience_target_missing():
    output = {"niches": [{**_FULL_NICHE}]}  # no audience_target
    confidence, missing = _ResearchScoringMixin._calculate_confidence(_FULL_SOURCES, output)
    assert confidence <= 0.40
    assert any("audience_target" in m for m in missing)


def test_confidence_not_capped_when_audience_target_present():
    output = {"niches": [{**_FULL_NICHE, "audience_target": "women 25-40", "expansion_potential": "high"}]}
    confidence, missing = _ResearchScoringMixin._calculate_confidence(_FULL_SOURCES, output)
    assert confidence > 0.40
    assert not any("audience_target" in m for m in missing)


def test_confidence_cap_does_not_affect_already_low_score():
    """If score is already < 0.4 without audience_target, capping doesn't raise it."""
    low_sources = {
        "pricing":     "llm_inference",
        "competitors": "none",
        "trend":       "llm_inference",
        "keywords":    "llm_inference",
        "entry_point": "none",
    }
    output = {"niches": [{**_FULL_NICHE}]}
    confidence, missing = _ResearchScoringMixin._calculate_confidence(low_sources, output)
    assert confidence <= 0.40
    assert any("audience_target" in m for m in missing)


# ---------------------------------------------------------------------------
# Task 3 — _is_cache_valid staticmethod in _ResearchAnalysisMixin
# ---------------------------------------------------------------------------

from apps.backend.agents._research.analysis_mixin import _ResearchAnalysisMixin


def test_is_cache_valid_current_schema_fresh():
    meta = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "2",
    }
    assert _ResearchAnalysisMixin._is_cache_valid(meta) is True


def test_is_cache_valid_stale_schema_version_rejects():
    meta = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1",
    }
    assert _ResearchAnalysisMixin._is_cache_valid(meta) is False


def test_is_cache_valid_missing_schema_version_rejects():
    meta = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        # no schema_version key
    }
    assert _ResearchAnalysisMixin._is_cache_valid(meta) is False


def test_is_cache_valid_expired_ttl_rejects():
    meta = {
        "created_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
        "schema_version": "2",
    }
    assert _ResearchAnalysisMixin._is_cache_valid(meta) is False


def test_is_cache_valid_empty_meta_rejects():
    assert _ResearchAnalysisMixin._is_cache_valid({}) is False


def test_is_cache_valid_invalid_date_rejects():
    meta = {"created_at": "not-a-date", "schema_version": "2"}
    assert _ResearchAnalysisMixin._is_cache_valid(meta) is False
