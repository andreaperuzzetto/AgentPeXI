"""C.3 — Shop-level Competitive Analysis: TDD test suite (written before implementation).

All tests must FAIL (RED) before C.3 is implemented.

Coverage:
  1   : cache hit — 0 Tavily calls when analysis < 30 days
  2   : cache miss + empty research → discovery Tavily called
  3   : < 3 shops in research cache → discovery Tavily called
  4   : ≥ 3 shops in research cache → discovery Tavily NOT called
  5   : output shops list has max 5 items (trimmed at 5)
  6   : gap_to_exploit is a non-empty string
  7   : ChromaDB stored with metadata type="competitor_shop_analysis"
  8   : ChromaDB cache_until is ~30 days from now
  9   : _single_niche_research output contains "competitor_shop_analysis"
  10  : API GET /niches/{niche}/competitor-analysis returns available=True with cache hit
  11  : API returns {"available": false} when no ChromaDB cache
  12  : mock_mode=True → returns None, no Tavily calls
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_shop_agent(mock_mode: bool = False) -> MagicMock:
    """Minimal stand-in for MarketDataAgent with _ShopAnalysisMixin."""
    agent = MagicMock()
    agent._mock = mock_mode
    agent._memory = MagicMock()
    agent._memory.query_chromadb = AsyncMock(return_value=[])
    agent._memory.store_insight = AsyncMock(return_value="fake-id")
    # _call_haiku_shop_analysis and _synthesize_shop_gaps are the two
    # internal helpers — mock them so tests stay fast and deterministic
    agent._call_haiku_shop_analysis = AsyncMock(
        return_value={
            "shop_name": "FakeShop",
            "estimated_listing_count": 50,
            "primary_niches": ["adhd planner"],
            "section_structure": "planners",
            "estimated_aov_usd": 8.0,
            "audience_served": "ADHD adults",
            "what_they_do_well": "clean layout",
            "what_they_dont_do": "no bundles",
            "threat_level": "medium",
        }
    )
    agent._synthesize_shop_gaps = AsyncMock(
        return_value="Gap: no audience-specific ADHD bundle under $5"
    )
    return agent


def _valid_cache_entry(niche: str, days_until_expiry: int = 25) -> list[dict]:
    """Simulate a non-expired ChromaDB cache entry."""
    cache_until = (datetime.now(timezone.utc) + timedelta(days=days_until_expiry)).isoformat()
    cached_result = {
        "niche": niche,
        "section_key": "planners_organizers",
        "shops_analyzed": 2,
        "shops": [],
        "gap_to_exploit": "Cached gap: no bundle",
        "gap_summary": "Cached gap: no bundle",
    }
    return [
        {
            "document": json.dumps(cached_result),
            "metadata": {
                "type": "competitor_shop_analysis",
                "niche": niche,
                "cache_until": cache_until,
            },
            "id": "fake-id",
        }
    ]


def _research_cache_entry(niche: str, top_sellers: list[str]) -> list[dict]:
    """Simulate a ChromaDB research_report entry with given top_sellers."""
    doc = {
        "niches": [
            {
                "name": niche,
                "competition": {"top_sellers": top_sellers},
            }
        ]
    }
    return [{"document": json.dumps(doc), "metadata": {"type": "research_report", "niche": niche}, "id": "r1"}]


# ---------------------------------------------------------------------------
# Import the mixin under test (will fail RED until file exists)
# ---------------------------------------------------------------------------

from apps.backend.agents._market_data._shop_analysis_mixin import _ShopAnalysisMixin  # noqa: E402


# ---------------------------------------------------------------------------
# Tests 1–8: _get_competitor_shop_analysis unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_skips_all_tavily():
    """Test 1: When cache is valid, no Tavily calls are made and cached result is returned."""
    niche = "adhd planner"
    agent = _make_shop_agent()
    agent._memory.query_chromadb = AsyncMock(return_value=_valid_cache_entry(niche))

    with patch("apps.backend.agents._market_data._shop_analysis_mixin.tavily_tool") as mock_tavily:
        result = await _ShopAnalysisMixin._get_competitor_shop_analysis(agent, niche, "planners_organizers")

    mock_tavily.search.assert_not_called()
    assert result is not None
    assert result["niche"] == niche
    assert result["gap_to_exploit"] == "Cached gap: no bundle"


@pytest.mark.asyncio
async def test_cache_miss_empty_research_calls_discovery_tavily():
    """Test 2: Cache miss + empty research cache → discovery Tavily called for shop names."""
    niche = "boho wedding"
    agent = _make_shop_agent()
    # Both cache check and research_report return empty
    agent._memory.query_chromadb = AsyncMock(return_value=[])

    with patch("apps.backend.agents._market_data._shop_analysis_mixin.tavily_tool") as mock_tavily:
        mock_tavily.search = AsyncMock(return_value={"results": [
            {"url": "https://www.etsy.com/shop/BohoShop"},
            {"url": "https://www.etsy.com/shop/WeddingPrints"},
            {"url": "https://www.etsy.com/shop/BlushDesigns"},
        ]})
        result = await _ShopAnalysisMixin._get_competitor_shop_analysis(agent, niche, "")

    # Discovery tavily should have been called at least once
    assert mock_tavily.search.called
    discovery_calls = [
        c for c in mock_tavily.search.call_args_list
        if "top sellers" in str(c)
    ]
    assert len(discovery_calls) >= 1


@pytest.mark.asyncio
async def test_few_top_sellers_in_research_triggers_discovery_tavily():
    """Test 3: Research cache has < 3 top_sellers → discovery Tavily is called."""
    niche = "minimalist wall art"
    agent = _make_shop_agent()
    # First call (cache check) → empty; second call (research_report) → 2 shops
    agent._memory.query_chromadb = AsyncMock(side_effect=[
        [],  # cache check: no hit
        _research_cache_entry(niche, ["ShopA", "ShopB"]),  # research report: 2 shops
    ])

    tavily_calls: list[str] = []

    async def fake_tavily_search(**kwargs):
        tavily_calls.append(kwargs.get("query", ""))
        return {"results": []}

    with patch("apps.backend.agents._market_data._shop_analysis_mixin.tavily_tool") as mock_tavily:
        mock_tavily.search = AsyncMock(side_effect=fake_tavily_search)
        await _ShopAnalysisMixin._get_competitor_shop_analysis(agent, niche, "")

    discovery_calls = [q for q in tavily_calls if "top sellers" in q]
    assert len(discovery_calls) >= 1, "Discovery Tavily not called despite < 3 shops"


@pytest.mark.asyncio
async def test_many_top_sellers_in_research_skips_discovery_tavily():
    """Test 4: Research cache has ≥ 3 top_sellers → no discovery Tavily (only per-shop Tavily)."""
    niche = "baby shower invitation"
    agent = _make_shop_agent()
    agent._memory.query_chromadb = AsyncMock(side_effect=[
        [],  # cache check: no hit
        _research_cache_entry(niche, ["ShopA", "ShopB", "ShopC"]),  # 3 shops
    ])

    tavily_calls: list[str] = []

    async def fake_tavily_search(**kwargs):
        tavily_calls.append(kwargs.get("query", ""))
        return {"results": []}

    with patch("apps.backend.agents._market_data._shop_analysis_mixin.tavily_tool") as mock_tavily:
        mock_tavily.search = AsyncMock(side_effect=fake_tavily_search)
        await _ShopAnalysisMixin._get_competitor_shop_analysis(agent, niche, "")

    discovery_calls = [q for q in tavily_calls if "top sellers" in q]
    assert len(discovery_calls) == 0, f"Discovery Tavily called despite ≥ 3 shops: {discovery_calls}"


@pytest.mark.asyncio
async def test_output_max_5_shops():
    """Test 5: Even if research cache has 7 shops, output shops list is capped at 5."""
    niche = "resume template"
    agent = _make_shop_agent()
    shops_7 = [f"Shop{i}" for i in range(7)]
    agent._memory.query_chromadb = AsyncMock(side_effect=[
        [],  # cache check: no hit
        _research_cache_entry(niche, shops_7),  # 7 shops
    ])

    with patch("apps.backend.agents._market_data._shop_analysis_mixin.tavily_tool") as mock_tavily:
        mock_tavily.search = AsyncMock(return_value={"results": []})
        result = await _ShopAnalysisMixin._get_competitor_shop_analysis(agent, niche, "")

    assert result is not None
    assert len(result["shops"]) <= 5


@pytest.mark.asyncio
async def test_gap_to_exploit_is_non_empty():
    """Test 6: gap_to_exploit in result is a non-empty string."""
    niche = "adhd planner"
    agent = _make_shop_agent()
    agent._memory.query_chromadb = AsyncMock(side_effect=[
        [],  # no cache
        _research_cache_entry(niche, ["ShopA", "ShopB", "ShopC"]),
    ])

    with patch("apps.backend.agents._market_data._shop_analysis_mixin.tavily_tool") as mock_tavily:
        mock_tavily.search = AsyncMock(return_value={"results": []})
        result = await _ShopAnalysisMixin._get_competitor_shop_analysis(agent, niche, "planners_organizers")

    assert result is not None
    assert len(result.get("gap_to_exploit", "")) > 0


@pytest.mark.asyncio
async def test_chromadb_stored_with_type_metadata():
    """Test 7: After analysis, store_insight is called with metadata type='competitor_shop_analysis'."""
    niche = "halloween party"
    agent = _make_shop_agent()
    agent._memory.query_chromadb = AsyncMock(side_effect=[
        [],
        _research_cache_entry(niche, ["ShopA", "ShopB", "ShopC"]),
    ])

    with patch("apps.backend.agents._market_data._shop_analysis_mixin.tavily_tool") as mock_tavily:
        mock_tavily.search = AsyncMock(return_value={"results": []})
        await _ShopAnalysisMixin._get_competitor_shop_analysis(agent, niche, "party_celebrations")

    agent._memory.store_insight.assert_called_once()
    call_kwargs = agent._memory.store_insight.call_args
    metadata = call_kwargs[1].get("metadata") or call_kwargs[0][1]
    assert metadata["type"] == "competitor_shop_analysis"
    assert metadata["niche"] == niche


@pytest.mark.asyncio
async def test_chromadb_cache_until_30_days_from_now():
    """Test 8: cache_until in stored metadata is ~30 days from now (within ±1 day tolerance)."""
    niche = "wedding planner printable"
    agent = _make_shop_agent()
    agent._memory.query_chromadb = AsyncMock(side_effect=[
        [],
        _research_cache_entry(niche, ["ShopA", "ShopB", "ShopC"]),
    ])

    with patch("apps.backend.agents._market_data._shop_analysis_mixin.tavily_tool") as mock_tavily:
        mock_tavily.search = AsyncMock(return_value={"results": []})
        await _ShopAnalysisMixin._get_competitor_shop_analysis(agent, niche, "")

    call_kwargs = agent._memory.store_insight.call_args
    metadata = call_kwargs[1].get("metadata") or call_kwargs[0][1]
    cache_until = datetime.fromisoformat(metadata["cache_until"])
    if cache_until.tzinfo is None:
        cache_until = cache_until.replace(tzinfo=timezone.utc)
    delta = cache_until - datetime.now(timezone.utc)
    assert 29 <= delta.days <= 31, f"cache_until delta={delta.days} days, expected ~30"


# ---------------------------------------------------------------------------
# Test 9: Integration — _single_niche_research includes competitor_shop_analysis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_niche_research_includes_competitor_shop_analysis():
    """Test 9: When _single_niche_research runs, output_data contains 'competitor_shop_analysis'."""
    from apps.backend.agents._research.analysis_mixin import _ResearchAnalysisMixin
    from apps.backend.core.models import AgentTask, TaskStatus

    FAKE_SHOP_ANALYSIS = {
        "niche": "adhd planner",
        "section_key": "planners_organizers",
        "shops_analyzed": 3,
        "shops": [],
        "gap_to_exploit": "No ADHD bundle under $5",
        "gap_summary": "No ADHD bundle under $5",
    }

    FAKE_LLM_OUTPUT = json.dumps({
        "niches": [{
            "name": "adhd planner",
            "viable": True,
            "viability_reason": "high demand",
            "demand": {"level": "high", "trend": "rising", "seasonality": "n/a",
                       "peak_months": [9], "publish_timing_advice": "now"},
            "competition": {"level": "medium", "top_sellers": ["ShopA"],
                            "avg_quality": "medium", "what_top_sellers_do": "clean",
                            "gap_to_exploit": "no bundles"},
            "pricing": {"min_usd": 3.0, "max_usd": 9.0, "avg_usd": 6.0,
                        "conversion_sweet_spot_usd": 5.9,
                        "launch_price_usd": 4.9, "mature_price_usd": 6.9,
                        "price_reasoning": "mid-range"},
            "keywords": ["adhd", "planner"],
            "etsy_tags_13": [f"tag{i}" for i in range(13)],
            "tag_strategy": "standard",
            "recommended_product_type": "printable_pdf",
            "product_format_details": "A4",
            "entry_difficulty": "medium",
            "selling_signals": {
                "thumbnail_style": "mockup",
                "conversion_triggers": ["price"],
                "bundle_vs_single": "single",
                "bundle_reasoning": "",
                "first_listing_recommendation": "adhd daily planner",
            },
            "failure_analysis_applied": {"failures_found": 0, "actions_taken": [], "avoided": []},
            "notes": "",
            "ai_producibility": {"score": "high", "reason": "standard PDF"},
            "audience_target": "ADHD adults",
            "expansion_potential": 25,
            "ladder": {
                "tripwire": {"product_type": "printable_pdf", "price_eur": 1.99, "description": "1-page"},
                "core": {"product_type": "printable_pdf", "price_eur": 6.99, "description": "full pack"},
                "bundle": {"product_type": "printable_pdf", "price_eur": 14.99, "description": "bundle"},
            },
        }],
        "summary": "adhd planner is a strong pick",
        "recommended_next_steps": [],
        "data_quality_warning": "",
    })

    agent = MagicMock()
    agent.name = "research"
    agent.model = "claude-haiku"
    agent.memory = MagicMock()
    agent.memory.query_chromadb = AsyncMock(return_value=[])
    agent.memory.query_chromadb_recent = AsyncMock(return_value=[])
    agent.memory.store_insight = AsyncMock(return_value="fake-id")
    agent._task_id = "test"
    agent._call_llm = AsyncMock(return_value=FAKE_LLM_OUTPUT)
    agent._log_step = AsyncMock()
    agent._call_tool = AsyncMock(return_value={})
    agent._read_finance_context = AsyncMock(return_value="")
    agent._read_shared_context = AsyncMock(return_value="")
    agent._calculate_confidence = MagicMock(return_value=(0.85, []))
    agent._notify_bundle_pending = AsyncMock()

    task = AgentTask(
        agent_name="research",
        input_data={"niches": ["adhd planner"], "section_key": "planners_organizers"},
        source="test",
    )

    with patch(
        "apps.backend.agents._research.analysis_mixin.MarketDataAgent"
    ) as MockMDA:
        mock_mda_instance = MagicMock()
        mock_mda_instance._get_competitor_shop_analysis = AsyncMock(return_value=FAKE_SHOP_ANALYSIS)
        MockMDA.return_value = mock_mda_instance

        result = await _ResearchAnalysisMixin._single_niche_research(agent, task, "adhd planner")

    assert result.status == TaskStatus.COMPLETED
    assert "competitor_shop_analysis" in (result.output_data or {}), (
        "competitor_shop_analysis missing from _single_niche_research output"
    )
    assert result.output_data["competitor_shop_analysis"]["shops_analyzed"] == 3


# ---------------------------------------------------------------------------
# Tests 10–11: API endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_competitor_analysis_returns_cached():
    """Test 10: GET /api/etsy/niches/{niche}/competitor-analysis returns available=True when cached."""
    from apps.backend.api.routers.etsy import get_niche_competitor_analysis

    cached_analysis = {
        "niche": "adhd planner",
        "shops_analyzed": 3,
        "gap_to_exploit": "No ADHD bundle",
    }
    mock_memory = MagicMock()
    mock_memory.query_chromadb = AsyncMock(return_value=[
        {
            "document": json.dumps(cached_analysis),
            "metadata": {"type": "competitor_shop_analysis"},
            "id": "cid1",
        }
    ])

    response = await get_niche_competitor_analysis("adhd planner", mock_memory)

    assert response["available"] is True
    assert response["niche"] == "adhd planner"
    assert response["analysis"]["shops_analyzed"] == 3


@pytest.mark.asyncio
async def test_api_competitor_analysis_returns_unavailable_when_no_cache():
    """Test 11: GET /api/etsy/niches/{niche}/competitor-analysis returns available=False if not cached."""
    from apps.backend.api.routers.etsy import get_niche_competitor_analysis

    mock_memory = MagicMock()
    mock_memory.query_chromadb = AsyncMock(return_value=[])

    response = await get_niche_competitor_analysis("unknown niche", mock_memory)

    assert response["available"] is False
    assert response["niche"] == "unknown niche"


# ---------------------------------------------------------------------------
# Test 12: mock_mode graceful skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_mode_returns_none_without_tavily():
    """Test 12: In mock_mode=True, _get_competitor_shop_analysis returns None with no Tavily calls."""
    niche = "boho wedding"
    agent = _make_shop_agent(mock_mode=True)

    with patch("apps.backend.agents._market_data._shop_analysis_mixin.tavily_tool") as mock_tavily:
        result = await _ShopAnalysisMixin._get_competitor_shop_analysis(agent, niche, "")

    assert result is None
    mock_tavily.search.assert_not_called()
