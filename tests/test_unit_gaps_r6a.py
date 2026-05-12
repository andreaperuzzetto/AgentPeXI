"""Unit-test gap round 6a — ~50 test.

Copertura:
  tools/_file_gen/_pdf_helpers.py        → _rgb, _draw_lines, _draw_instructions_page (~19)
  agents/_market_data/_storage_mixin.py  → get_top_candidates, _save_signals           (~12)
  core/_memory/_reminders.py             → get_personal_recalls, get_reminder_notion_id (~10)
  core/_pepe/_context.py                 → _synthesize_reply, _enrich_task_context       (~9)
"""
from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, call

# ═══════════════════════════════════════════════════════════════════════════════
# 1.  tools/_file_gen/_pdf_helpers.py
# ═══════════════════════════════════════════════════════════════════════════════

from apps.backend.tools._file_gen._pdf_helpers import (
    _draw_instructions_page,
    _draw_lines,
    _rgb,
    FONTS,
)

# ── _rgb ──────────────────────────────────────────────────────────────────────


class TestRgb:
    def test_white_components_are_one(self):
        c = _rgb((255, 255, 255))
        assert c.red == pytest.approx(1.0)
        assert c.green == pytest.approx(1.0)
        assert c.blue == pytest.approx(1.0)

    def test_black_components_are_zero(self):
        c = _rgb((0, 0, 0))
        assert c.red == pytest.approx(0.0)
        assert c.green == pytest.approx(0.0)
        assert c.blue == pytest.approx(0.0)

    def test_mixed_maps_to_unit_range(self):
        c = _rgb((128, 64, 32))
        assert c.red == pytest.approx(128 / 255.0)
        assert c.green == pytest.approx(64 / 255.0)
        assert c.blue == pytest.approx(32 / 255.0)
        assert all(0.0 <= v <= 1.0 for v in (c.red, c.green, c.blue))


# ── _draw_lines ───────────────────────────────────────────────────────────────


def _canvas() -> MagicMock:
    return MagicMock()


class TestDrawLines:
    def test_dotted_false_does_not_call_setdash_2_4(self):
        c = _canvas()
        _draw_lines(c, x=0, y_start=100, width=400, count=1, spacing=10, color=(0, 0, 0), dotted=False)
        assert call(2, 4) not in c.setDash.call_args_list

    def test_dotted_false_calls_setdash_no_args(self):
        c = _canvas()
        _draw_lines(c, x=0, y_start=100, width=400, count=1, spacing=10, color=(0, 0, 0), dotted=False)
        assert call() in c.setDash.call_args_list

    def test_dotted_true_calls_setdash_2_4(self):
        c = _canvas()
        _draw_lines(c, x=0, y_start=100, width=400, count=1, spacing=10, color=(0, 0, 0), dotted=True)
        c.setDash.assert_any_call(2, 4)

    def test_count_3_calls_line_three_times(self):
        c = _canvas()
        _draw_lines(c, x=0, y_start=100, width=400, count=3, spacing=10, color=(0, 0, 0))
        assert c.line.call_count == 3

    def test_returns_y_start_minus_n_spacings(self):
        c = _canvas()
        result = _draw_lines(c, x=0, y_start=200, width=400, count=4, spacing=15, color=(0, 0, 0))
        assert result == pytest.approx(200 - 4 * 15)

    def test_count_zero_no_line_returns_y_start(self):
        c = _canvas()
        result = _draw_lines(c, x=0, y_start=100, width=400, count=0, spacing=10, color=(0, 0, 0))
        c.line.assert_not_called()
        assert result == pytest.approx(100)

    def test_set_stroke_color_called_once(self):
        c = _canvas()
        _draw_lines(c, x=0, y_start=100, width=400, count=1, spacing=10, color=(100, 100, 100))
        c.setStrokeColor.assert_called_once()

    def test_final_reset_dash_called_after_dotted_true(self):
        c = _canvas()
        _draw_lines(c, x=0, y_start=100, width=400, count=1, spacing=10, color=(0, 0, 0), dotted=True)
        # setDash(2,4) + final setDash() reset → 2 total; last call is reset
        assert c.setDash.call_count == 2
        assert c.setDash.call_args_list[-1] == call()

    def test_line_args_correct_x_and_x_plus_width(self):
        c = _canvas()
        _draw_lines(c, x=50, y_start=300, width=200, count=1, spacing=10, color=(0, 0, 0))
        c.line.assert_called_once_with(50, 300, 250, 300)


