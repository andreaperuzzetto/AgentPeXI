"""Tests for WarmupOrchestratorMixin (PA-2)."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from apps.backend.agents._research.warmup_mixin import (
    WarmupOrchestratorMixin,
    _infer_product_type,
)


# ---------------------------------------------------------------------------
# _infer_product_type
# ---------------------------------------------------------------------------

def test_infer_product_type_wall_art():
    assert _infer_product_type("modern wall art for living room") == "digital_art_png"

def test_infer_product_type_coloring():
    assert _infer_product_type("coloring pages for toddler girls") == "digital_art_png"

def test_infer_product_type_abc_learning():
    assert _infer_product_type("ABC learning printable for preschool") == "digital_art_png"

def test_infer_product_type_educational_worksheet():
    assert _infer_product_type("educational worksheet for ADHD kids") == "digital_art_png"

def test_infer_product_type_art_print():
    assert _infer_product_type("art print for kitchen") == "digital_art_png"

def test_infer_product_type_default_printable():
    assert _infer_product_type("wedding invitation printable for boho brides") == "printable_pdf"

def test_infer_product_type_planner():
    assert _infer_product_type("ADHD daily planner printable for adults") == "printable_pdf"

def test_infer_product_type_case_insensitive():
    assert _infer_product_type("Wall Art PRINT for kitchen") == "digital_art_png"


# ---------------------------------------------------------------------------
# Data integrity: _DISCOVERY_CATEGORIES_BY_SECTION
# ---------------------------------------------------------------------------

def test_section_count():
    assert len(WarmupOrchestratorMixin._DISCOVERY_CATEGORIES_BY_SECTION) == 4

def test_each_section_has_6_queries():
    for section, queries in WarmupOrchestratorMixin._DISCOVERY_CATEGORIES_BY_SECTION.items():
        assert len(queries) == 6, f"Section {section!r} has {len(queries)} queries, expected 6"

def test_total_queries_count():
    total = sum(
        len(q) for q in WarmupOrchestratorMixin._DISCOVERY_CATEGORIES_BY_SECTION.values()
    )
    assert total == 24

def test_expected_sections_present():
    keys = set(WarmupOrchestratorMixin._DISCOVERY_CATEGORIES_BY_SECTION.keys())
    assert keys == {"party_celebrations", "wellness_selfcare", "planners_organizers", "kids_learning"}

def test_queries_are_non_empty_strings():
    for section, queries in WarmupOrchestratorMixin._DISCOVERY_CATEGORIES_BY_SECTION.items():
        for q in queries:
            assert isinstance(q, str) and q.strip(), f"Empty query in section {section!r}"


# ---------------------------------------------------------------------------
# ResearchAgent has section_sweep (integration smoke test)
# ---------------------------------------------------------------------------

def test_research_agent_has_section_sweep():
    from apps.backend.agents.research import ResearchAgent
    assert hasattr(ResearchAgent, "section_sweep")
    assert callable(ResearchAgent.section_sweep)


def test_research_agent_mro_order():
    """WarmupOrchestratorMixin should appear after _ResearchDiscoveryMixin in MRO."""
    from apps.backend.agents.research import ResearchAgent
    mro_names = [c.__name__ for c in ResearchAgent.__mro__]
    assert "WarmupOrchestratorMixin" in mro_names
    assert "ResearchAgent" in mro_names
    disc_idx = mro_names.index("_ResearchDiscoveryMixin")
    warm_idx = mro_names.index("WarmupOrchestratorMixin")
    assert disc_idx < warm_idx, "WarmupOrchestratorMixin should come after _ResearchDiscoveryMixin"


# ---------------------------------------------------------------------------
# section_sweep behaviour
# ---------------------------------------------------------------------------

class _MockAgent(WarmupOrchestratorMixin):
    """Minimal concrete class to test the mixin in isolation."""

    async def _call_tool(self, *, tool_name, action, input_params, fn, **kwargs):
        """Minimal _call_tool stub: delegates to fn(**kwargs)."""
        if asyncio.iscoroutinefunction(fn):
            return await fn(**kwargs)
        return fn(**kwargs)


@pytest.fixture
def agent():
    return _MockAgent()


@pytest.mark.asyncio
async def test_section_sweep_unknown_section_returns_empty(agent):
    result = await agent.section_sweep("nonexistent_section")
    assert result == []


@pytest.mark.asyncio
async def test_section_sweep_calls_all_6_queries(agent):
    """section_sweep should call _research_audience_query once per query in section."""
    called = []
    original = agent._research_audience_query

    async def spy(query, section_key):
        called.append(query)
        return [{"niche": query, "product_type": "printable_pdf", "source": "warmup_test", "section": section_key}]

    agent._research_audience_query = spy  # type: ignore[assignment]
    await agent.section_sweep("planners_organizers")
    assert len(called) == 6


@pytest.mark.asyncio
async def test_section_sweep_deduplicates_candidates(agent):
    """Duplicate niche:product_type pairs should appear only once."""
    async def _dup_research(query, section_key):
        # Every query returns the same niche
        return [
            {"niche": "duplicate niche", "product_type": "printable_pdf", "source": "s", "section": section_key},
            {"niche": "duplicate niche", "product_type": "printable_pdf", "source": "s2", "section": section_key},
        ]

    agent._research_audience_query = _dup_research  # type: ignore[assignment]
    result = await agent.section_sweep("planners_organizers", top_k=100)
    niches = [r["niche"] for r in result]
    assert niches.count("duplicate niche") == 1


@pytest.mark.asyncio
async def test_section_sweep_respects_top_k(agent):
    async def _many_candidates(query, section_key):
        return [
            {"niche": f"{query}-{i}", "product_type": "printable_pdf", "source": "s", "section": section_key}
            for i in range(3)
        ]

    agent._research_audience_query = _many_candidates  # type: ignore[assignment]
    result = await agent.section_sweep("planners_organizers", top_k=5)
    assert len(result) <= 5


@pytest.mark.asyncio
async def test_section_sweep_tolerates_query_failure(agent):
    """A failure in one query should not abort the whole sweep."""
    call_count = 0

    async def _flaky(query, section_key):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise RuntimeError("simulated failure")
        return [{"niche": query, "product_type": "printable_pdf", "source": "s", "section": section_key}]

    agent._research_audience_query = _flaky  # type: ignore[assignment]
    result = await agent.section_sweep("planners_organizers", top_k=10)
    # Should return results from the non-failing queries, not raise
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_section_sweep_filters_empty_niche(agent):
    async def _empty_niche(query, section_key):
        return [{"niche": "   ", "product_type": "printable_pdf", "source": "s", "section": section_key}]

    agent._research_audience_query = _empty_niche  # type: ignore[assignment]
    result = await agent.section_sweep("planners_organizers")
    assert result == []


# ---------------------------------------------------------------------------
# _research_audience_query
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_research_audience_query_returns_candidate_on_success(agent):
    with (
        patch("apps.backend.agents._research.warmup_mixin.tavily_tool") as mock_tavily,
        patch("apps.backend.agents._research.warmup_mixin.get_google_trends") as mock_trends,
    ):
        mock_tavily.search = AsyncMock(return_value={"results": []})
        mock_trends.return_value = {"percent_change": 0}

        results = await agent._research_audience_query(
            "wedding invitation printable for boho brides", "party_celebrations"
        )
        assert any(r["niche"] == "wedding invitation printable for boho brides" for r in results)


@pytest.mark.asyncio
async def test_research_audience_query_trending_adds_extra_candidate(agent):
    with (
        patch("apps.backend.agents._research.warmup_mixin.tavily_tool") as mock_tavily,
        patch("apps.backend.agents._research.warmup_mixin.get_google_trends") as mock_trends,
    ):
        mock_tavily.search = AsyncMock(return_value={"results": []})
        mock_trends.return_value = {"percent_change": 50}  # > 10 → trending

        results = await agent._research_audience_query(
            "wedding invitation printable for boho brides", "party_celebrations"
        )
        sources = [r["source"] for r in results]
        assert any("trending" in s for s in sources)


@pytest.mark.asyncio
async def test_research_audience_query_trending_no_duplicate_niche(agent):
    """M3: quando Tavily succede e trending > 10%, deve essere restituito
    UN SOLO entry per la niche con source _trending.

    Due entry con la stessa niche_key causano la perdita del trending tag
    nel dedup di section_sweep (che conserva solo il primo, non-trending).
    """
    with (
        patch("apps.backend.agents._research.warmup_mixin.tavily_tool") as mock_tavily,
        patch("apps.backend.agents._research.warmup_mixin.get_google_trends") as mock_trends,
    ):
        mock_tavily.search = AsyncMock(return_value={"results": []})
        mock_trends.return_value = {"percent_change": 50}  # > 10 → trending

        results = await agent._research_audience_query(
            "boho wedding invitation", "party_celebrations"
        )
        niches = [r["niche"] for r in results]
        assert niches.count("boho wedding invitation") == 1, (
            f"Got {niches.count('boho wedding invitation')} entries for same niche "
            "— section_sweep dedup would drop the trending tag"
        )
        sources = [r["source"] for r in results]
        assert any("trending" in s for s in sources), (
            "The surviving entry must carry the _trending source tag"
        )


@pytest.mark.asyncio
async def test_research_audience_query_tavily_failure_returns_empty(agent):
    with (
        patch("apps.backend.agents._research.warmup_mixin.tavily_tool") as mock_tavily,
        patch("apps.backend.agents._research.warmup_mixin.get_google_trends") as mock_trends,
    ):
        mock_tavily.search = AsyncMock(side_effect=RuntimeError("tavily down"))
        mock_trends.return_value = {"percent_change": 0}

        results = await agent._research_audience_query("some query", "planners_organizers")
        # Tavily failed → query itself NOT added as candidate
        assert results == [] or all(r["source"] != "warmup_planners_organizers" for r in results)
