"""C.1 — Product Ladder: TDD test suite (written before implementation).

All 15 tests must FAIL (RED) before C.1 is implemented.

Coverage:
  1-2   : prompts.py — schema version + ladder keywords
  3-6   : scoring_mixin — expansion_potential gate + boost
  7     : scoring_mixin — audience_target +0.08 contribution
  8-9   : scoring_mixin — rebalanced selling/seasonality weights
  10-11 : analysis_mixin — requires_human_review flag helper
  12-13 : production_queue — create_item product_tier validation
  14-15 : production_queue — ProductionQueueItem.from_row product_tier field
"""
from __future__ import annotations

import pytest
import aiosqlite

# ---------------------------------------------------------------------------
# 1-2: prompts.py
# ---------------------------------------------------------------------------

from apps.backend.agents._research.prompts import RESEARCH_SCHEMA_VERSION, SYSTEM_PROMPT


def test_research_schema_version_is_3():
    assert RESEARCH_SCHEMA_VERSION == "3"


def test_system_prompt_contains_ladder_keywords():
    for keyword in ("ladder", "tripwire", "bundle_blueprint", "ai_producibility"):
        assert keyword in SYSTEM_PROMPT, f"SYSTEM_PROMPT is missing keyword: {keyword!r}"


# ---------------------------------------------------------------------------
# 3-9: scoring_mixin
# ---------------------------------------------------------------------------

from apps.backend.agents._research.scoring_mixin import _ResearchScoringMixin


def _empty_sources() -> dict:
    return {}


def _full_sources() -> dict:
    return {
        "entry_point": "market_signals",
        "pricing": "etsy_api",
        "trend": "google_trends",
        "keywords": "erank_content",
        "competitors": "etsy_api",
    }


def _full_niche(**overrides) -> dict:
    """Fully populated niche — audience_target='x' avoids both +0.08 bonus and 0.40 cap."""
    base: dict = {
        "name": "Test Niche",
        "viable": True,
        "audience_target": "x",
        "expansion_potential": 25,
        "etsy_tags_13": [f"tag{i}" for i in range(13)],
        "selling_signals": {
            "thumbnail_style": "clean mockup",
            "conversion_triggers": ["social proof badge"],
            "bundle_vs_single": "single",
            "first_listing_recommendation": "weekly planner",
        },
        "pricing": {"conversion_sweet_spot_usd": 4.99, "launch_price_usd": 3.49},
        "demand": {"peak_months": [1, 2], "publish_timing_advice": "publish in december"},
    }
    base.update(overrides)
    return base


# --- Test 3: expansion_potential < 10 → viable=False, score=0.0 ---

def test_expansion_potential_below_10_marks_niche_not_viable():
    niche = _full_niche(expansion_potential=5)
    output = {"niches": [niche]}
    score, _ = _ResearchScoringMixin._calculate_confidence(_empty_sources(), output)
    assert output["niches"][0].get("viable") is False
    # Early return: score = sources-only (trend fallback 0.03), no completeness added
    assert score < 0.10


# --- Test 4: expansion_potential ≥ 20 → +0.05 boost ---

def test_expansion_potential_gte_20_adds_0_05_boost():
    """expansion=25 should score 0.05 more than expansion=15."""
    niche_low = _full_niche(expansion_potential=15)
    niche_high = _full_niche(expansion_potential=25)
    score_low, _ = _ResearchScoringMixin._calculate_confidence(
        _empty_sources(), {"niches": [niche_low]}
    )
    score_high, _ = _ResearchScoringMixin._calculate_confidence(
        _empty_sources(), {"niches": [niche_high]}
    )
    assert score_high - score_low == pytest.approx(0.05, abs=0.001)


# --- Test 5: expansion_potential = 15 → no boost, exact score ---

def test_expansion_potential_15_no_boost_no_discard():
    """expansion=15 is in the neutral range [10, 20): viable and no +0.05.

    Expected score (empty sources → trend fallback +0.03, audience_target='x'):
      sources=0.03, tags=13→0.15, selling_complete→0.10, pricing_both→0.10, seasonality→0.02
      total = 0.40 (would be 0.45 with the +0.05 boost, 0.48 in v2 with old weights)
    """
    niche = _full_niche(expansion_potential=15)
    output = {"niches": [niche]}
    score, _ = _ResearchScoringMixin._calculate_confidence(_empty_sources(), output)
    assert output["niches"][0].get("viable") is not False
    assert score == pytest.approx(0.40, abs=0.001)