# ── _draw_instructions_page ───────────────────────────────────────────────────


@pytest.fixture
def inst_fixture():
    c = MagicMock()
    scheme = MagicMock()
    scheme.background = (235, 240, 228)
    scheme.primary = (106, 134, 103)
    scheme.accent = (65, 90, 62)
    return c, scheme


class TestDrawInstructionsPage:
    def test_fonts_none_uses_fonts_default(self, inst_fixture):
        c, scheme = inst_fixture
        _draw_instructions_page(c, scheme, w=595, h=842)
        names = [cc[0][0] for cc in c.setFont.call_args_list]
        assert FONTS["heading"] in names  # "Helvetica-Bold"

    def test_fonts_custom_dict_used(self, inst_fixture):
        c, scheme = inst_fixture
        _draw_instructions_page(c, scheme, w=595, h=842, fonts={"heading": "Times-Roman", "body": "Courier"})
        names = [cc[0][0] for cc in c.setFont.call_args_list]
        assert "Times-Roman" in names
        assert "Courier" in names

    def test_show_page_called_exactly_once(self, inst_fixture):
        c, scheme = inst_fixture
        _draw_instructions_page(c, scheme, w=595, h=842)
        c.showPage.assert_called_once()

    def test_set_font_called_at_least_8_times(self, inst_fixture):
        c, scheme = inst_fixture
        _draw_instructions_page(c, scheme, w=595, h=842)
        # 1 (title h18) + 3×2 (each section: heading+body) + 1 (footer body) = 8
        assert c.setFont.call_count >= 8

    def test_draw_centred_string_contains_thank_you(self, inst_fixture):
        c, scheme = inst_fixture
        _draw_instructions_page(c, scheme, w=595, h=842)
        c.drawCentredString.assert_called()
        texts = [cc[0][2] for cc in c.drawCentredString.call_args_list]
        assert any("Thank You" in t for t in texts)

    def test_draw_string_called_for_section_content(self, inst_fixture):
        c, scheme = inst_fixture
        _draw_instructions_page(c, scheme, w=595, h=842)
        c.drawString.assert_called()

    def test_separator_line_drawn(self, inst_fixture):
        c, scheme = inst_fixture
        _draw_instructions_page(c, scheme, w=595, h=842)
        c.line.assert_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  agents/_market_data/_storage_mixin.py
# ═══════════════════════════════════════════════════════════════════════════════

from apps.backend.agents._market_data._storage_mixin import _StorageMixin
from apps.backend.agents._market_data._models import MarketSignals


class FakeStorage(_StorageMixin):
    pass


@pytest.fixture
def storage():
    s = FakeStorage()
    s._memory = AsyncMock()
    db = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.lastrowid = 99
    db.execute = AsyncMock(return_value=cursor)
    db.commit = AsyncMock()
    s._memory.get_db = AsyncMock(return_value=db)
    return s, db, cursor


def _make_signals() -> MarketSignals:
    return MarketSignals(
        niche="planner",
        product_type="pdf",
        etsy_result_count=100,
        avg_reviews=4.5,
        avg_price_eur=4.99,
        autocomplete_hits=10,
        google_trend_score=0.7,
        erank_search_volume=500,
        entry_score=0.8,
        seasonal_boost=1.0,
        tier="core",
        collected_at="2026-05-12T10:00:00",
    )


