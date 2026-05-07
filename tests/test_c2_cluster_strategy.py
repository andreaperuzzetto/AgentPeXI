"""C.2 — Cluster Strategy: TDD test suite (written before implementation).

All tests must FAIL (RED) before C.2 is implemented.

Coverage:
  1-3   : DB migrations — cluster_id, release_order, etsy_listing_url columns
  4-8   : ProductionQueueItem cluster fields (from_row + create_item)
  9-11  : _generate_core_variations — method + output structure
  12-15 : _build_cluster — 6 items, release_order 1-6, correct tiers, bundle status
  16    : _autonomous_discovery trigger — confidence gate >= 0.75
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.core.production_queue import ProductionQueueItem, ProductionQueueService


# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------

def _make_memory_base(tmp_path):
    """Instantiate MemoryBase directly (bypasses MemoryManager monkey-patches)."""
    from apps.backend.core._memory._base import MemoryBase
    mm = MemoryBase.__new__(MemoryBase)
    mm._db_path = str(tmp_path / "test.db")
    mm._chromadb_path = str(tmp_path / "chromadb")
    mm._db = None
    mm._chroma_collection = None
    mm._screen_memory_collection = None
    mm._personal_memory_collection = None
    mm._shared_memory_collection = None
    mm._ws_broadcaster = None
    mm._bridge_callback = None
    mm.mock_mode = False
    return mm


def _fake_core_output(niche: str) -> dict:
    """Minimal AgentResult output_data representing a successful core research."""
    return {
        "niches": [
            {
                "name": niche,
                "viable": True,
                "confidence": 0.82,
                "product_type": "printable_pdf",
                "demand": {"level": "high", "trend": "rising"},
                "competition": {"level": "medium"},
                "pricing": {"suggested_eur": 4.99},
                "etsy_tags_13": ["tag1"] * 13,
                "selling_signals": {},
                "ai_producibility": {"score": "medium"},
                "audience_target": "ADHD adults",
                "expansion_potential": 25,
            }
        ],
        "summary": f"Report for {niche}",
        "ladder": {
            "tripwire_blueprint": {
                "title": f"{niche} Quick Sheet",
                "price_usd": 2.99,
            },
            "bundle_blueprint": {
                "title": f"{niche} Ultimate Bundle",
                "price_usd": 12.99,
                "items_included": ["item1", "item2"],
            },
        },
    }


def _fake_core_result(niche: str) -> AgentResult:
    return AgentResult(
        task_id="test-task",
        agent_name="research",
        status=TaskStatus.COMPLETED,
        output_data=_fake_core_output(niche),
    )


def _fake_variations(niche: str) -> list[dict]:
    return [
        {
            "variation_type": "STYLE",
            "title_hint": f"{niche} — Clean Style",
            "etsy_tags_13_delta": ["clean", "minimalist"],
            "audience_target": "adults seeking minimal design",
            "design_direction": "clean lines, white space",
        },
        {
            "variation_type": "AUDIENCE",
            "title_hint": f"{niche} — Teacher Edition",
            "etsy_tags_13_delta": ["teacher", "classroom"],
            "audience_target": "teachers",
            "design_direction": "bright, structured layout",
        },
        {
            "variation_type": "FORMAT",
            "title_hint": f"{niche} — A5 Pocket",
            "etsy_tags_13_delta": ["a5", "pocket"],
            "audience_target": "on-the-go adults",
            "design_direction": "compact, landscape",
        },
    ]


class _TestDiscoveryAgent:
    """Minimal test double for _ResearchDiscoveryMixin behavioral tests."""

    name = "research"

    def __init__(self, queue_service: ProductionQueueService):
        self.queue = queue_service
        self._telegram_markup_sender = None
        self._notified: list[str] = []

    async def _single_niche_research(
        self, task: AgentTask, niche: str
    ) -> AgentResult:
        return _fake_core_result(niche)

    async def _generate_core_variations(
        self, niche: str, core_spec: dict, n: int = 3
    ) -> list[dict]:
        return _fake_variations(niche)[:n]

    async def _notify_bundle_pending(self, *args, **kwargs) -> None:
        pass

    async def _notify_telegram(self, msg: str) -> None:
        self._notified.append(msg)

    # The actual method under test will be mixed in via _build_cluster
    async def _build_cluster(self, winner_niche: str, section_key: str) -> None:
        from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
        return await _ResearchDiscoveryMixin._build_cluster(self, winner_niche, section_key)


# ---------------------------------------------------------------------------
# Tests 1-3: DB migrations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_production_queue_has_cluster_id_after_migration(tmp_path):
    """cluster_id column must exist in production_queue after migration."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        cur = await db.execute("PRAGMA table_info(production_queue)")
        cols = {row[1] for row in await cur.fetchall()}

    assert "cluster_id" in cols, "cluster_id column missing after migration"


