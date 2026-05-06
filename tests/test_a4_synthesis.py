# tests/test_a4_synthesis.py
"""Tests for WarmupOrchestratorMixin._synthesize_warmup_report()."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_mixin():
    from apps.backend.agents._research.warmup_mixin import WarmupOrchestratorMixin

    class _Impl(WarmupOrchestratorMixin):
        pass

    instance = _Impl()
    instance.memory = MagicMock()
    instance._ws_broadcast = None
    return instance


_ALL_CANDIDATES = {
    "party_celebrations": [
        {"niche": "wedding planner printable", "product_type": "printable_pdf", "score": 0.78, "section": "party_celebrations"},
        {"niche": "baby shower game", "product_type": "printable_pdf", "score": 0.65, "section": "party_celebrations"},
    ],
    "wellness_selfcare": [
        {"niche": "anxiety journal", "product_type": "printable_pdf", "score": 0.82, "section": "wellness_selfcare"},
    ],
    "planners_organizers": [
        {"niche": "ADHD planner", "product_type": "printable_pdf", "score": 0.74, "section": "planners_organizers"},
    ],
    "kids_learning": [],
}

_MOCK_SONNET_RESPONSE = json.dumps({
    "recommended": [
        {"niche": "anxiety journal", "product_type": "printable_pdf", "score": 0.82, "section": "wellness_selfcare", "rationale": "High score, large audience"},
        {"niche": "ADHD planner", "product_type": "printable_pdf", "score": 0.74, "section": "planners_organizers", "rationale": "Specific audience, underserved"},
        {"niche": "wedding planner printable", "product_type": "printable_pdf", "score": 0.78, "section": "party_celebrations", "rationale": "Evergreen demand"},
    ],
    "report_text": "Warmup completato — 3 niche raccomandate da Sonnet.",
})


@pytest.mark.asyncio
async def test_synthesize_calls_sonnet():
    """_synthesize_warmup_report() must call _call_llm with model_override=MODEL_SONNET."""
    mixin = _make_mixin()
    mixin._call_llm = AsyncMock(return_value=_MOCK_SONNET_RESPONSE)

    result = await mixin._synthesize_warmup_report(_ALL_CANDIDATES)

    assert mixin._call_llm.call_count == 1
    kwargs = mixin._call_llm.call_args[1]
    from apps.backend.core.config import MODEL_SONNET
    assert kwargs.get("model_override") == MODEL_SONNET


@pytest.mark.asyncio
async def test_synthesize_returns_structured_output():
    """_synthesize_warmup_report() must return dict with 'recommended' list and 'report_text'."""
    mixin = _make_mixin()
    mixin._call_llm = AsyncMock(return_value=_MOCK_SONNET_RESPONSE)

    result = await mixin._synthesize_warmup_report(_ALL_CANDIDATES)

    assert "recommended" in result
    assert "report_text" in result
    assert isinstance(result["recommended"], list)
    assert len(result["recommended"]) == 3


@pytest.mark.asyncio
async def test_synthesize_tolerates_malformed_json():
    """_synthesize_warmup_report() must not raise on Sonnet returning invalid JSON."""
    mixin = _make_mixin()
    mixin._call_llm = AsyncMock(return_value="not valid json at all")

    result = await mixin._synthesize_warmup_report(_ALL_CANDIDATES)

    assert "recommended" in result
    assert "report_text" in result
    # Falls back to top-scored candidates from all sections
    assert isinstance(result["recommended"], list)


@pytest.mark.asyncio
async def test_synthesize_handles_null_json():
    """LLM returning JSON null should trigger fallback, not crash."""
    mixin = _make_mixin()
    mixin._call_llm = AsyncMock(return_value="null")
    all_candidates = {
        "home_decor": [{"niche": "wall art", "score": 0.8}],
    }
    result = await mixin._synthesize_warmup_report(all_candidates)
    assert isinstance(result["recommended"], list)
    assert isinstance(result["report_text"], str)
    assert "⚠️" in result["report_text"]


@pytest.mark.asyncio
async def test_synthesize_handles_none_scores():
    """Candidates with score=None should not crash fallback sorting."""
    mixin = _make_mixin()
    mixin._call_llm = AsyncMock(return_value="invalid json{{{")
    all_candidates = {
        "home_decor": [{"niche": "wall art", "score": None}],
        "jewelry": [{"niche": "rings", "score": 0.7}],
    }
    result = await mixin._synthesize_warmup_report(all_candidates)
    assert isinstance(result["recommended"], list)
    assert isinstance(result["report_text"], str)