class TestGetTopCandidates:
    async def test_empty_fetchall_returns_empty_list(self, storage):
        s, db, cursor = storage
        result = await asyncio.wait_for(s.get_top_candidates(), timeout=5)
        assert result == []

    async def test_single_row_returns_list_of_one(self, storage):
        s, db, cursor = storage
        cursor.fetchall = AsyncMock(return_value=[
            {"niche": "planner", "product_type": "pdf", "entry_score": 0.9, "last_collected": "2026-01-01"}
        ])
        result = await asyncio.wait_for(s.get_top_candidates(), timeout=5)
        assert len(result) == 1

    async def test_result_dict_has_niche_value(self, storage):
        s, db, cursor = storage
        cursor.fetchall = AsyncMock(return_value=[
            {"niche": "planner", "product_type": "pdf", "entry_score": 0.9, "last_collected": "2026-01-01"}
        ])
        result = await asyncio.wait_for(s.get_top_candidates(), timeout=5)
        assert result[0]["niche"] == "planner"

    async def test_min_score_passed_as_execute_param(self, storage):
        s, db, cursor = storage
        await asyncio.wait_for(s.get_top_candidates(min_score=0.5), timeout=5)
        params = db.execute.call_args[0][1]
        assert 0.5 in params

    async def test_limit_passed_as_execute_param(self, storage):
        s, db, cursor = storage
        await asyncio.wait_for(s.get_top_candidates(limit=5), timeout=5)
        params = db.execute.call_args[0][1]
        assert 5 in params


class TestSaveSignals:
    async def test_execute_called(self, storage):
        s, db, cursor = storage
        await asyncio.wait_for(s._save_signals(_make_signals()), timeout=5)
        db.execute.assert_called_once()

    async def test_12_params_in_execute_call(self, storage):
        s, db, cursor = storage
        await asyncio.wait_for(s._save_signals(_make_signals()), timeout=5)
        params = db.execute.call_args[0][1]
        assert len(params) == 12

    async def test_commit_called(self, storage):
        s, db, cursor = storage
        await asyncio.wait_for(s._save_signals(_make_signals()), timeout=5)
        db.commit.assert_called_once()

    async def test_returns_cursor_lastrowid(self, storage):
        s, db, cursor = storage
        result = await asyncio.wait_for(s._save_signals(_make_signals()), timeout=5)
        assert result == 99

    async def test_niche_in_params_tuple(self, storage):
        s, db, cursor = storage
        await asyncio.wait_for(s._save_signals(_make_signals()), timeout=5)
        params = db.execute.call_args[0][1]
        assert "planner" in params

    async def test_product_type_in_params_tuple(self, storage):
        s, db, cursor = storage
        await asyncio.wait_for(s._save_signals(_make_signals()), timeout=5)
        params = db.execute.call_args[0][1]
        assert "pdf" in params

    async def test_entry_score_in_params_tuple(self, storage):
        s, db, cursor = storage
        await asyncio.wait_for(s._save_signals(_make_signals()), timeout=5)
        params = db.execute.call_args[0][1]
        assert 0.8 in params


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  core/_memory/_reminders.py
# ═══════════════════════════════════════════════════════════════════════════════

from apps.backend.core._memory._reminders import RemindersMixin


class FakeReminders(RemindersMixin):
    pass


def _make_reminders_obj(rows: list) -> FakeReminders:
    obj = FakeReminders()
    obj._db = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=rows)
    obj._db.execute = AsyncMock(return_value=cursor)
    return obj


def _notion_ctx_mgr(row) -> tuple[FakeReminders, AsyncMock]:
    """Setup obj with async-with pattern for get_reminder_notion_id."""
    obj = FakeReminders()
    obj._db = MagicMock()
    ctx = AsyncMock()
    cur = AsyncMock()
    cur.fetchone = AsyncMock(return_value=row)
    ctx.__aenter__ = AsyncMock(return_value=cur)
    ctx.__aexit__ = AsyncMock(return_value=False)
    obj._db.execute = MagicMock(return_value=ctx)
    return obj, cur


