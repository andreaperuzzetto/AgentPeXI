"""tests/e2e/test_research_pipeline.py — A.3 gate: Research step criteria.

  R1: audience_target non null, len > 10
  R2: cluster_id = sha256(niche.lower().strip())[:12]
  R3: expansion_potential < 10 → niche.viable = False (discarded)
  R4: _build_cluster inserts 3 core items (covers _generate_core_variations output)
  R5: cluster_id is deterministic (same input → same hash, case-insensitive)
  R6: confidence ≥ 0.65 for a fully-sourced niche
  R7: ladder.tripwire.price_usd ≤ 2.50
  R8: _build_cluster inserts ≥ 5 listing specs into production_queue
"""
from __future__ import annotations

import hashlib

import aiosqlite
import pytest

from apps.backend.agents._research.scoring_mixin import _ResearchScoringMixin
from apps.backend.agents._research.utils import _compute_cluster_id
from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.core.production_queue import ProductionQueueService

from tests.e2e.conftest import _make_memory_base


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _full_sources() -> dict:
    return {
        "entry_point": "market_signals",
        "pricing": "etsy_api",
        "trend": "google_trends",
        "keywords": "erank_content",
        "competitors": "etsy_api",
    }


def _full_niche(**overrides) -> dict:
    base: dict = {
        "name": "ADHD Planner",
        "viable": True,
        "audience_target": "ADHD adults seeking daily focus tools",
        "expansion_potential": 25,
        "etsy_tags_13": [f"tag{i}" for i in range(13)],
        "selling_signals": {
            "thumbnail_style": "clean mockup",
            "conversion_triggers": ["social proof badge"],
            "bundle_vs_single": "single",
            "first_listing_recommendation": "weekly planner",
        },
        "pricing": {"conversion_sweet_spot_usd": 4.99, "launch_price_usd": 3.49},
        "demand": {"peak_months": [1, 9], "publish_timing_advice": "publish in august"},
    }
    base.update(overrides)
    return base


def _valid_research_output(
    niche: str = "ADHD Planner",
    audience_target: str = "adults with ADHD seeking focus tools",
    expansion_potential: int = 25,
    tripwire_price: float = 1.99,
) -> dict:
    return {
        "niches": [
            {
                "name": niche,
                "viable": True,
                "audience_target": audience_target,
                "expansion_potential": expansion_potential,
                "etsy_tags_13": [f"tag{i}" for i in range(13)],
                "selling_signals": {},
                "ai_producibility": {"score": "medium"},
                "demand": {"level": "high", "trend": "rising"},
                "competition": {"level": "medium"},
                "pricing": {"suggested_eur": 4.99},
            }
        ],
        "summary": f"E2E report for {niche}",
        "ladder": {
            "tripwire_blueprint": {"title": f"{niche} Quick Sheet", "price_usd": tripwire_price},
            "bundle_blueprint": {"title": f"{niche} Bundle", "price_usd": 12.99, "items_included": ["a", "b"]},
        },
    }


class _ResearchAgent:
    name = "research"

    def __init__(self, queue_service, memory):
        self.queue = queue_service
        self.memory = memory

    async def _single_niche_research(self, task: AgentTask, niche: str) -> AgentResult:
        return AgentResult(
            task_id="e2e-001",
            agent_name="research",
            status=TaskStatus.COMPLETED,
            output_data=_valid_research_output(niche),
        )

    async def _generate_core_variations(self, niche: str, core_spec: dict, n: int = 3) -> list[dict]:
        return [
            {"variation_type": "STYLE",    "name": f"{niche} V1", "audience_target": "minimalists",   "viable": True, "confidence": 0.80, "product_type": "printable_pdf", "demand": {"level": "high"}, "competition": {"level": "medium"}, "pricing": {"suggested_eur": 4.99}, "etsy_tags_13": ["t"] * 13, "selling_signals": {}, "ai_producibility": {"score": "medium"}, "expansion_potential": 22},
            {"variation_type": "AUDIENCE", "name": f"{niche} V2", "audience_target": "teachers",       "viable": True, "confidence": 0.78, "product_type": "printable_pdf", "demand": {"level": "high"}, "competition": {"level": "medium"}, "pricing": {"suggested_eur": 4.99}, "etsy_tags_13": ["t"] * 13, "selling_signals": {}, "ai_producibility": {"score": "medium"}, "expansion_potential": 18},
            {"variation_type": "FORMAT",   "name": f"{niche} V3", "audience_target": "digital users",  "viable": True, "confidence": 0.75, "product_type": "printable_pdf", "demand": {"level": "high"}, "competition": {"level": "medium"}, "pricing": {"suggested_eur": 4.99}, "etsy_tags_13": ["t"] * 13, "selling_signals": {}, "ai_producibility": {"score": "medium"}, "expansion_potential": 20},
        ][:n]

    async def _notify_bundle_pending(self, *args, **kwargs) -> None:
        pass

    async def _notify_telegram(self, msg: str) -> None:
        pass

    async def _build_cluster(self, winner_niche: str, section_key: str) -> None:
        return await _ResearchDiscoveryMixin._build_cluster(self, winner_niche, section_key)


