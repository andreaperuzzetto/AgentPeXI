"""Coverage tests for telegram handlers: autopilot (uncovered functions),
warmup, and shop_identity.

Already covered elsewhere (NOT duplicated here):
  - tests/telegram/test_queue_handler.py:
      handle_approval_callback, handle_bundle_callback (autopilot.py)
      cmd_approve, cmd_skip (autopilot.py)
  - tests/telegram/test_system_config_handlers.py:
      system / config / shop_setup handlers
"""
from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# ── Autopilot (uncovered functions only) ─────────────────────────────────────
from apps.backend.telegram.handlers.autopilot import (
    cb_unknown,
    cmd_approve,
    cmd_queue,
    cmd_run,
    cmd_skip,
    cmd_stop,
    register as register_autopilot,
)

# ── Warmup ────────────────────────────────────────────────────────────────────
from apps.backend.telegram.handlers.warmup import (
    _esc,
    _fmt_section,
    _niche_hash,
    _safe_score,
    cb_approve_warmup_batch,
    cb_approve_warmup_niche,
    cb_reject_warmup_niche,
    cmd_warmup,
    cmd_warmup_detail,
    register as register_warmup,
)

# ── Shop Identity ─────────────────────────────────────────────────────────────
from apps.backend.telegram.handlers.shop_identity import (
    cb_approve_identity,
    cmd_generate_assets,
    cmd_shop_description,
    cmd_style_guide,
    register as register_shop_identity,
)


# =============================================================================
# Shared factories (as specified in the task)
# =============================================================================

def make_update(text="/run", user_id=123):
    upd = MagicMock()
    upd.effective_user = MagicMock()
    upd.effective_user.id = user_id
    upd.message = AsyncMock()
    upd.message.text = text
    upd.message.reply_text = AsyncMock()
    upd.message.reply_html = AsyncMock()
    upd.callback_query = None
    return upd


def make_callback(data="", user_id=123):
    upd = MagicMock()
    upd.effective_user = MagicMock()
    upd.effective_user.id = user_id
    upd.callback_query = AsyncMock()
    upd.callback_query.data = data
    upd.callback_query.answer = AsyncMock()
    upd.callback_query.edit_message_text = AsyncMock()
    upd.message = None
    return upd


def make_ctx(bot_data: dict | None = None):
    ctx = MagicMock()
    ctx.bot = AsyncMock()
    ctx.bot_data = bot_data or {}
    ctx.args = []
    return ctx


# Autopilot-specific deps factory
def _deps_with_loop(loop=None):
    deps = MagicMock()
    deps.autopilot_loop = loop
    return deps


# Warmup-specific update (uses effective_chat, not message)
def _warmup_update(chat_id=12345, callback_data=""):
    upd = MagicMock()
    upd.effective_chat = MagicMock()
    upd.effective_chat.id = chat_id
    upd.callback_query = AsyncMock()
    upd.callback_query.data = callback_data
    upd.callback_query.answer = AsyncMock()
    return upd


def _warmup_ctx(args=None):
    ctx = MagicMock()
    ctx.args = list(args) if args else []
    ctx.bot = AsyncMock()
    ctx.bot.send_message = AsyncMock()
    return ctx


def _research_agent(docs=None, warmup_result=None):
    agent = MagicMock()
    agent.memory = AsyncMock()
    agent.memory.query_insights_by_type = AsyncMock(return_value=docs or [])
    agent.memory.update_insight_metadata = AsyncMock()
    agent.memory.store_insight = AsyncMock()
    agent.run_full_warmup = AsyncMock(return_value=warmup_result or {
        "total": 0,
        "all_candidates": {},
        "report": {"recommended": [], "report_text": "Done."},
    })
    return agent


def _production_queue(existing=None):
    pq = AsyncMock()
    pq.get_items_by_status = AsyncMock(return_value=existing or [])
    pq.create_item = AsyncMock()
    return pq


def _warmup_deps(research_agent=None, production_queue=None):
    deps = MagicMock()
    deps.research_agent = research_agent
    deps.production_queue = production_queue
    return deps


# Shop-identity deps factory
def _identity_deps():
    deps = MagicMock()
    deps.pepe = MagicMock()
    deps.pepe.memory = AsyncMock()
    deps.pepe.memory.get_db = AsyncMock(return_value=MagicMock())
    deps.pepe.anthropic_client = MagicMock()
    deps.pepe.storage = MagicMock()
    return deps


# =============================================================================
# ██████████  AUTOPILOT  ██████████
# =============================================================================


class TestCmdRun:
    """cmd_run — starts autopilot loop."""

    async def test_success_replies_with_loop_message(self):
        loop = AsyncMock()
        loop.cmd_run = AsyncMock(return_value="▶️ Autopilot avviato.")
        deps = _deps_with_loop(loop)
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_run(deps, upd, ctx), timeout=5)
        loop.cmd_run.assert_awaited_once()
        upd.message.reply_text.assert_awaited_once_with("▶️ Autopilot avviato.")

    async def test_loop_none_sends_not_available_warning(self):
        deps = _deps_with_loop(loop=None)
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_run(deps, upd, ctx), timeout=5)
        upd.message.reply_text.assert_awaited_once()
        msg = upd.message.reply_text.call_args[0][0]
        assert "non disponibile" in msg


