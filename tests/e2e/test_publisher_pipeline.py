"""tests/e2e/test_publisher_pipeline.py — A.3 gate: Publisher step criteria.

  PUB1: PQ items created by _build_cluster have cluster_id set
  PUB2: each item has product_tier assigned (non-null, in valid set)
  PUB3a: tripwire item has release_order=1
  PUB3b: bundle item has release_order=6
  PUB4: shop analysis mock_mode=True → returns None (0 external calls)
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import aiosqlite
import pytest

from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
from apps.backend.agents._research.utils import _compute_cluster_id
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.core.production_queue import ProductionQueueService

from tests.e2e.conftest import _make_memory_base


# ---------------------------------------------------------------------------
# Shared stub
# ---------------------------------------------------------------------------

def _fake_core_output(niche: str) -> dict:
    return {
        "niches": [
            {
                "name": niche, "viable": True, "audience_target": "adults seeking productivity tools",
                "expansion_potential": 20, "etsy_tags_13": [f"tag{i}" for i in range(13)],
                "selling_signals": {}, "ai_producibility": {"score": "medium"},
                "demand": {"level": "high", "trend": "rising"}, "competition": {"level": "medium"},
                "pricing": {"suggested_eur": 4.99},
            }
        ],
        "summary": f"E2E report for {niche}",
        "ladder": {
            "tripwire_blueprint": {"title": f"{niche} Quick Sheet", "price_usd": 1.99},
            "bundle_blueprint": {"title": f"{niche} Bundle", "price_usd": 12.99, "items_included": ["a", "b"]},
        },
    }


class _PublisherPipelineAgent:
    name = "research"

    def __init__(self, queue_service, memory):
        self.queue = queue_service
        self.memory = memory

    async def _single_niche_research(self, task: AgentTask, niche: str) -> AgentResult:
        return AgentResult(
            task_id="pub-e2e-001",
            agent_name="research",
            status=TaskStatus.COMPLETED,
            output_data=_fake_core_output(niche),
        )

    async def _generate_core_variations(self, niche: str, core_spec: dict, n: int = 3) -> list[dict]:
        return [
            {"variation_type": "STYLE",    "name": f"{niche} V1", "audience_target": "minimalists",    "viable": True, "confidence": 0.80, "product_type": "printable_pdf", "demand": {"level": "high"}, "competition": {"level": "medium"}, "pricing": {"suggested_eur": 4.99}, "etsy_tags_13": ["t"] * 13, "selling_signals": {}, "ai_producibility": {"score": "medium"}, "expansion_potential": 20},
            {"variation_type": "AUDIENCE", "name": f"{niche} V2", "audience_target": "teachers",        "viable": True, "confidence": 0.78, "product_type": "printable_pdf", "demand": {"level": "high"}, "competition": {"level": "medium"}, "pricing": {"suggested_eur": 4.99}, "etsy_tags_13": ["t"] * 13, "selling_signals": {}, "ai_producibility": {"score": "medium"}, "expansion_potential": 18},
            {"variation_type": "FORMAT",   "name": f"{niche} V3", "audience_target": "digital-first",   "viable": True, "confidence": 0.75, "product_type": "printable_pdf", "demand": {"level": "high"}, "competition": {"level": "medium"}, "pricing": {"suggested_eur": 4.99}, "etsy_tags_13": ["t"] * 13, "selling_signals": {}, "ai_producibility": {"score": "medium"}, "expansion_potential": 22},
        ][:n]

    async def _notify_bundle_pending(self, *args, **kwargs) -> None:
        pass

    async def _notify_telegram(self, msg: str) -> None:
        pass

    async def _build_cluster(self, winner_niche: str, section_key: str) -> None:
        return await _ResearchDiscoveryMixin._build_cluster(self, winner_niche, section_key)


# ---------------------------------------------------------------------------
# PUB1: PQ items have cluster_id set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pub1_items_have_cluster_id(tmp_path):
    mm = _make_memory_base(tmp_path)
    await mm.init()
    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        pq = ProductionQueueService(db)
        agent = _PublisherPipelineAgent(pq, mm)
        niche = "Productivity Planner PUB E2E"
        await agent._build_cluster(niche, "planners")
        cluster_id = _compute_cluster_id(niche)
        items = await pq.get_cluster_items(cluster_id)
    assert len(items) == 6, f"Expected 6 items, got {len(items)}"
    for item in items:
        assert item.cluster_id is not None and item.cluster_id != "", (
            f"Item ro={item.release_order} has missing cluster_id"
        )


# ---------------------------------------------------------------------------
# PUB2: items have product_tier assigned
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pub2_items_have_product_tier(tmp_path):
    mm = _make_memory_base(tmp_path)
    await mm.init()
    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        pq = ProductionQueueService(db)
        agent = _PublisherPipelineAgent(pq, mm)
        await agent._build_cluster("Morning Routine Printable PUB E2E", "routines")
        cluster_id = _compute_cluster_id("Morning Routine Printable PUB E2E")
        items = await pq.get_cluster_items(cluster_id)
    valid_tiers = {"tripwire", "core", "core_premium", "bundle"}
    for item in items:
        assert item.product_tier in valid_tiers, (
            f"Item ro={item.release_order} has invalid product_tier={item.product_tier!r}"
        )


# ---------------------------------------------------------------------------
# PUB3: release_order tripwire=1, bundle=6
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pub3a_release_order_tripwire_is_1(tmp_path):
    mm = _make_memory_base(tmp_path)
    await mm.init()
    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        pq = ProductionQueueService(db)
        agent = _PublisherPipelineAgent(pq, mm)
        await agent._build_cluster("Study Notes PUB E2E", "education")
        cluster_id = _compute_cluster_id("Study Notes PUB E2E")
        items = await pq.get_cluster_items(cluster_id)
    tripwire = next((i for i in items if i.product_tier == "tripwire"), None)
    assert tripwire is not None, "No tripwire item found in cluster"
    assert tripwire.release_order == 1, f"Tripwire has release_order={tripwire.release_order}"


@pytest.mark.asyncio
async def test_pub3b_release_order_bundle_is_6(tmp_path):
    mm = _make_memory_base(tmp_path)
    await mm.init()
    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        pq = ProductionQueueService(db)
        agent = _PublisherPipelineAgent(pq, mm)
        await agent._build_cluster("Budget Tracker PUB E2E", "finance")
        cluster_id = _compute_cluster_id("Budget Tracker PUB E2E")
        items = await pq.get_cluster_items(cluster_id)
    bundle = next((i for i in items if i.product_tier == "bundle"), None)
    assert bundle is not None, "No bundle item found in cluster"
    assert bundle.release_order == 6, f"Bundle has release_order={bundle.release_order}"


# ---------------------------------------------------------------------------
# PUB4: shop analysis mock_mode=True → returns None (no Tavily calls)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pub4_shop_analysis_mock_mode_returns_none_no_external_calls():
    """C.3 criterion: mock_mode=True → _get_competitor_shop_analysis returns None, 0 Tavily calls."""
    from apps.backend.agents._market_data._shop_analysis_mixin import _ShopAnalysisMixin

    class _MockShopAgent(_ShopAnalysisMixin):
        def __init__(self):
            self._mock = True
            self._memory = AsyncMock()

    agent = _MockShopAgent()
    result = await agent._get_competitor_shop_analysis("Bullet Journal E2E", "planners")
    assert result is None, f"Expected None in mock_mode, got {result!r}"
    agent._memory.query_chromadb.assert_not_called()