# --- Test 6: expansion_potential = None → viable=False (silent discard) ---

def test_expansion_potential_none_marks_niche_not_viable():
    niche = _full_niche(expansion_potential=None)
    output = {"niches": [niche]}
    score, _ = _ResearchScoringMixin._calculate_confidence(_empty_sources(), output)
    assert output["niches"][0].get("viable") is False
    # Early return: score = sources-only (trend fallback 0.03), no completeness added
    assert score < 0.10


# --- Test 7: audience_target > 10 chars → +0.08 contribution ---

def test_audience_target_present_adds_0_08():
    """audience_target present and > 10 chars adds 0.08 to confidence.

    Uses minimal niche (all scores at fallback minimums) to isolate the delta:
      without audience: sources=0, tags=0.02, selling=0.01, pricing=0.01, seasonality=0.01 = 0.05
      with audience:    0.05 + 0.08 = 0.13
    """
    base = _full_niche(
        audience_target="",
        expansion_potential=15,
        etsy_tags_13=[],
        selling_signals={},
        pricing={},
        demand={},
    )
    with_audience = _full_niche(
        audience_target="donne 25-40 con ansia da organizzazione",
        expansion_potential=15,
        etsy_tags_13=[],
        selling_signals={},
        pricing={},
        demand={},
    )
    score_base, _ = _ResearchScoringMixin._calculate_confidence(
        _empty_sources(), {"niches": [base]}
    )
    score_with, _ = _ResearchScoringMixin._calculate_confidence(
        _empty_sources(), {"niches": [with_audience]}
    )
    assert score_with - score_base == pytest.approx(0.08, abs=0.001)


# --- Test 8: selling_signals complete → max 0.10 (not 0.15) ---

def test_selling_signals_complete_contributes_max_0_10():
    """With selling_complete and all other fields at their minimum fallback:
      sources: trend fallback → 0.03
      tags=0   → 0.02
      selling  → 0.10  (was 0.15 in v2)
      pricing  → 0.01
      season   → 0.01
      total    = 0.17  (v2 gives 0.22)
    """
    niche = _full_niche(
        audience_target="",
        expansion_potential=15,
        etsy_tags_13=[],
        pricing={},
        demand={},
    )
    # selling_signals remains complete (from _full_niche default)
    score, _ = _ResearchScoringMixin._calculate_confidence(_empty_sources(), {"niches": [niche]})
    assert score == pytest.approx(0.17, abs=0.001)


# --- Test 9: seasonality complete → max 0.02 (not 0.05) ---

def test_seasonality_complete_contributes_max_0_02():
    """With demand complete and all other fields at minimum:
      sources: trend fallback → 0.03
      tags=0   → 0.02
      selling  → 0.01
      pricing  → 0.01
      season   → 0.02  (was 0.05 in v2)
      total    = 0.09  (v2 gives 0.12)
    """
    niche = _full_niche(
        audience_target="",
        expansion_potential=15,
        etsy_tags_13=[],
        selling_signals={},
        pricing={},
        # demand complete (from _full_niche default)
    )
    score, _ = _ResearchScoringMixin._calculate_confidence(_empty_sources(), {"niches": [niche]})
    assert score == pytest.approx(0.09, abs=0.001)


# ---------------------------------------------------------------------------
# 10-11: analysis_mixin — requires_human_review flag helper
# ---------------------------------------------------------------------------

from apps.backend.agents._research.analysis_mixin import _ResearchAnalysisMixin


def test_requires_human_review_true_when_ai_producibility_low():
    output = {
        "niches": [{
            "name": "Custom pet portrait",
            "ai_producibility": {
                "score": "low",
                "reasoning": "requires complex illustration — AI quality inconsistent",
            },
        }]
    }
    _ResearchAnalysisMixin._apply_requires_human_review(output)
    assert output["niches"][0]["requires_human_review"] is True


def test_requires_human_review_false_when_ai_producibility_high():
    output = {
        "niches": [{
            "name": "Weekly planner",
            "ai_producibility": {
                "score": "high",
                "reasoning": "simple grid layout — fully automatable",
            },
        }]
    }
    _ResearchAnalysisMixin._apply_requires_human_review(output)
    assert output["niches"][0]["requires_human_review"] is False