class TestCmdStop:
    """cmd_stop — stops autopilot loop."""

    async def test_success_replies_with_stop_message(self):
        loop = AsyncMock()
        loop.cmd_stop = AsyncMock(return_value="⏹ Autopilot fermato.")
        deps = _deps_with_loop(loop)
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_stop(deps, upd, ctx), timeout=5)
        loop.cmd_stop.assert_awaited_once()
        upd.message.reply_text.assert_awaited_once_with("⏹ Autopilot fermato.")

    async def test_loop_none_sends_not_available_warning(self):
        deps = _deps_with_loop(loop=None)
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_stop(deps, upd, ctx), timeout=5)
        msg = upd.message.reply_text.call_args[0][0]
        assert "non disponibile" in msg


class TestCmdApprove:
    """cmd_approve — approve an item from the production queue."""

    async def test_valid_id_registers_approved(self):
        loop = AsyncMock()
        deps = _deps_with_loop(loop)
        upd = make_update()
        ctx = make_ctx()
        ctx.args = ["42"]
        await asyncio.wait_for(cmd_approve(deps, upd, ctx), timeout=5)
        loop.register_approval.assert_awaited_once_with(42, "approved")

    async def test_reply_contains_item_id(self):
        loop = AsyncMock()
        deps = _deps_with_loop(loop)
        upd = make_update()
        ctx = make_ctx()
        ctx.args = ["99"]
        await asyncio.wait_for(cmd_approve(deps, upd, ctx), timeout=5)
        reply = upd.message.reply_text.call_args[0][0]
        assert "99" in reply

    async def test_no_args_replies_usage(self):
        loop = AsyncMock()
        deps = _deps_with_loop(loop)
        upd = make_update()
        ctx = make_ctx()
        ctx.args = []
        await asyncio.wait_for(cmd_approve(deps, upd, ctx), timeout=5)
        loop.register_approval.assert_not_awaited()
        reply = upd.message.reply_text.call_args[0][0]
        assert "item_id" in reply or "approve" in reply.lower()

    async def test_non_int_id_replies_error(self):
        loop = AsyncMock()
        deps = _deps_with_loop(loop)
        upd = make_update()
        ctx = make_ctx()
        ctx.args = ["abc"]
        await asyncio.wait_for(cmd_approve(deps, upd, ctx), timeout=5)
        loop.register_approval.assert_not_awaited()
        reply = upd.message.reply_text.call_args[0][0]
        assert "numero" in reply or "intero" in reply

    async def test_loop_none_sends_not_available(self):
        deps = _deps_with_loop(loop=None)
        upd = make_update()
        ctx = make_ctx()
        ctx.args = ["1"]
        await asyncio.wait_for(cmd_approve(deps, upd, ctx), timeout=5)
        msg = upd.message.reply_text.call_args[0][0]
        assert "non disponibile" in msg


class TestCmdQueue:
    """cmd_queue — show / clear the production queue."""

    async def test_no_action_calls_cmd_queue_with_empty_string(self):
        loop = AsyncMock()
        loop.cmd_queue = AsyncMock(return_value="📋 Coda vuota.")
        deps = _deps_with_loop(loop)
        upd = make_update()
        ctx = make_ctx()
        ctx.args = []
        await asyncio.wait_for(cmd_queue(deps, upd, ctx), timeout=5)
        loop.cmd_queue.assert_awaited_once_with("")
        upd.message.reply_text.assert_awaited_once_with("📋 Coda vuota.")

    async def test_clear_action_is_forwarded(self):
        loop = AsyncMock()
        loop.cmd_queue = AsyncMock(return_value="🗑 Coda svuotata.")
        deps = _deps_with_loop(loop)
        upd = make_update()
        ctx = make_ctx()
        ctx.args = ["clear"]
        await asyncio.wait_for(cmd_queue(deps, upd, ctx), timeout=5)
        loop.cmd_queue.assert_awaited_once_with("clear")

    async def test_relays_loop_message_verbatim(self):
        loop = AsyncMock()
        loop.cmd_queue = AsyncMock(return_value="3 items pending.")
        deps = _deps_with_loop(loop)
        upd = make_update()
        ctx = make_ctx()
        ctx.args = []
        await asyncio.wait_for(cmd_queue(deps, upd, ctx), timeout=5)
        upd.message.reply_text.assert_awaited_once_with("3 items pending.")

    async def test_loop_none_sends_not_available(self):
        deps = _deps_with_loop(loop=None)
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_queue(deps, upd, ctx), timeout=5)
        msg = upd.message.reply_text.call_args[0][0]
        assert "non disponibile" in msg