@pytest.mark.asyncio
async def test_production_queue_has_release_order_after_migration(tmp_path):
    """release_order column must exist in production_queue with DEFAULT 0."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        cur = await db.execute("PRAGMA table_info(production_queue)")
        rows = await cur.fetchall()
        col_names = {row[1] for row in rows}
        release_order_col = next((r for r in rows if r[1] == "release_order"), None)

    assert "release_order" in col_names, "release_order column missing after migration"
    # dflt_value is index 4 in PRAGMA table_info
    assert release_order_col[4] == "0", (
        f"Expected DEFAULT 0 for release_order, got {release_order_col[4]!r}"
    )


@pytest.mark.asyncio
async def test_production_queue_has_etsy_listing_url_after_migration(tmp_path):
    """etsy_listing_url column must exist in production_queue after migration."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        cur = await db.execute("PRAGMA table_info(production_queue)")
        cols = {row[1] for row in await cur.fetchall()}

    assert "etsy_listing_url" in cols, "etsy_listing_url column missing after migration"


# ---------------------------------------------------------------------------
# Tests 4-8: ProductionQueueItem cluster fields
# ---------------------------------------------------------------------------

# Base row for from_row tests (must match all existing fields)
_BASE_ROW_C2 = {
    "id": 1,
    "niche": "adhd planner",
    "product_type": "printable_pdf",
    "keywords": '["tag1", "tag2"]',
    "entry_score": 0.72,
    "status": "planned",
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
    "product_tier": "core",
    "loop_run_id": None,
    "created_at": 1700000000.0,
    "updated_at": 1700000000.0,
    # C.2 cluster fields (must NOT crash if absent)
}


def test_queue_item_from_row_with_cluster_id():
    """from_row must populate cluster_id when present."""
    row = dict(_BASE_ROW_C2, cluster_id="abc123def456")
    item = ProductionQueueItem.from_row(row)  # type: ignore[arg-type]
    assert item.cluster_id == "abc123def456"


def test_queue_item_from_row_cluster_id_defaults_to_none():
    """from_row must default cluster_id to None when absent."""
    item = ProductionQueueItem.from_row(_BASE_ROW_C2)  # type: ignore[arg-type]
    assert item.cluster_id is None


def test_queue_item_from_row_with_release_order():
    """from_row must populate release_order when present."""
    row = dict(_BASE_ROW_C2, release_order=3)
    item = ProductionQueueItem.from_row(row)  # type: ignore[arg-type]
    assert item.release_order == 3


def test_queue_item_from_row_release_order_defaults_to_zero():
    """from_row must default release_order to 0 when absent."""
    item = ProductionQueueItem.from_row(_BASE_ROW_C2)  # type: ignore[arg-type]
    assert item.release_order == 0


