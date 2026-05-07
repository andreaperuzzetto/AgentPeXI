"""tests/e2e/test_cluster_pipeline.py — A.3 gate: Cluster step criteria.

  CL1: Production queue entry has product_type = 'printable_pdf'
  CL2: Core items have release_order ∈ {2, 3, 4}
  CL3: Tripwire item has release_order = 1
  CL4: Bundle item has status = 'pending_approval'
  CL5: Two calls with same niche → single cluster_id (dedup)
  CL6: _update_cluster_crossrefs called only if ≥ 2 published items
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
from apps.backend.agents._research.utils import _compute_cluster_id
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.core.production_queue import ProductionQueueService

from tests.e2e.conftest import _make_memory_base


# ---------------------------------------------------------------------------
# Stub agent (same pattern as test_c2_cluster_strategy)
# ---------------------------------------------------------------------------

class _ClusterAgent:
    name = "research"

    def __init__(self, queue_service, memory):
        self.queue = queue_service
        self.memory = memory

    async def _single_niche_research(self, task: AgentTask, niche: str) -> AgentResult:
        return AgentResult(
            task_id="e2e-cl-001",
            agent_name="research",
            status=TaskStatus.COMPLETED,
            output_data=_valid_cluster_output(niche),
        )

    async def _generate_core_variations(self, niche: str, core_spec: dict, n: int = 3) -> list[dict]:
        return [
            {"variation_type": "STYLE",    "name": f"{niche} V1", "audience_target": "x", "viable": True, "confidence": 0.80, "product_type": "printable_pdf", "demand": {"level": "high"}, "competition": {"level": "medium"}, "pricing": {"suggested_eur": 4.99}, "etsy_tags_13": ["t"] * 13, "selling_signals": {}, "ai_producibility": {"score": "medium"}, "expansion_potential": 22},
            {"variation_type": "AUDIENCE", "name": f"{niche} V2", "audience_target": "y", "viable": True, "confidence": 0.78, "product_type": "printable_pdf", "demand": {"level": "high"}, "competition": {"level": "medium"}, "pricing": {"suggested_eur": 4.99}, "etsy_tags_13": ["t"] * 13, "selling_signals": {}, "ai_producibility": {"score": "medium"}, "expansion_potential": 18},
            {"variation_type": "FORMAT",   "name": f"{niche} V3", "audience_target": "z", "viable": True, "confidence": 0.75, "product_type": "printable_pdf", "demand": {"level": "high"}, "competition": {"level": "medium"}, "pricing": {"suggested_eur": 4.99}, "etsy_tags_13": ["t"] * 13, "selling_signals": {}, "ai_producibility": {"score": "medium"}, "expansion_potential": 20},
        ][:n]

    async def _notify_bundle_pending(self, *args, **kwargs) -> None:
        pass

    async def _notify_telegram(self, msg: str) -> None:
        pass

    async def _build_cluster(self, winner_niche: str, section_key: str) -> None:
        return await _ResearchDiscoveryMixin._build_cluster(self, winner_niche, section_key)


def _valid_cluster_output(niche: str) -> dict:
    return {
        "niches": [
            {
                "name": niche,
                "viable": True,
                "audience_target": f"Target audience for {niche}",
                "expansion_potential": 25,
                "etsy_tags_13": [f"tag{i}" for i in range(13)],
                "selling_signals": {},
                "ai_producibility": {"score": "medium"},
                "demand": {"level": "high", "trend": "rising"},
                "competition": {"level": "medium"},
                "pricing": {"suggested_eur": 4.99},
            }
        ],
        "summary": f"E2E cluster report for {niche}",
        "ladder": {
            "tripwire_blueprint": {"title": f"{niche} Quick Sheet", "price_usd": 1.99},
            "bundle_blueprint": {"title": f"{niche} Bundle", "price_usd": 12.99, "items_included": ["a", "b"]},
        },
    }


# ---------------------------------------------------------------------------
# CL1: product_type = 'printable_pdf'
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cl1_items_have_printable_pdf_product_type(tmp_path):
    mm = _make_memory_base(tmp_path)
    await mm.init()
    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        pq = ProductionQueueService(db)
        agent = _ClusterAgent(pq, mm)
        await agent._build_cluster("CL1 Test Niche", "planners")
        cluster_id = _compute_cluster_id("CL1 Test Niche")
        items = await pq.get_cluster_items(cluster_id)
    non_pdf = [i for i in items if i.product_type != "printable_pdf"]
    assert non_pdf == [], f"Non-PDF items: {[i.product_type for i in non_pdf]}"


# ---------------------------------------------------------------------------
# CL2: Core items have release_order ∈ {2, 3, 4}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cl2_core_items_release_order_2_3_4(tmp_path):
    mm = _make_memory_base(tmp_path)
    await mm.init()
    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        pq = ProductionQueueService(db)
        agent = _ClusterAgent(pq, mm)
        await agent._build_cluster("CL2 Test Niche", "planners")
        cluster_id = _compute_cluster_id("CL2 Test Niche")
        items = await pq.get_cluster_items(cluster_id)
    core_items = [i for i in items if i.product_tier == "core"]
    core_orders = {i.release_order for i in core_items}
    expected = {2, 3, 4}
    assert core_orders == expected, f"Core release orders: {core_orders}"


# ---------------------------------------------------------------------------
# CL3: Tripwire item has release_order = 1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cl3_tripwire_item_has_release_order_1(tmp_path):
    mm = _make_memory_base(tmp_path)
    await mm.init()
    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        pq = ProductionQueueService(db)
        agent = _ClusterAgent(pq, mm)
        await agent._build_cluster("CL3 Test Niche", "planners")
        cluster_id = _compute_cluster_id("CL3 Test Niche")
        items = await pq.get_cluster_items(cluster_id)
    tripwire = [i for i in items if i.product_tier == "tripwire"]
    assert len(tripwire) == 1, f"Expected 1 tripwire, got {len(tripwire)}"
    assert tripwire[0].release_order == 1


# ---------------------------------------------------------------------------
# CL4: Bundle item has status = 'pending_approval'
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cl4_bundle_item_has_pending_approval_status(tmp_path):
    mm = _make_memory_base(tmp_path)
    await mm.init()
    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        pq = ProductionQueueService(db)
        agent = _ClusterAgent(pq, mm)
        await agent._build_cluster("CL4 Test Niche", "planners")
        cluster_id = _compute_cluster_id("CL4 Test Niche")
        items = await pq.get_cluster_items(cluster_id)
    bundle = [i for i in items if i.product_tier == "bundle"]
    assert len(bundle) == 1, f"Expected 1 bundle, got {len(bundle)}"
    assert bundle[0].status == "pending_approval", f"Bundle status: {bundle[0].status}"


# ---------------------------------------------------------------------------
# CL5: Same niche twice → items belong to single cluster_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cl5_duplicate_niche_produces_single_cluster(tmp_path):
    mm = _make_memory_base(tmp_path)
    await mm.init()
    cluster_id = _compute_cluster_id("CL5 Test Niche")
    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        pq = ProductionQueueService(db)
        agent = _ClusterAgent(pq, mm)
        await agent._build_cluster("CL5 Test Niche", "planners")
        await agent._build_cluster("CL5 Test Niche", "planners")
        items = await pq.get_cluster_items(cluster_id)
    cluster_ids = {i.cluster_id for i in items}
    assert cluster_ids == {cluster_id}, f"Multiple cluster_ids: {cluster_ids}"
    assert len(items) >= 5, f"Expected ≥ 5 items, got {len(items)}"


# ---------------------------------------------------------------------------
# CL6: _update_cluster_crossrefs skipped when < 2 published items
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cl6_crossref_skipped_when_fewer_than_2_published(tmp_path):
    from unittest.mock import MagicMock
    from apps.backend.core.production_queue import ProductionQueueItem
    from apps.backend.agents._publisher._crossref_mixin import _CrossrefMixin

    # Build one fake item with status="completed" and etsy_listing_id set
    one_item = MagicMock(spec=ProductionQueueItem)
    one_item.id = 1
    one_item.etsy_listing_id = "111"
    one_item.status = "completed"
    one_item.listing_title = "CL6 Test Listing"
    one_item.listing_description = "desc"
    one_item.cluster_id = "abc123"
    one_item.etsy_listing_url = "http://etsy.com/listing/111"
    one_item.niche = "cl6 test niche"
    one_item.product_tier = "core"
    one_item.release_order = 2

    class _FakeCrossref(_CrossrefMixin):
        def __init__(self_inner):
            self_inner.memory = MagicMock()
            self_inner.memory.mock_mode = False  # explicitly NOT mock mode
            self_inner.memory.get_db = AsyncMock(return_value=MagicMock())
            self_inner.etsy_api = MagicMock()
            self_inner.etsy_api.patch_listing_description = AsyncMock()

        async def _notify_telegram(self_inner, msg: str) -> None:
            pass

    with patch("apps.backend.agents._publisher._crossref_mixin.ProductionQueueService") as MockPQ:
        mock_pq = AsyncMock()
        mock_pq.get_cluster_items = AsyncMock(return_value=[one_item])  # only 1 published
        MockPQ.return_value = mock_pq

        xref_agent = _FakeCrossref()
        await xref_agent._update_cluster_crossrefs(
            cluster_id="abc123",
            new_listing_id="111",
            new_listing_title="CL6 Test",
            new_listing_url="http://etsy.com/listing/111",
        )
        xref_agent.etsy_api.patch_listing_description.assert_not_called()