class TestCmdSkip:
    """cmd_skip — skip a production queue item."""

    async def test_valid_id_registers_skipped_user(self):
        loop = AsyncMock()
        deps = _deps_with_loop(loop)
        upd = make_update()
        ctx = make_ctx()
        ctx.args = ["7"]
        await asyncio.wait_for(cmd_skip(deps, upd, ctx), timeout=5)
        loop.register_approval.assert_awaited_once_with(7, "skipped_user")

    async def test_reply_contains_item_id(self):
        loop = AsyncMock()
        deps = _deps_with_loop(loop)
        upd = make_update()
        ctx = make_ctx()
        ctx.args = ["55"]
        await asyncio.wait_for(cmd_skip(deps, upd, ctx), timeout=5)
        reply = upd.message.reply_text.call_args[0][0]
        assert "55" in reply

    async def test_no_args_replies_usage(self):
        loop = AsyncMock()
        deps = _deps_with_loop(loop)
        upd = make_update()
        ctx = make_ctx()
        ctx.args = []
        await asyncio.wait_for(cmd_skip(deps, upd, ctx), timeout=5)
        loop.register_approval.assert_not_awaited()

    async def test_non_int_id_replies_error(self):
        loop = AsyncMock()
        deps = _deps_with_loop(loop)
        upd = make_update()
        ctx = make_ctx()
        ctx.args = ["notanumber"]
        await asyncio.wait_for(cmd_skip(deps, upd, ctx), timeout=5)
        loop.register_approval.assert_not_awaited()
        reply = upd.message.reply_text.call_args[0][0]
        assert "numero" in reply or "intero" in reply

    async def test_loop_none_sends_not_available(self):
        deps = _deps_with_loop(loop=None)
        upd = make_update()
        ctx = make_ctx()
        ctx.args = ["1"]
        await asyncio.wait_for(cmd_skip(deps, upd, ctx), timeout=5)
        msg = upd.message.reply_text.call_args[0][0]
        assert "non disponibile" in msg


class TestCbUnknown:
    """cb_unknown — catch-all callback."""

    async def test_answers_non_riconosciuta(self):
        upd = make_callback(data="something_unknown")
        ctx = make_ctx()
        await asyncio.wait_for(cb_unknown(upd, ctx), timeout=5)
        upd.callback_query.answer.assert_awaited_once_with("Azione non riconosciuta")

    async def test_none_callback_query_returns_early(self):
        upd = MagicMock()
        upd.callback_query = None
        ctx = make_ctx()
        # Must not raise
        await asyncio.wait_for(cb_unknown(upd, ctx), timeout=5)


class TestRegisterAutopilot:
    """register — registers handlers on Application."""

    def test_adds_at_least_seven_handlers(self):
        app = MagicMock()
        deps = MagicMock()
        deps.autopilot_loop = None
        chat_filter = MagicMock()
        register_autopilot(app, deps, chat_filter)
        # run, stop, approve, skip, queue → 5 commands + 2 callbacks + 1 catch-all = 8
        assert app.add_handler.call_count >= 7

    def test_does_not_raise(self):
        app = MagicMock()
        deps = MagicMock()
        chat_filter = MagicMock()
        try:
            register_autopilot(app, deps, chat_filter)
        except Exception as exc:
            pytest.fail(f"register_autopilot raised unexpectedly: {exc}")


# =============================================================================
# ██████████  WARMUP — pure helpers  ██████████
# =============================================================================


class TestSafeScore:
    def test_empty_dict_returns_zero(self):
        assert _safe_score({}) == 0.0

    def test_with_float_score(self):
        assert _safe_score({"metadata": {"score": 0.75}}) == pytest.approx(0.75)

    def test_with_string_score(self):
        assert _safe_score({"metadata": {"score": "0.9"}}) == pytest.approx(0.9)

    def test_with_zero_score_returns_zero(self):
        assert _safe_score({"metadata": {"score": 0}}) == 0.0

    def test_with_invalid_type_returns_zero(self):
        assert _safe_score({"metadata": {"score": "not_a_float"}}) == 0.0

    def test_nested_missing_metadata_returns_zero(self):
        assert _safe_score({"score": 0.8}) == 0.0


class TestNicheHash:
    def test_is_16_chars(self):
        h = _niche_hash("mandala", "wall art")
        assert len(h) == 16

    def test_is_stable_same_input(self):
        h1 = _niche_hash("mandala", "wall art")
        h2 = _niche_hash("mandala", "wall art")
        assert h1 == h2

    def test_different_inputs_produce_different_hashes(self):
        h1 = _niche_hash("mandala", "wall art")
        h2 = _niche_hash("floral", "printable_pdf")
        assert h1 != h2

    def test_matches_expected_sha256_prefix(self):
        expected = hashlib.sha256(
            "mandala:wall art".encode(), usedforsecurity=False
        ).hexdigest()[:16]
        assert _niche_hash("mandala", "wall art") == expected


class TestFmtSection:
    def test_underscores_replaced_by_spaces(self):
        assert _fmt_section("fashion_accessories") == "Fashion Accessories"

    def test_single_word_is_titlecased(self):
        assert _fmt_section("fashion") == "Fashion"

    def test_multiple_underscores(self):
        assert _fmt_section("a_b_c") == "A B C"


class TestEsc:
    def test_escapes_asterisk(self):
        assert _esc("*bold*") == "\\*bold\\*"

    def test_escapes_underscore(self):
        assert _esc("_italic_") == "\\_italic\\_"

    def test_no_special_chars_unchanged(self):
        assert _esc("hello world") == "hello world"

    def test_escapes_dot(self):
        assert "\\." in _esc("price 5.00")


# =============================================================================
# ██████████  WARMUP — cmd_warmup  ██████████
# =============================================================================


