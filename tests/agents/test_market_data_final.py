"""Coverage tests for _collection_mixin.py — RF-FINAL-C.

Targets uncovered lines: 28-29 (cache HIT), 106-107 (search Exception),
110-111 (autocomplete Exception), 143-145 (_real_tier2 happy path).
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from apps.backend.agents._market_data._collection_mixin import _CollectionMixin
from apps.backend.agents._market_data._storage_mixin import _StorageMixin
from apps.backend.agents._market_data._models import MarketSignals


# ---------------------------------------------------------------------------
# Minimal fake agent — only _CollectionMixin; all deps are mocked attributes
# ---------------------------------------------------------------------------

class _FakeAgent(_CollectionMixin):
    """Minimal stub combining _CollectionMixin with mocked collaborators."""

    _mock = False

    def __init__(self) -> None:
        self._client = None

        # _StorageMixin collaborators
        self.get_cached_signals = AsyncMock(return_value=None)
        self._save_signals = AsyncMock()

        # _SearchMixin collaborators
        self._search_etsy_listings = AsyncMock(
            return_value={"count": 100, "avg_reviews": 20.0, "avg_price_eur": 18.5}
        )
        self._get_autocomplete = AsyncMock(
            return_value=["ceramic mug art", "ceramic mug set"]
        )

        # _ScoringMixin collaborators
        self._get_seasonal_boost = MagicMock(return_value=1.0)
        self._compute_entry_score = MagicMock(return_value=0.6)

    # Borrow the real static method from _StorageMixin
    _dict_to_signals = staticmethod(_StorageMixin._dict_to_signals)


def _make_agent() -> _FakeAgent:
    return _FakeAgent()


# ---------------------------------------------------------------------------
# Helper: a minimal cached-signals dict
# ---------------------------------------------------------------------------

_CACHED_DICT: dict = {
    "niche": "ceramic mug",
    "product_type": None,
    "etsy_result_count": 50,
    "avg_reviews": 8.0,
    "avg_price_eur": 22.0,
    "autocomplete_hits": 2,
    "google_trend_score": 0.0,
    "erank_search_volume": 0,
    "entry_score": 0.65,
    "seasonal_boost": 1.0,
    "tier": 1,
    "collected_at": 1_700_000_000.0,
}


# ===========================================================================
# Lines 28-29  — cache HIT branch
# ===========================================================================

@pytest.mark.asyncio
async def test_collect_tier1_cache_hit_returns_signals():
    """get_cached_signals returns data → _dict_to_signals called, returns immediately."""
    agent = _make_agent()
    fake_signals = MarketSignals(niche="ceramic mug")
    agent.get_cached_signals = AsyncMock(return_value=_CACHED_DICT)
    agent._dict_to_signals = MagicMock(return_value=fake_signals)

    result = await asyncio.wait_for(agent.collect_tier1("ceramic mug"), timeout=5)

    assert result is fake_signals
    agent._dict_to_signals.assert_called_once_with(_CACHED_DICT)


@pytest.mark.asyncio
async def test_collect_tier1_cache_hit_no_etsy_calls():
    """When cache HIT, Etsy search and autocomplete are never called."""
    agent = _make_agent()
    agent.get_cached_signals = AsyncMock(return_value=_CACHED_DICT)
    agent._dict_to_signals = MagicMock(return_value=MarketSignals(niche="ceramic mug"))

    await asyncio.wait_for(agent.collect_tier1("ceramic mug"), timeout=5)

    agent._search_etsy_listings.assert_not_called()
    agent._get_autocomplete.assert_not_called()


@pytest.mark.asyncio
async def test_collect_tier1_force_refresh_bypasses_cache():
    """force_refresh=True skips cache check even when data is available."""
    agent = _make_agent()
    agent.get_cached_signals = AsyncMock(return_value=_CACHED_DICT)
    agent._mock = True
    agent._mock_tier1 = MagicMock(return_value=MarketSignals(niche="ceramic mug"))

    await asyncio.wait_for(
        agent.collect_tier1("ceramic mug", force_refresh=True), timeout=5
    )

    # Cache should NOT have been consulted
    agent.get_cached_signals.assert_not_called()


@pytest.mark.asyncio
async def test_collect_tier1_cache_miss_proceeds_to_real_tier1():
    """When get_cached_signals returns None, the real collection path runs."""
    agent = _make_agent()
    agent.get_cached_signals = AsyncMock(return_value=None)

    result = await asyncio.wait_for(agent.collect_tier1("ceramic mug"), timeout=5)

    assert isinstance(result, MarketSignals)
    assert result.niche == "ceramic mug"
    agent._search_etsy_listings.assert_called_once()


# ===========================================================================
# Lines 106-107 — search_data is Exception (asyncio.gather return_exceptions)
# ===========================================================================

@pytest.mark.asyncio
async def test_real_tier1_search_exception_fallback_count_zero():
    """_search_etsy_listings raises → etsy_result_count fallback to 0."""
    agent = _make_agent()
    agent._search_etsy_listings = AsyncMock(side_effect=Exception("Etsy 503"))

    result = await asyncio.wait_for(agent._real_tier1("ceramic mug", None), timeout=5)

    assert isinstance(result, MarketSignals)
    assert result.etsy_result_count == 0
    assert result.avg_reviews == 0.0
    assert result.avg_price_eur == 0.0


@pytest.mark.asyncio
async def test_real_tier1_search_exception_autocomplete_still_counted():
    """When search fails but autocomplete succeeds, ac hits are still counted."""
    agent = _make_agent()
    agent._search_etsy_listings = AsyncMock(side_effect=Exception("timeout"))
    agent._get_autocomplete = AsyncMock(
        return_value=["ceramic mug art", "ceramic mug gift", "other suggestion"]
    )

    result = await asyncio.wait_for(agent._real_tier1("ceramic mug", None), timeout=5)

    # "ceramic" is in "ceramic mug art" and "ceramic mug gift" → ac_hits = 2
    assert result.autocomplete_hits == 2


@pytest.mark.asyncio
async def test_real_tier1_search_exception_returns_market_signals():
    """Even with search failure, result is a MarketSignals instance with correct niche."""
    agent = _make_agent()
    agent._search_etsy_listings = AsyncMock(side_effect=RuntimeError("network error"))

    result = await asyncio.wait_for(agent._real_tier1("boho planner", "printable_pdf"), timeout=5)

    assert isinstance(result, MarketSignals)
    assert result.niche == "boho planner"
    assert result.product_type == "printable_pdf"
    assert result.tier == 1


# ===========================================================================
# Lines 110-111 — ac_suggestions is Exception (asyncio.gather return_exceptions)
# ===========================================================================

@pytest.mark.asyncio
async def test_real_tier1_autocomplete_exception_fallback_empty():
    """_get_autocomplete raises → ac_suggestions fallback to [], autocomplete_hits=0."""
    agent = _make_agent()
    agent._get_autocomplete = AsyncMock(side_effect=Exception("AC timeout"))

    result = await asyncio.wait_for(agent._real_tier1("ceramic mug", None), timeout=5)

    assert isinstance(result, MarketSignals)
    assert result.autocomplete_hits == 0


@pytest.mark.asyncio
async def test_real_tier1_autocomplete_exception_search_data_still_used():
    """When autocomplete fails, Etsy search data is still used correctly."""
    agent = _make_agent()
    agent._search_etsy_listings = AsyncMock(
        return_value={"count": 5000, "avg_reviews": 35.0, "avg_price_eur": 30.0}
    )
    agent._get_autocomplete = AsyncMock(side_effect=ConnectionError("AC down"))

    result = await asyncio.wait_for(agent._real_tier1("ceramic mug", None), timeout=5)

    assert result.etsy_result_count == 5000
    assert result.avg_reviews == 35.0
    assert result.avg_price_eur == 30.0
    assert result.autocomplete_hits == 0


@pytest.mark.asyncio
async def test_real_tier1_both_exceptions_returns_zero_signals():
    """Both tasks raise → all fallback values, MarketSignals returned."""
    agent = _make_agent()
    agent._search_etsy_listings = AsyncMock(side_effect=Exception("search down"))
    agent._get_autocomplete = AsyncMock(side_effect=Exception("ac down"))

    result = await asyncio.wait_for(agent._real_tier1("ceramic mug", None), timeout=5)

    assert isinstance(result, MarketSignals)
    assert result.etsy_result_count == 0
    assert result.autocomplete_hits == 0


# ===========================================================================
# Lines 143-145 — _real_tier2 happy path (get_google_trends succeeds)
# ===========================================================================

@pytest.mark.asyncio
async def test_real_tier2_happy_path_returns_score():
    """get_google_trends returns current_value=42.5 → score=42.5."""
    agent = _make_agent()
    mock_trends = AsyncMock(return_value={"current_value": 42.5, "trend_direction": "growing"})

    with patch("apps.backend.tools.trends.get_google_trends", mock_trends):
        result = await asyncio.wait_for(agent._real_tier2("ceramic mug"), timeout=5)

    assert result == {"score": 42.5}


@pytest.mark.asyncio
async def test_real_tier2_happy_path_uses_current_value_first():
    """If both current_value and avg_value present, current_value wins."""
    agent = _make_agent()
    mock_trends = AsyncMock(
        return_value={"current_value": 70, "avg_value": 55.0, "trend_direction": "stable"}
    )

    with patch("apps.backend.tools.trends.get_google_trends", mock_trends):
        result = await asyncio.wait_for(agent._real_tier2("boho planner"), timeout=5)

    assert result == {"score": 70.0}


@pytest.mark.asyncio
async def test_real_tier2_happy_path_fallback_to_avg_value():
    """If current_value is 0/falsy, falls back to avg_value."""
    agent = _make_agent()
    mock_trends = AsyncMock(
        return_value={"current_value": 0, "avg_value": 33.5, "trend_direction": "stable"}
    )

    with patch("apps.backend.tools.trends.get_google_trends", mock_trends):
        result = await asyncio.wait_for(agent._real_tier2("boho planner"), timeout=5)

    assert result == {"score": 33.5}


@pytest.mark.asyncio
async def test_real_tier2_exception_returns_zero_score():
    """If get_google_trends raises, fallback score=0.0 is returned silently."""
    agent = _make_agent()
    mock_trends = AsyncMock(side_effect=RuntimeError("pytrends quota exceeded"))

    with patch("apps.backend.tools.trends.get_google_trends", mock_trends):
        result = await asyncio.wait_for(agent._real_tier2("ceramic mug"), timeout=5)

    assert result == {"score": 0.0}


@pytest.mark.asyncio
async def test_real_tier2_import_error_returns_zero_score():
    """ImportError (pytrends not installed) returns score=0.0."""
    agent = _make_agent()

    with patch(
        "apps.backend.tools.trends.get_google_trends",
        side_effect=ImportError("No module named 'pytrends'"),
    ):
        result = await asyncio.wait_for(agent._real_tier2("ceramic mug"), timeout=5)

    assert result == {"score": 0.0}


@pytest.mark.asyncio
async def test_real_tier2_happy_path_score_rounded_one_decimal():
    """Score is rounded to 1 decimal place."""
    agent = _make_agent()
    mock_trends = AsyncMock(return_value={"current_value": 67.456})

    with patch("apps.backend.tools.trends.get_google_trends", mock_trends):
        result = await asyncio.wait_for(agent._real_tier2("ceramic mug"), timeout=5)

    assert result == {"score": 67.5}


@pytest.mark.asyncio
async def test_real_tier2_called_with_correct_niche():
    """get_google_trends is called with the niche keyword."""
    agent = _make_agent()
    mock_trends = AsyncMock(return_value={"current_value": 50})

    with patch("apps.backend.tools.trends.get_google_trends", mock_trends):
        await asyncio.wait_for(agent._real_tier2("boho wedding printables"), timeout=5)

    mock_trends.assert_called_once_with("boho wedding printables")