# ---------------------------------------------------------------------------
# 12-15: production_queue — product_tier field
# ---------------------------------------------------------------------------

from apps.backend.core.production_queue import ProductionQueueItem, ProductionQueueService

# Minimal schema that includes the new product_tier column (C.1.4).
_C1_SCHEMA = """
CREATE TABLE IF NOT EXISTS production_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL DEFAULT (hex(randomblob(8))),
    product_type TEXT NOT NULL DEFAULT 'printable_pdf',
    niche TEXT NOT NULL DEFAULT '',
    brief TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending_design',
    keywords TEXT,
    entry_score REAL DEFAULT 0.0,
    design_prompt TEXT,
    image_url TEXT,
    thumbnail_path TEXT,
    listing_title TEXT,
    listing_description TEXT,
    listing_tags TEXT,
    listing_price REAL,
    approval_sent_at REAL,
    approval_message_id INTEGER,
    approval_chat_id INTEGER,
    skip_reason TEXT,
    skip_count_user INTEGER DEFAULT 0,
    skip_count_timeout INTEGER DEFAULT 0,
    error_message TEXT,
    scheduled_publish_at REAL,
    published_at REAL,
    etsy_listing_id TEXT,
    llm_cost_usd REAL DEFAULT 0.0,
    image_cost_usd REAL DEFAULT 0.0,
    listing_fee_usd REAL DEFAULT 0.20,
    ads_activated INTEGER DEFAULT 0,
    ads_paused INTEGER DEFAULT 0,
    loop_run_id TEXT,
    ab_price_variant TEXT,
    file_paths TEXT,
    product_tier TEXT DEFAULT 'core',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# Minimal row dict matching all required fields in ProductionQueueItem.from_row
_BASE_ROW: dict = {
    "id": 1,
    "niche": "test niche",
    "product_type": "printable_pdf",
    "keywords": '["tag1","tag2"]',
    "entry_score": 0.75,
    "status": "pending_design",
    "design_prompt": None,
    "image_url": None,
    "thumbnail_path": None,
    "listing_title": None,
    "listing_description": None,
    "listing_tags": None,
    "listing_price": None,
    "approval_sent_at": None,
    "approval_message_id": None,
    "approval_chat_id": None,
    "skip_reason": None,
    "skip_count_user": 0,
    "skip_count_timeout": 0,
    "error_message": None,
    "scheduled_publish_at": None,
    "published_at": None,
    "etsy_listing_id": None,
    "llm_cost_usd": 0.0,
    "image_cost_usd": 0.0,
    "listing_fee_usd": 0.20,
    "ads_activated": 0,
    "ads_paused": 0,
    "loop_run_id": None,
    "created_at": "2024-01-01T00:00:00+00:00",
    "updated_at": "2024-01-01T00:00:00+00:00",
}


@pytest.fixture
async def c1_db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_C1_SCHEMA)
        await conn.commit()
        yield conn


@pytest.fixture
async def c1_queue(c1_db):
    return ProductionQueueService(c1_db)


# --- Test 12: invalid product_tier → ValueError ---

@pytest.mark.asyncio
async def test_create_item_invalid_product_tier_raises_value_error(c1_queue):
    with pytest.raises(ValueError):
        await c1_queue.create_item(
            "party supplies niche",
            "printable_pdf",
            ["tag1"],
            product_tier="invalid",
        )


# --- Test 13: valid product_tier → accepted, returns int id ---

@pytest.mark.asyncio
async def test_create_item_valid_product_tier_tripwire(c1_queue):
    item_id = await c1_queue.create_item(
        "party supplies niche",
        "printable_pdf",
        ["tag1"],
        product_tier="tripwire",
    )
    assert isinstance(item_id, int)
    assert item_id > 0


# --- Test 14: from_row with product_tier present ---

def test_queue_item_from_row_with_product_tier():
    row = dict(_BASE_ROW, product_tier="bundle")
    item = ProductionQueueItem.from_row(row)  # type: ignore[arg-type]
    assert item.product_tier == "bundle"


# --- Test 15: from_row without product_tier key → defaults to "core" ---

def test_queue_item_from_row_missing_product_tier_defaults_to_core():
    row = dict(_BASE_ROW)  # no product_tier key
    row.pop("product_tier", None)
    item = ProductionQueueItem.from_row(row)  # type: ignore[arg-type]
    assert item.product_tier == "core"