class TestCmdWarmup:
    async def test_no_research_agent_sends_warning(self):
        deps = _warmup_deps(research_agent=None)
        upd = _warmup_update()
        ctx = _warmup_ctx()
        await asyncio.wait_for(cmd_warmup(upd, ctx, deps), timeout=5)
        ctx.bot.send_message.assert_awaited()
        msg = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "non disponibile" in msg

    async def test_sends_initial_progress_message(self):
        deps = _warmup_deps(research_agent=_research_agent())
        upd = _warmup_update()
        ctx = _warmup_ctx()
        await asyncio.wait_for(cmd_warmup(upd, ctx, deps), timeout=5)
        first_call = ctx.bot.send_message.call_args_list[0]
        text = first_call.kwargs.get("text", "")
        assert "warmup" in text.lower() or "avviato" in text.lower()

    async def test_empty_recommended_sends_no_keyboard(self):
        deps = _warmup_deps(research_agent=_research_agent())
        upd = _warmup_update()
        ctx = _warmup_ctx()
        await asyncio.wait_for(cmd_warmup(upd, ctx, deps), timeout=5)
        last_call = ctx.bot.send_message.call_args
        assert last_call.kwargs.get("reply_markup") is None

    async def test_with_recommended_sends_inline_keyboard(self):
        result = {
            "total": 1,
            "all_candidates": {"fashion": [{"niche": "mandala"}]},
            "report": {
                "recommended": [
                    {
                        "niche": "mandala",
                        "product_type": "wall art",
                        "score": 0.8,
                        "section": "fashion",
                        "rationale": "popular trend",
                    }
                ],
                "report_text": "1 candidate.",
            },
        }
        deps = _warmup_deps(research_agent=_research_agent(warmup_result=result))
        upd = _warmup_update()
        ctx = _warmup_ctx()
        await asyncio.wait_for(cmd_warmup(upd, ctx, deps), timeout=5)
        last_call = ctx.bot.send_message.call_args
        assert last_call.kwargs.get("reply_markup") is not None

    async def test_run_full_warmup_exception_sends_error(self):
        agent = _research_agent()
        agent.run_full_warmup = AsyncMock(side_effect=RuntimeError("Boom"))
        deps = _warmup_deps(research_agent=agent)
        upd = _warmup_update()
        ctx = _warmup_ctx()
        await asyncio.wait_for(cmd_warmup(upd, ctx, deps), timeout=5)
        last_call = ctx.bot.send_message.call_args
        text = last_call.kwargs.get("text", "")
        assert "errore" in text.lower() or "❌" in text

    async def test_total_candidates_shown_in_message(self):
        result = {
            "total": 7,
            "all_candidates": {},
            "report": {"recommended": [], "report_text": "Done."},
        }
        deps = _warmup_deps(research_agent=_research_agent(warmup_result=result))
        upd = _warmup_update()
        ctx = _warmup_ctx()
        await asyncio.wait_for(cmd_warmup(upd, ctx, deps), timeout=5)
        last_call = ctx.bot.send_message.call_args
        text = last_call.kwargs.get("text", "")
        assert "7" in text


# =============================================================================
# ██████████  WARMUP — cb_approve_warmup_batch  ██████████
# =============================================================================


