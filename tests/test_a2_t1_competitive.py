"""Tests for _competitive_mixin.py — shop_competitive_analysis()."""
import pytest
from unittest.mock import AsyncMock
from apps.backend.agents.market_data import MarketDataAgent
from apps.backend.core.memory import MemoryManager


@pytest.fixture
def mock_memory():
    mem = AsyncMock(spec=MemoryManager)
    return mem


@pytest.mark.asyncio
async def test_competitive_analysis_party(mock_memory):
    agent = MarketDataAgent(memory=mock_memory, mock_mode=True)
    result = await agent.shop_competitive_analysis("party_celebrations")
    assert result["section_key"] == "party_celebrations"
    assert "color_palette" in result
    assert len(result["color_palette"]) == 3
    assert "style_keywords" in result
    assert len(result["style_keywords"]) >= 3
    assert "mockup_style" in result
    assert result["mockup_style"] in ("flat_lay", "lifestyle")
    assert "avg_price_range" in result


@pytest.mark.asyncio
async def test_competitive_analysis_all_sections(mock_memory):
    agent = MarketDataAgent(memory=mock_memory, mock_mode=True)
    sections = ["party_celebrations", "wellness_self_care", "planners_organizers", "kids_learning"]
    for section_key in sections:
        result = await agent.shop_competitive_analysis(section_key)
        assert result["section_key"] == section_key


@pytest.mark.asyncio
async def test_competitive_analysis_unknown_section(mock_memory):
    agent = MarketDataAgent(memory=mock_memory, mock_mode=True)
    result = await agent.shop_competitive_analysis("unknown_section")
    # Should return a generic fallback, not raise
    assert result["section_key"] == "unknown_section"
    assert "color_palette" in result


@pytest.mark.asyncio
async def test_analyze_all_sections(mock_memory):
    agent = MarketDataAgent(memory=mock_memory, mock_mode=True)
    all_signals = await agent.analyze_all_sections()
    assert len(all_signals) == 4
    assert all(s["section_key"] for s in all_signals)