@pytest.mark.asyncio
async def test_create_item_with_cluster_id_and_release_order(tmp_path):
    """create_item must accept cluster_id and release_order, persist them."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        svc = ProductionQueueService(db)

        item_id = await svc.create_item(
            "adhd planner",
            "printable_pdf",
            ["focus", "planner"],
            cluster_id="abc123def456",
            release_order=2,
            product_tier="core",
        )

        item = await svc.get_item(item_id)

    assert item is not None
    assert item.cluster_id == "abc123def456"
    assert item.release_order == 2


# ---------------------------------------------------------------------------
# Tests 9-11: _generate_core_variations
# ---------------------------------------------------------------------------

def test_generate_core_variations_method_exists_on_discovery_mixin():
    """_generate_core_variations must be declared on _ResearchDiscoveryMixin."""
    from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
    assert callable(getattr(_ResearchDiscoveryMixin, "_generate_core_variations", None)), (
        "_ResearchDiscoveryMixin has no method _generate_core_variations"
    )


@pytest.mark.asyncio
async def test_generate_core_variations_returns_n_items(tmp_path):
    """_generate_core_variations must return a list of exactly n=3 dicts."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        svc = ProductionQueueService(db)

        agent = _TestDiscoveryAgent(svc)

        # _call_llm must be patched to avoid real network call
        from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin

        canned_llm_response = (
            '{"variations": ['
            '{"variation_type": "STYLE", "title_hint": "Clean", "etsy_tags_13_delta": [], "audience_target": "adults", "design_direction": "minimal"},'
            '{"variation_type": "AUDIENCE", "title_hint": "Teacher", "etsy_tags_13_delta": [], "audience_target": "teachers", "design_direction": "bright"},'
            '{"variation_type": "FORMAT", "title_hint": "Pocket", "etsy_tags_13_delta": [], "audience_target": "mobile", "design_direction": "compact"}'
            "]}"
        )
        agent._call_llm = AsyncMock(return_value=canned_llm_response)

        result = await _ResearchDiscoveryMixin._generate_core_variations(
            agent, "adhd planner", {"some": "spec"}, n=3
        )

    assert isinstance(result, list), f"Expected list, got {type(result)}"
    assert len(result) == 3, f"Expected 3 variations, got {len(result)}"


@pytest.mark.asyncio
async def test_generate_core_variations_items_have_required_keys(tmp_path):
    """Each variation dict must contain variation_type, title_hint, etsy_tags_13_delta,
    audience_target, design_direction."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        svc = ProductionQueueService(db)
        agent = _TestDiscoveryAgent(svc)

        from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin

        canned = (
            '{"variations": ['
            '{"variation_type": "STYLE", "title_hint": "Clean", "etsy_tags_13_delta": [], "audience_target": "adults", "design_direction": "minimal"},'
            '{"variation_type": "AUDIENCE", "title_hint": "Teacher", "etsy_tags_13_delta": [], "audience_target": "teachers", "design_direction": "bright"},'
            '{"variation_type": "FORMAT", "title_hint": "Pocket", "etsy_tags_13_delta": [], "audience_target": "mobile", "design_direction": "compact"}'
            "]}"
        )
        agent._call_llm = AsyncMock(return_value=canned)

        result = await _ResearchDiscoveryMixin._generate_core_variations(
            agent, "adhd planner", {"some": "spec"}, n=3
        )

    required_keys = {"variation_type", "title_hint", "etsy_tags_13_delta", "audience_target", "design_direction"}
    for i, var in enumerate(result):
        missing = required_keys - set(var.keys())
        assert not missing, f"Variation {i} missing keys: {missing}"


# ---------------------------------------------------------------------------
# Tests 12-15: _build_cluster
# ---------------------------------------------------------------------------

def test_build_cluster_method_exists_on_discovery_mixin():
    """_build_cluster must be declared on _ResearchDiscoveryMixin."""
    from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
    assert callable(getattr(_ResearchDiscoveryMixin, "_build_cluster", None)), (
        "_ResearchDiscoveryMixin has no method _build_cluster"
    )


@pytest.mark.asyncio
async def test_build_cluster_creates_six_production_queue_items(tmp_path):
    """_build_cluster must insert exactly 6 items in production_queue."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        svc = ProductionQueueService(db)
        agent = _TestDiscoveryAgent(svc)

        await agent._build_cluster("adhd planner", "planners_organizers")

        items = await svc.get_items_by_status("planned")
        bundle_items = await svc.get_items_by_status("pending_approval")

    total = len(items) + len(bundle_items)
    assert total == 6, f"Expected 6 cluster items, got {total}"