class TestCbApproveWarmupBatch:
    async def test_no_research_agent_sends_warning(self):
        deps = _warmup_deps(research_agent=None, production_queue=_production_queue())
        upd = _warmup_update()
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_approve_warmup_batch(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "non disponibile" in text

    async def test_no_production_queue_sends_warning(self):
        deps = _warmup_deps(research_agent=_research_agent(), production_queue=None)
        upd = _warmup_update()
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_approve_warmup_batch(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "non disponibile" in text

    async def test_approves_pending_docs_up_to_eight(self):
        docs = [
            {"metadata": {"niche": f"niche{i}", "product_type": "pdf", "score": 0.5, "status": "pending"}}
            for i in range(10)
        ]
        pq = _production_queue()
        deps = _warmup_deps(research_agent=_research_agent(docs=docs), production_queue=pq)
        upd = _warmup_update()
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_approve_warmup_batch(upd, ctx, deps), timeout=5)
        assert pq.create_item.await_count == 8

    async def test_skips_non_pending_docs(self):
        docs = [
            {"metadata": {"niche": "approved_niche", "product_type": "pdf", "score": 0.5, "status": "approved"}},
        ]
        pq = _production_queue()
        deps = _warmup_deps(research_agent=_research_agent(docs=docs), production_queue=pq)
        upd = _warmup_update()
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_approve_warmup_batch(upd, ctx, deps), timeout=5)
        pq.create_item.assert_not_awaited()

    async def test_skips_duplicates_already_in_queue(self):
        docs = [
            {"metadata": {"niche": "mandala", "product_type": "wall art", "score": 0.8, "status": "pending"}},
        ]
        existing = MagicMock()
        existing.niche = "mandala"
        existing.product_type = "wall art"
        pq = _production_queue(existing=[existing])
        deps = _warmup_deps(research_agent=_research_agent(docs=docs), production_queue=pq)
        upd = _warmup_update()
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_approve_warmup_batch(upd, ctx, deps), timeout=5)
        pq.create_item.assert_not_awaited()

    async def test_exception_sends_error_message(self):
        agent = _research_agent()
        agent.memory.query_insights_by_type = AsyncMock(side_effect=Exception("DB fail"))
        deps = _warmup_deps(research_agent=agent, production_queue=_production_queue())
        upd = _warmup_update()
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_approve_warmup_batch(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "errore" in text.lower() or "❌" in text


# =============================================================================
# ██████████  WARMUP — cb_approve_warmup_niche  ██████████
# =============================================================================


class TestCbApproveWarmupNiche:
    async def test_no_research_agent_sends_warning(self):
        deps = _warmup_deps(research_agent=None, production_queue=_production_queue())
        upd = _warmup_update(callback_data="approve_warmup:abc123")
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_approve_warmup_niche(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "non disponibile" in text

    async def test_no_production_queue_sends_warning(self):
        deps = _warmup_deps(research_agent=_research_agent(), production_queue=None)
        upd = _warmup_update(callback_data="approve_warmup:abc123")
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_approve_warmup_niche(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "non disponibile" in text

    async def test_found_by_id_creates_queue_item(self):
        doc_id = "abc123def456789a"
        docs = [{"id": doc_id, "metadata": {"niche": "mandala", "product_type": "wall art", "score": 0.9, "status": "pending"}}]
        pq = _production_queue()
        deps = _warmup_deps(research_agent=_research_agent(docs=docs), production_queue=pq)
        upd = _warmup_update(callback_data=f"approve_warmup:{doc_id}")
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_approve_warmup_niche(upd, ctx, deps), timeout=5)
        pq.create_item.assert_awaited_once()
        kwargs = pq.create_item.call_args.kwargs
        assert kwargs["niche"] == "mandala"

    async def test_not_found_sends_warning(self):
        pq = _production_queue()
        deps = _warmup_deps(research_agent=_research_agent(docs=[]), production_queue=pq)
        upd = _warmup_update(callback_data="approve_warmup:nonexistent")
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_approve_warmup_niche(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "non trovato" in text or "candidato" in text.lower()

    async def test_already_queued_sends_already_in_queue_message(self):
        doc_id = "abc123def456789a"
        docs = [{"id": doc_id, "metadata": {"niche": "mandala", "product_type": "wall art", "score": 0.9}}]
        existing = MagicMock()
        existing.niche = "mandala"
        existing.product_type = "wall art"
        pq = _production_queue(existing=[existing])
        deps = _warmup_deps(research_agent=_research_agent(docs=docs), production_queue=pq)
        upd = _warmup_update(callback_data=f"approve_warmup:{doc_id}")
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_approve_warmup_niche(upd, ctx, deps), timeout=5)
        pq.create_item.assert_not_awaited()
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "coda" in text.lower() or "già" in text.lower()

    async def test_empty_niche_sends_error(self):
        doc_id = "emptynichedocid1"
        docs = [{"id": doc_id, "metadata": {"niche": "   ", "product_type": "wall art", "score": 0.5}}]
        pq = _production_queue()
        deps = _warmup_deps(research_agent=_research_agent(docs=docs), production_queue=pq)
        upd = _warmup_update(callback_data=f"approve_warmup:{doc_id}")
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_approve_warmup_niche(upd, ctx, deps), timeout=5)
        pq.create_item.assert_not_awaited()

    async def test_exception_sends_error_message(self):
        agent = _research_agent()
        agent.memory.query_insights_by_type = AsyncMock(side_effect=RuntimeError("Crash"))
        deps = _warmup_deps(research_agent=agent, production_queue=_production_queue())
        upd = _warmup_update(callback_data="approve_warmup:xyz")
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_approve_warmup_niche(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "❌" in text or "errore" in text.lower()


# =============================================================================
# ██████████  WARMUP — cb_reject_warmup_niche  ██████████
# =============================================================================


class TestCbRejectWarmupNiche:
    async def test_no_research_agent_sends_warning(self):
        deps = _warmup_deps(research_agent=None)
        upd = _warmup_update(callback_data="reject_warmup:abc")
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_reject_warmup_niche(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "non disponibile" in text

    async def test_found_with_doc_id_updates_metadata(self):
        doc_id = "realid1234567890"
        docs = [{"id": doc_id, "metadata": {"niche": "mandala", "product_type": "pdf", "status": "pending"}}]
        agent = _research_agent(docs=docs)
        deps = _warmup_deps(research_agent=agent)
        upd = _warmup_update(callback_data=f"reject_warmup:{doc_id}")
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_reject_warmup_niche(upd, ctx, deps), timeout=5)
        agent.memory.update_insight_metadata.assert_awaited_once()
        updated_meta = agent.memory.update_insight_metadata.call_args[0][1]
        assert updated_meta["status"] == "rejected"

    async def test_found_without_doc_id_uses_store_insight_fallback(self):
        docs = [{"id": None, "metadata": {"niche": "floral", "product_type": "pdf", "status": "pending"}, "document": "floral text"}]
        agent = _research_agent(docs=docs)
        deps = _warmup_deps(research_agent=agent)
        upd = _warmup_update(callback_data="reject_warmup:floral")
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_reject_warmup_niche(upd, ctx, deps), timeout=5)
        agent.memory.store_insight.assert_awaited_once()

    async def test_not_found_still_sends_rejection_message(self):
        agent = _research_agent(docs=[])
        deps = _warmup_deps(research_agent=agent)
        upd = _warmup_update(callback_data="reject_warmup:unknown_niche")
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_reject_warmup_niche(upd, ctx, deps), timeout=5)
        ctx.bot.send_message.assert_awaited()
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "rifiutata" in text.lower() or "marcata" in text.lower()

    async def test_exception_sends_error_message(self):
        agent = _research_agent()
        agent.memory.query_insights_by_type = AsyncMock(side_effect=RuntimeError("Fail"))
        deps = _warmup_deps(research_agent=agent)
        upd = _warmup_update(callback_data="reject_warmup:niche1")
        ctx = _warmup_ctx()
        await asyncio.wait_for(cb_reject_warmup_niche(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "❌" in text or "errore" in text.lower()


# =============================================================================
# ██████████  WARMUP — cmd_warmup_detail  ██████████
# =============================================================================


class TestCmdWarmupDetail:
    async def test_no_args_sends_usage(self):
        deps = _warmup_deps(research_agent=_research_agent())
        upd = _warmup_update()
        ctx = _warmup_ctx(args=[])
        await asyncio.wait_for(cmd_warmup_detail(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "warmup" in text.lower() or "uso" in text.lower()

    async def test_no_research_agent_sends_warning(self):
        deps = _warmup_deps(research_agent=None)
        upd = _warmup_update()
        ctx = _warmup_ctx(args=["mandala"])
        await asyncio.wait_for(cmd_warmup_detail(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "non disponibile" in text

    async def test_found_sends_detail_card(self):
        docs = [{"metadata": {"niche": "mandala art", "product_type": "wall art", "score": 0.85, "section": "fashion", "status": "pending", "source": "etsy"}}]
        agent = _research_agent(docs=docs)
        deps = _warmup_deps(research_agent=agent)
        upd = _warmup_update()
        ctx = _warmup_ctx(args=["mandala"])
        await asyncio.wait_for(cmd_warmup_detail(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "mandala" in text.lower()

    async def test_not_found_sends_warning(self):
        agent = _research_agent(docs=[])
        deps = _warmup_deps(research_agent=agent)
        upd = _warmup_update()
        ctx = _warmup_ctx(args=["nonexistent"])
        await asyncio.wait_for(cmd_warmup_detail(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "nessun" in text.lower() or "trovato" in text.lower()

    async def test_exception_sends_error_message(self):
        agent = _research_agent()
        agent.memory.query_insights_by_type = AsyncMock(side_effect=RuntimeError("Boom"))
        deps = _warmup_deps(research_agent=agent)
        upd = _warmup_update()
        ctx = _warmup_ctx(args=["mandala"])
        await asyncio.wait_for(cmd_warmup_detail(upd, ctx, deps), timeout=5)
        text = ctx.bot.send_message.call_args.kwargs.get("text", "")
        assert "❌" in text or "errore" in text.lower()


class TestRegisterWarmup:
    def test_adds_handlers_without_raising(self):
        app = MagicMock()
        deps = MagicMock()
        chat_filter = MagicMock()
        register_warmup(app, deps, chat_filter)
        assert app.add_handler.call_count >= 5


# =============================================================================
# ██████████  SHOP IDENTITY  ██████████
# =============================================================================

_SIS_PATH = "apps.backend.telegram.handlers.shop_identity.ShopIdentityService"
_DESIGN_PATH = "apps.backend.telegram.handlers.shop_identity.DesignAgent"
_MARKET_PATH = "apps.backend.agents.market_data.MarketDataAgent"


def _mock_option(id_=1, aesthetic_name="Minimalist", is_active=False,
                 palette_primary="#fff", palette_secondary="#000",
                 palette_accent="#f00", mockup_style="flat", tone="clean"):
    opt = MagicMock()
    opt.id = id_
    opt.aesthetic_name = aesthetic_name
    opt.is_active = is_active
    opt.palette_primary = palette_primary
    opt.palette_secondary = palette_secondary
    opt.palette_accent = palette_accent
    opt.mockup_style = mockup_style
    opt.tone = tone
    return opt


class TestCmdStyleGuide:
    @patch(_SIS_PATH)
    async def test_existing_options_sends_list_and_buttons(self, MockSIS):
        svc = AsyncMock()
        svc.list_options = AsyncMock(return_value=[_mock_option(id_=1, is_active=False)])
        MockSIS.return_value = svc
        deps = _identity_deps()
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_style_guide(deps, upd, ctx), timeout=5)
        upd.message.reply_text.assert_awaited_once()

    @patch(_SIS_PATH)
    @patch(_MARKET_PATH)
    async def test_no_options_triggers_generation(self, MockMarket, MockSIS):
        svc = AsyncMock()
        svc.list_options = AsyncMock(side_effect=[[], [_mock_option()]])
        svc.list_options.return_value = [_mock_option()]
        MockSIS.return_value = svc
        market_inst = AsyncMock()
        market_inst.generate_style_options = AsyncMock(return_value=[1])
        MockMarket.return_value = market_inst
        # First call returns [], second call returns options
        svc.list_options = AsyncMock()
        svc.list_options.side_effect = [[], [_mock_option()]]
        deps = _identity_deps()
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_style_guide(deps, upd, ctx), timeout=5)
        upd.message.reply_text.assert_awaited()

    @patch(_SIS_PATH)
    @patch(_MARKET_PATH)
    async def test_generate_exception_replies_error(self, MockMarket, MockSIS):
        svc = AsyncMock()
        svc.list_options = AsyncMock(return_value=[])
        MockSIS.return_value = svc
        market_inst = AsyncMock()
        market_inst.generate_style_options = AsyncMock(side_effect=RuntimeError("API fail"))
        MockMarket.return_value = market_inst
        deps = _identity_deps()
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_style_guide(deps, upd, ctx), timeout=5)
        upd.message.reply_text.assert_awaited()
        last_reply = upd.message.reply_text.call_args_list[-1][0][0]
        assert "errore" in last_reply.lower() or "⚠️" in last_reply

    @patch(_SIS_PATH)
    async def test_active_option_has_no_approve_button(self, MockSIS):
        svc = AsyncMock()
        svc.list_options = AsyncMock(return_value=[_mock_option(is_active=True)])
        MockSIS.return_value = svc
        deps = _identity_deps()
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_style_guide(deps, upd, ctx), timeout=5)
        reply_kwargs = upd.message.reply_text.call_args.kwargs
        # Active option → no buttons → keyboard is None
        assert reply_kwargs.get("reply_markup") is None

    @patch(_SIS_PATH)
    async def test_inactive_option_has_approve_button(self, MockSIS):
        svc = AsyncMock()
        svc.list_options = AsyncMock(return_value=[_mock_option(is_active=False)])
        MockSIS.return_value = svc
        deps = _identity_deps()
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_style_guide(deps, upd, ctx), timeout=5)
        reply_kwargs = upd.message.reply_text.call_args.kwargs
        assert reply_kwargs.get("reply_markup") is not None


class TestCbApproveIdentity:
    @patch(_SIS_PATH)
    async def test_valid_data_activates_and_edits_message(self, MockSIS):
        svc = AsyncMock()
        record = _mock_option(id_=5, aesthetic_name="Boho", tone="earthy")
        svc.set_active = AsyncMock()
        svc.get_active = AsyncMock(return_value=record)
        MockSIS.return_value = svc
        deps = _identity_deps()
        upd = make_callback(data="approve_identity:5")
        ctx = make_ctx()
        await asyncio.wait_for(cb_approve_identity(deps, upd, ctx), timeout=5)
        svc.set_active.assert_awaited_once_with(5)
        upd.callback_query.edit_message_text.assert_awaited_once()

    async def test_malformed_data_no_colon_edits_invalid(self):
        deps = _identity_deps()
        upd = make_callback(data="approve_identity_no_colon")
        ctx = make_ctx()
        await asyncio.wait_for(cb_approve_identity(deps, upd, ctx), timeout=5)
        upd.callback_query.edit_message_text.assert_awaited_once()
        msg = upd.callback_query.edit_message_text.call_args[0][0]
        assert "valido" in msg or "⚠️" in msg

    async def test_non_int_id_edits_invalid(self):
        deps = _identity_deps()
        upd = make_callback(data="approve_identity:notanint")
        ctx = make_ctx()
        await asyncio.wait_for(cb_approve_identity(deps, upd, ctx), timeout=5)
        upd.callback_query.edit_message_text.assert_awaited_once()

    @patch(_SIS_PATH)
    async def test_record_none_after_activation_edits_warning(self, MockSIS):
        svc = AsyncMock()
        svc.set_active = AsyncMock()
        svc.get_active = AsyncMock(return_value=None)
        MockSIS.return_value = svc
        deps = _identity_deps()
        upd = make_callback(data="approve_identity:3")
        ctx = make_ctx()
        await asyncio.wait_for(cb_approve_identity(deps, upd, ctx), timeout=5)
        msg = upd.callback_query.edit_message_text.call_args[0][0]
        assert "trovata" in msg or "⚠️" in msg

    @patch(_SIS_PATH)
    async def test_value_error_edits_error_message(self, MockSIS):
        svc = AsyncMock()
        svc.set_active = AsyncMock(side_effect=ValueError("not found"))
        MockSIS.return_value = svc
        deps = _identity_deps()
        upd = make_callback(data="approve_identity:99")
        ctx = make_ctx()
        await asyncio.wait_for(cb_approve_identity(deps, upd, ctx), timeout=5)
        upd.callback_query.edit_message_text.assert_awaited()
        msg = upd.callback_query.edit_message_text.call_args[0][0]
        assert "not found" in msg

    @patch(_SIS_PATH)
    async def test_unexpected_exception_edits_internal_error(self, MockSIS):
        svc = AsyncMock()
        svc.set_active = AsyncMock(side_effect=Exception("Unexpected"))
        MockSIS.return_value = svc
        deps = _identity_deps()
        upd = make_callback(data="approve_identity:2")
        ctx = make_ctx()
        await asyncio.wait_for(cb_approve_identity(deps, upd, ctx), timeout=5)
        upd.callback_query.edit_message_text.assert_awaited()
        msg = upd.callback_query.edit_message_text.call_args[0][0]
        assert "interno" in msg or "⚠️" in msg

    @patch(_SIS_PATH)
    async def test_answer_is_called(self, MockSIS):
        svc = AsyncMock()
        record = _mock_option()
        svc.set_active = AsyncMock()
        svc.get_active = AsyncMock(return_value=record)
        MockSIS.return_value = svc
        deps = _identity_deps()
        upd = make_callback(data="approve_identity:1")
        ctx = make_ctx()
        await asyncio.wait_for(cb_approve_identity(deps, upd, ctx), timeout=5)
        upd.callback_query.answer.assert_awaited_once()


class TestCmdGenerateAssets:
    @patch(_SIS_PATH)
    async def test_no_active_identity_replies_warning(self, MockSIS):
        svc = AsyncMock()
        svc.get_active = AsyncMock(return_value=None)
        MockSIS.return_value = svc
        deps = _identity_deps()
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_generate_assets(deps, upd, ctx), timeout=5)
        upd.message.reply_text.assert_awaited_once()
        msg = upd.message.reply_text.call_args[0][0]
        assert "attiva" in msg.lower() or "⚠️" in msg

    @patch(_DESIGN_PATH)
    @patch(_SIS_PATH)
    async def test_success_replies_with_paths(self, MockSIS, MockDesign):
        identity = _mock_option(id_=1, aesthetic_name="Boho")
        svc = AsyncMock()
        svc.get_active = AsyncMock(return_value=identity)
        MockSIS.return_value = svc
        design_inst = AsyncMock()
        design_inst.generate_shop_assets = AsyncMock(return_value={
            "logo_path": "/assets/logo.png",
            "banner_path": "/assets/banner.png",
        })
        MockDesign.return_value = design_inst
        deps = _identity_deps()
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_generate_assets(deps, upd, ctx), timeout=5)
        assert upd.message.reply_text.await_count >= 2
        last_reply = upd.message.reply_text.call_args_list[-1][0][0]
        assert "logo" in last_reply.lower() or "assets" in last_reply.lower()

    @patch(_DESIGN_PATH)
    @patch(_SIS_PATH)
    async def test_exception_replies_error(self, MockSIS, MockDesign):
        identity = _mock_option()
        svc = AsyncMock()
        svc.get_active = AsyncMock(return_value=identity)
        MockSIS.return_value = svc
        design_inst = AsyncMock()
        design_inst.generate_shop_assets = AsyncMock(side_effect=RuntimeError("Crash"))
        MockDesign.return_value = design_inst
        deps = _identity_deps()
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_generate_assets(deps, upd, ctx), timeout=5)
        last_reply = upd.message.reply_text.call_args_list[-1][0][0]
        assert "errore" in last_reply.lower() or "⚠️" in last_reply


class TestCmdShopDescription:
    @patch(_SIS_PATH)
    async def test_no_active_identity_replies_warning(self, MockSIS):
        svc = AsyncMock()
        svc.get_active = AsyncMock(return_value=None)
        MockSIS.return_value = svc
        deps = _identity_deps()
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_shop_description(deps, upd, ctx), timeout=5)
        upd.message.reply_text.assert_awaited_once()
        msg = upd.message.reply_text.call_args[0][0]
        assert "attiva" in msg.lower() or "⚠️" in msg

    @patch(_DESIGN_PATH)
    @patch(_SIS_PATH)
    async def test_success_replies_with_description(self, MockSIS, MockDesign):
        identity = _mock_option()
        svc = AsyncMock()
        svc.get_active = AsyncMock(return_value=identity)
        MockSIS.return_value = svc
        design_inst = AsyncMock()
        design_inst.generate_shop_description = AsyncMock(return_value="Beautiful artisan shop.")
        MockDesign.return_value = design_inst
        deps = _identity_deps()
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_shop_description(deps, upd, ctx), timeout=5)
        last_reply = upd.message.reply_text.call_args_list[-1][0][0]
        assert "Beautiful" in last_reply or "descrizione" in last_reply.lower()

    @patch(_DESIGN_PATH)
    @patch(_SIS_PATH)
    async def test_exception_replies_error(self, MockSIS, MockDesign):
        identity = _mock_option()
        svc = AsyncMock()
        svc.get_active = AsyncMock(return_value=identity)
        MockSIS.return_value = svc
        design_inst = AsyncMock()
        design_inst.generate_shop_description = AsyncMock(side_effect=Exception("LLM fail"))
        MockDesign.return_value = design_inst
        deps = _identity_deps()
        upd = make_update()
        ctx = make_ctx()
        await asyncio.wait_for(cmd_shop_description(deps, upd, ctx), timeout=5)
        last_reply = upd.message.reply_text.call_args_list[-1][0][0]
        assert "errore" in last_reply.lower() or "⚠️" in last_reply


class TestRegisterShopIdentity:
    def test_adds_handlers_without_raising(self):
        app = MagicMock()
        deps = MagicMock()
        deps.pepe = MagicMock()
        chat_filter = MagicMock()
        register_shop_identity(app, deps, chat_filter)
        assert app.add_handler.call_count >= 4

    def test_does_not_raise(self):
        app = MagicMock()
        deps = MagicMock()
        chat_filter = MagicMock()
        try:
            register_shop_identity(app, deps, chat_filter)
        except Exception as exc:
            pytest.fail(f"register_shop_identity raised unexpectedly: {exc}")