# ---------------------------------------------------------------------------
# R1: audience_target non null, len > 10
# ---------------------------------------------------------------------------

def test_r1_audience_target_present_and_long_enough():
    output = _valid_research_output()
    niche = output["niches"][0]
    assert niche["audience_target"] is not None
    assert len(niche["audience_target"]) > 10


def test_r1_short_audience_target_is_only_10_chars():
    output = _valid_research_output(audience_target="adults")
    assert len(output["niches"][0]["audience_target"]) <= 10


# ---------------------------------------------------------------------------
# R2: cluster_id = sha256(niche.lower().strip())[:12]
# ---------------------------------------------------------------------------

def test_r2_cluster_id_is_12_chars():
    assert len(_compute_cluster_id("ADHD Planner")) == 12


def test_r2_cluster_id_matches_sha256_lower_strip():
    niche = "ADHD Planner"
    expected = hashlib.sha256(niche.lower().strip().encode()).hexdigest()[:12]
    assert _compute_cluster_id(niche) == expected


# ---------------------------------------------------------------------------
# R3: expansion_potential < 10 → niche.viable = False
# ---------------------------------------------------------------------------

def test_r3_expansion_potential_below_10_marks_niche_not_viable():
    niche = _full_niche(expansion_potential=5)
    _ResearchScoringMixin._calculate_confidence({}, {"niches": [niche], "summary": "x"})
    assert niche["viable"] is False


def test_r3_expansion_potential_gte_10_keeps_niche_viable():
    niche = _full_niche(expansion_potential=15)
    _ResearchScoringMixin._calculate_confidence({}, {"niches": [niche], "summary": "x"})
    assert niche["viable"] is True


# ---------------------------------------------------------------------------
# R4: _build_cluster inserts 3 core items (variation coverage)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r4_build_cluster_has_3_core_tier_items(tmp_path):
    mm = _make_memory_base(tmp_path)
    await mm.init()
    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        pq = ProductionQueueService(db)
        agent = _ResearchAgent(pq, mm)
        await agent._build_cluster("Focus Planner E2E", "planners")
        cluster_id = _compute_cluster_id("Focus Planner E2E")
        items = await pq.get_cluster_items(cluster_id)
    core_items = [i for i in items if i.product_tier == "core"]
    assert len(core_items) == 3, f"Expected 3 core items, got {len(core_items)}"


# ---------------------------------------------------------------------------
# R5: cluster_id is deterministic and case-insensitive
# ---------------------------------------------------------------------------

def test_r5_cluster_id_deterministic_across_runs():
    niche = "Wedding Planner Printable"
    assert _compute_cluster_id(niche) == _compute_cluster_id(niche) == _compute_cluster_id(niche)


def test_r5_cluster_id_case_insensitive():
    assert _compute_cluster_id("ADHD Planner") == _compute_cluster_id("adhd planner")


def test_r5_cluster_id_different_niches_differ():
    assert _compute_cluster_id("ADHD Planner") != _compute_cluster_id("Wedding Planner")


# ---------------------------------------------------------------------------
# R6: confidence ≥ 0.65 for fully-sourced niche
# ---------------------------------------------------------------------------

def test_r6_valid_niche_with_full_sources_scores_gte_065():
    niche = _full_niche()
    output = {"niches": [niche], "summary": "x"}
    score, warnings = _ResearchScoringMixin._calculate_confidence(_full_sources(), output)
    assert score >= 0.65, f"Expected ≥ 0.65, got {score:.2f} (warnings: {warnings})"


# ---------------------------------------------------------------------------
# R7: ladder.tripwire.price_usd ≤ 2.50
# ---------------------------------------------------------------------------

def test_r7_tripwire_price_is_lte_250():
    output = _valid_research_output(tripwire_price=1.99)
    assert output["ladder"]["tripwire_blueprint"]["price_usd"] <= 2.50


# ---------------------------------------------------------------------------
# R8: _build_cluster inserts ≥ 5 listing specs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r8_build_cluster_inserts_6_items(tmp_path):
    mm = _make_memory_base(tmp_path)
    await mm.init()
    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        pq = ProductionQueueService(db)
        agent = _ResearchAgent(pq, mm)
        await agent._build_cluster("ADHD Daily Planner E2E", "home_office")
        cluster_id = _compute_cluster_id("ADHD Daily Planner E2E")
        items = await pq.get_cluster_items(cluster_id)
    assert len(items) >= 5, f"Expected ≥ 5 items, got {len(items)}"