class TestGetPersonalRecalls:
    async def test_response_key_extracted(self):
        rows = [{"input_data": '{"query": "test"}', "output_data": '{"response": "risposta"}',
                 "task_id": "t1", "status": "completed", "created_at": "2026-01-01"}]
        obj = _make_reminders_obj(rows)
        result = await asyncio.wait_for(obj.get_personal_recalls(), timeout=5)
        assert result[0]["response"] == "risposta"

    async def test_answer_key_fallback(self):
        rows = [{"input_data": '{}', "output_data": '{"answer": "alt"}',
                 "task_id": "t2", "status": "completed", "created_at": "2026-01-01"}]
        obj = _make_reminders_obj(rows)
        result = await asyncio.wait_for(obj.get_personal_recalls(), timeout=5)
        assert result[0]["response"] == "alt"

    async def test_none_fields_produce_empty_strings(self):
        rows = [{"input_data": None, "output_data": None,
                 "task_id": "t3", "status": "completed", "created_at": "2026-01-01"}]
        obj = _make_reminders_obj(rows)
        result = await asyncio.wait_for(obj.get_personal_recalls(), timeout=5)
        assert result[0]["response"] == ""
        assert result[0]["query"] == ""

    async def test_invalid_json_handled_gracefully(self):
        rows = [{"input_data": "not-json", "output_data": "also-not-json",
                 "task_id": "t4", "status": "completed", "created_at": "2026-01-01"}]
        obj = _make_reminders_obj(rows)
        result = await asyncio.wait_for(obj.get_personal_recalls(), timeout=5)
        assert len(result) == 1
        assert result[0]["response"] == ""

    async def test_task_id_present_in_result(self):
        rows = [{"input_data": '{}', "output_data": '{}',
                 "task_id": "tid-99", "status": "completed", "created_at": "2026-01-01"}]
        obj = _make_reminders_obj(rows)
        result = await asyncio.wait_for(obj.get_personal_recalls(), timeout=5)
        assert result[0]["task_id"] == "tid-99"

    async def test_long_response_truncated_with_ellipsis(self):
        long_resp = "x" * 300
        rows = [{"input_data": '{}', "output_data": f'{{"response": "{long_resp}"}}',
                 "task_id": "t5", "status": "completed", "created_at": "2026-01-01"}]
        obj = _make_reminders_obj(rows)
        result = await asyncio.wait_for(obj.get_personal_recalls(), timeout=5)
        assert result[0]["response"].endswith("…")
        assert len(result[0]["response"]) == 201  # 200 chars + "…"


class TestGetReminderNotionId:
    async def test_row_present_returns_notion_page_id(self):
        obj, cur = _notion_ctx_mgr({"notion_page_id": "page-abc-123"})
        result = await asyncio.wait_for(obj.get_reminder_notion_id(42), timeout=5)
        assert result == "page-abc-123"

    async def test_row_none_returns_none(self):
        obj, cur = _notion_ctx_mgr(None)
        result = await asyncio.wait_for(obj.get_reminder_notion_id(42), timeout=5)
        assert result is None

    async def test_telegram_msg_id_in_execute_args(self):
        obj, cur = _notion_ctx_mgr(None)
        await asyncio.wait_for(obj.get_reminder_notion_id(777), timeout=5)
        call_args = obj._db.execute.call_args
        assert 777 in call_args[0][1]

    async def test_async_context_manager_entered(self):
        obj, cur = _notion_ctx_mgr({"notion_page_id": "p1"})
        await asyncio.wait_for(obj.get_reminder_notion_id(1), timeout=5)
        # __aenter__ must have been called (context manager was used)
        ctx = obj._db.execute.return_value
        ctx.__aenter__.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  core/_pepe/_context.py
# ═══════════════════════════════════════════════════════════════════════════════

from apps.backend.core._pepe._context import ContextMixin
from apps.backend.core.models import AgentResult, TaskStatus


class FakeContext(ContextMixin):
    pass