@pytest.mark.asyncio
async def test_build_cluster_release_orders_are_1_to_6(tmp_path):
    """Each cluster item must have a unique release_order from 1 to 6."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        svc = ProductionQueueService(db)
        agent = _TestDiscoveryAgent(svc)

        await agent._build_cluster("adhd planner", "planners_organizers")

        planned = await svc.get_items_by_status("planned")
        pending = await svc.get_items_by_status("pending_approval")
        all_items = planned + pending

    release_orders = sorted(item.release_order for item in all_items)
    assert release_orders == [1, 2, 3, 4, 5, 6], (
        f"Expected release_orders [1-6], got {release_orders}"
    )


@pytest.mark.asyncio
async def test_build_cluster_all_items_share_same_cluster_id(tmp_path):
    """All 6 cluster items must share the same cluster_id (sha256[:12] of niche)."""
    from apps.backend.agents._research.utils import _compute_cluster_id

    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        svc = ProductionQueueService(db)
        agent = _TestDiscoveryAgent(svc)

        await agent._build_cluster("adhd planner", "planners_organizers")

        planned = await svc.get_items_by_status("planned")
        pending = await svc.get_items_by_status("pending_approval")
        all_items = planned + pending

    expected_cid = _compute_cluster_id("adhd planner")
    for item in all_items:
        assert item.cluster_id == expected_cid, (
            f"Item release_order={item.release_order} has cluster_id={item.cluster_id!r}, "
            f"expected {expected_cid!r}"
        )


@pytest.mark.asyncio
async def test_build_cluster_bundle_item_has_pending_approval_status(tmp_path):
    """The bundle item (release_order=6) must have status='pending_approval'."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        svc = ProductionQueueService(db)
        agent = _TestDiscoveryAgent(svc)

        await agent._build_cluster("adhd planner", "planners_organizers")

        pending = await svc.get_items_by_status("pending_approval")

    assert len(pending) == 1, f"Expected exactly 1 pending_approval item, got {len(pending)}"
    bundle = pending[0]
    assert bundle.release_order == 6, f"Expected release_order=6 for bundle, got {bundle.release_order}"
    assert bundle.product_tier == "bundle", f"Expected product_tier='bundle', got {bundle.product_tier!r}"


# ---------------------------------------------------------------------------
# Test 16: _autonomous_discovery trigger
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_autonomous_discovery_calls_build_cluster_when_high_confidence(tmp_path):
    """_autonomous_discovery must call _build_cluster when winner.confidence >= 0.75."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        db.row_factory = aiosqlite.Row
        svc = ProductionQueueService(db)

        from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin

        # Minimal agent with all async deps mocked
        agent = MagicMock()
        agent.name = "research"
        agent.queue = svc
        agent.memory = MagicMock()
        agent.memory.mock_mode = False

        # _mine_opportunity_candidates returns 1 candidate
        agent._mine_opportunity_candidates = AsyncMock(return_value=[
            {"niche": "adhd planner", "product_type": "printable_pdf", "source": "test", "entry_score": 0.8}
        ])
        # Skip EntryPointScoring (raise to use raw candidates)
        agent._get_entry_point_scorer = AsyncMock(side_effect=Exception("skip scorer"))

        # Haiku analysis returns high-confidence result for winner
        agent.spawn_subagent = AsyncMock(return_value=AgentResult(
            task_id="t",
            agent_name="research",
            status=TaskStatus.COMPLETED,
            output_data={
                "niches": [{"name": "adhd planner", "viable": True, "confidence": 0.82,
                            "recommended_product_type": "printable_pdf",
                            "_candidate_product_type": "printable_pdf",
                            "_candidate_source": "test"}]
            },
        ))

        # Sonnet synthesis returns winner with confidence >= 0.75
        agent._call_llm = AsyncMock(return_value=(
            '{"winner": {"niche": "adhd planner", "product_type": "printable_pdf", '
            '"why_winner": "test", "confidence": 0.82, '
            '"brief": {"template": null, "art_type": null, "etsy_tags_13": [], '
            '"selling_signals": {}, "pricing": {}, "keywords": [], "color_palette_hint": ""}}, '
            '"runner_up": {"niche": "other", "product_type": "printable_pdf", "why": "x"}, '
            '"summary": "test", "candidates_analyzed": 1, "candidates_viable": 1}'
        ))
        agent._try_parse_json = lambda s: __import__("json").loads(s)
        agent._build_market_context = MagicMock(return_value="")
        agent._notify_telegram = AsyncMock()
        agent._log_step = AsyncMock()
        agent._call_tool = AsyncMock()

        # _build_cluster is what we're testing gets called
        agent._build_cluster = AsyncMock()

        task = AgentTask(agent_name="research", input_data={}, source="test")
        await _ResearchDiscoveryMixin._autonomous_discovery(agent, task)

    agent._build_cluster.assert_called_once()
    call_args = agent._build_cluster.call_args
    assert call_args[0][0] == "adhd planner", (
        f"_build_cluster called with wrong niche: {call_args[0][0]!r}"
    )
