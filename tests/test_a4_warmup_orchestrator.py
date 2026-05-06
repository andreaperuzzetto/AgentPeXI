# tests/test_a4_warmup_orchestrator.py
"""Tests for WarmupOrchestratorMixin — run_full_warmup() and _store_warmup_candidates()."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from apps.backend.agents._research.warmup_mixin import WarmupOrchestratorMixin


def _make_mixin() -> WarmupOrchestratorMixin:
    """Minimal concrete WarmupOrchestratorMixin instance with mocked dependencies."""
    class _Impl(WarmupOrchestratorMixin):
        pass

    instance = _Impl()
    instance.memory = MagicMock()
    instance.memory.store_insight = AsyncMock(return_value="doc-abc-123")
    instance._ws_broadcast = AsyncMock()
    return instance


@pytest.mark.asyncio
async def test_run_full_warmup_calls_all_sections():
    """run_full_warmup() must sweep all 4 sections."""
    mixin = _make_mixin()
    fake_candidates = [
        {"niche": "wedding planner printable", "product_type": "printable_pdf", "score": 0.72, "section": "party_celebrations"},
    ]
    mixin.section_sweep = AsyncMock(return_value=fake_candidates)
    mixin._synthesize_warmup_report = AsyncMock(return_value={
        "recommended": [],
        "report_text": "mock report",
    })

    result = await mixin.run_full_warmup()

    assert mixin.section_sweep.call_count == 4
    called_sections = {c.args[0] for c in mixin.section_sweep.call_args_list}
    assert called_sections == set(mixin._WARMUP_SECTION_KEYS)
    assert result["total"] == 4  # 4 sections × 1 candidate each


@pytest.mark.asyncio
async def test_run_full_warmup_emits_progress_per_section():
    """run_full_warmup() must emit warmup_progress WS event for each completed section."""
    mixin = _make_mixin()
    mixin.section_sweep = AsyncMock(return_value=[
        {"niche": "n1", "product_type": "printable_pdf", "score": 0.6, "section": "party_celebrations"},
    ])
    mixin._synthesize_warmup_report = AsyncMock(return_value={
        "recommended": [],
        "report_text": "mock report",
    })

    await mixin.run_full_warmup()

    progress_calls = [
        c for c in mixin._ws_broadcast.call_args_list
        if c.args[0].get("type") == "warmup_progress"
    ]
    assert len(progress_calls) == 4


@pytest.mark.asyncio
async def test_run_full_warmup_emits_completed_event():
    """run_full_warmup() must emit warmup_completed WS event at the end."""
    mixin = _make_mixin()
    mixin.section_sweep = AsyncMock(return_value=[
        {"niche": "n1", "product_type": "printable_pdf", "score": 0.65, "section": "party_celebrations"},
    ])
    mixin._synthesize_warmup_report = AsyncMock(return_value={
        "recommended": [{"niche": "n1"}],
        "report_text": "mock",
    })

    result = await mixin.run_full_warmup()

    completed_calls = [
        c for c in mixin._ws_broadcast.call_args_list
        if c.args[0].get("type") == "warmup_completed"
    ]
    assert len(completed_calls) == 1
    assert completed_calls[0].args[0]["candidates_count"] == result["total"]


@pytest.mark.asyncio
async def test_store_warmup_candidates_calls_store_insight():
    """_store_warmup_candidates() must call memory.store_insight for each candidate."""
    mixin = _make_mixin()
    candidates = [
        {"niche": "wedding planner", "product_type": "printable_pdf", "score": 0.72, "source": "warmup_party"},
        {"niche": "baby shower game", "product_type": "printable_pdf", "score": 0.65, "source": "warmup_party"},
    ]

    doc_ids = await mixin._store_warmup_candidates("party_celebrations", candidates)

    assert mixin.memory.store_insight.call_count == 2
    assert len(doc_ids) == 2

    # Verify metadata shape
    first_call_kwargs = mixin.memory.store_insight.call_args_list[0]
    metadata = first_call_kwargs[1]["metadata"]
    assert metadata["type"] == "warmup_candidate"
    assert metadata["section"] == "party_celebrations"
    assert metadata["niche"] == "wedding planner"
    assert metadata["status"] == "pending"


@pytest.mark.asyncio
async def test_run_full_warmup_tolerates_section_failure():
    """A failing section_sweep should be logged and skipped, not crash the whole run."""
    mixin = _make_mixin()
    call_count = 0

    async def _side_effect(section_key, top_k=5):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Tavily timeout")
        return [{"niche": "n1", "product_type": "printable_pdf", "score": 0.6, "section": section_key}]

    mixin.section_sweep = _side_effect
    mixin._synthesize_warmup_report = AsyncMock(return_value={"recommended": [], "report_text": "mock"})

    result = await mixin.run_full_warmup()
    # 3 sections succeeded (1 candidate each), 1 failed → 3 total
    assert result["total"] == 3