@pytest.fixture
def ctx_obj():
    obj = FakeContext()
    obj.memory = AsyncMock()
    obj.memory.query_chromadb_recent = AsyncMock(return_value=[])
    obj.memory.get_listings_by_niche = AsyncMock(return_value=[])
    obj._agents = {}
    obj._agent_status = {}
    obj._ws_broadcast = None
    obj._business_domain = None
    obj._llm_simple_call = AsyncMock(return_value="synth result")
    obj._has_business_domain = MagicMock(return_value=False)
    return obj


def _large_result() -> AgentResult:
    return AgentResult(
        task_id="t1",
        agent_name="research",
        status=TaskStatus.COMPLETED,
        output_data={"x": "y" * 8100},  # JSON len ≈ 8109 > 8000
    )


def _small_result() -> AgentResult:
    return AgentResult(
        task_id="t2",
        agent_name="research",
        status=TaskStatus.COMPLETED,
        output_data={"x": "small"},
    )


class TestSynthesizeReply:
    async def test_large_output_truncated_in_llm_call(self, ctx_obj):
        await asyncio.wait_for(
            ctx_obj._synthesize_reply("msg", "research", _large_result()), timeout=5
        )
        user_content = ctx_obj._llm_simple_call.call_args[0][1]
        assert "... [troncato]" in user_content

    async def test_small_output_not_truncated(self, ctx_obj):
        await asyncio.wait_for(
            ctx_obj._synthesize_reply("msg", "research", _small_result()), timeout=5
        )
        user_content = ctx_obj._llm_simple_call.call_args[0][1]
        assert "... [troncato]" not in user_content

    async def test_returns_llm_text(self, ctx_obj):
        ctx_obj._llm_simple_call = AsyncMock(return_value="Risultato OK")
        result = await asyncio.wait_for(
            ctx_obj._synthesize_reply("msg", "research", _small_result()), timeout=5
        )
        assert result == "Risultato OK"

    async def test_returns_fallback_when_llm_empty(self, ctx_obj):
        ctx_obj._llm_simple_call = AsyncMock(return_value="")
        result = await asyncio.wait_for(
            ctx_obj._synthesize_reply("msg", "research", _small_result()), timeout=5
        )
        assert "research" in result

    async def test_llm_simple_call_called_once(self, ctx_obj):
        await asyncio.wait_for(
            ctx_obj._synthesize_reply("msg", "research", _small_result()), timeout=5
        )
        ctx_obj._llm_simple_call.assert_called_once()


class TestEnrichTaskContext:
    async def test_exception_on_chromadb_no_research_context(self, ctx_obj):
        ctx_obj.memory.query_chromadb_recent = AsyncMock(side_effect=RuntimeError("boom"))
        enriched = await asyncio.wait_for(
            ctx_obj._enrich_task_context("design", {"niche": "planner"}, "sess1"), timeout=5
        )
        assert "research_context" not in enriched

    async def test_seasonal_context_always_present(self, ctx_obj):
        enriched = await asyncio.wait_for(
            ctx_obj._enrich_task_context("analytics", {}, "sess1"), timeout=5
        )
        assert "seasonal_context" in enriched
        assert "current_month" in enriched["seasonal_context"]

    async def test_design_exception_does_not_propagate(self, ctx_obj):
        ctx_obj.memory.query_chromadb_recent = AsyncMock(side_effect=Exception("db down"))
        enriched = await asyncio.wait_for(
            ctx_obj._enrich_task_context("design", {"niche": "planner"}, "sess1"), timeout=5
        )
        assert isinstance(enriched, dict)

    async def test_non_design_agent_no_research_context(self, ctx_obj):
        ctx_obj.memory.query_chromadb_recent = AsyncMock(return_value=[])
        enriched = await asyncio.wait_for(
            ctx_obj._enrich_task_context("publisher", {"niche": "planner"}, "sess1"), timeout=5
        )
        # research_context only set for "design" agent
        assert "research_context" not in enriched
