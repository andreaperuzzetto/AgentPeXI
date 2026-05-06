"""Tests for Telegram /warmup command and approval callbacks."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_deps():
    deps = MagicMock()
    deps.pepe = MagicMock()
    deps.pepe.memory = MagicMock()
    deps.research_agent = MagicMock()
    deps.research_agent.run_full_warmup = AsyncMock(return_value={
        "all_candidates": {
            "party_celebrations": [
                {"niche": "wedding planner printable", "product_type": "printable_pdf",
                 "score": 0.78, "section": "party_celebrations"},
            ],
            "wellness_selfcare": [],
            "planners_organizers": [],
            "kids_learning": [],
        },
        "total": 1,
        "report": {
            "recommended": [
                {"niche": "wedding planner printable", "product_type": "printable_pdf",
                 "score": 0.78, "section": "party_celebrations",
                 "rationale": "Evergreen demand"},
            ],
            "report_text": "Warmup completato — 1 niche raccomandata.",
        },
    })
    return deps


@pytest.mark.asyncio
async def test_cmd_warmup_calls_run_full_warmup():
    """/warmup command must call research_agent.run_full_warmup()."""
    from apps.backend.telegram.handlers.warmup import cmd_warmup

    deps = _make_deps()
    update = MagicMock()
    update.effective_chat.id = 12345
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()

    await cmd_warmup(update, context, deps)

    deps.research_agent.run_full_warmup.assert_awaited_once()
    context.bot.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_cmd_warmup_sends_report_with_keyboard():
    """/warmup must send Telegram message with inline keyboard for approval."""
    from apps.backend.telegram.handlers.warmup import cmd_warmup
    from telegram import InlineKeyboardMarkup

    deps = _make_deps()
    update = MagicMock()
    update.effective_chat.id = 12345
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()

    await cmd_warmup(update, context, deps)

    call_kwargs = context.bot.send_message.call_args_list[-1][1]
    assert "reply_markup" in call_kwargs
    assert isinstance(call_kwargs["reply_markup"], InlineKeyboardMarkup)


@pytest.mark.asyncio
async def test_cb_approve_warmup_batch_adds_to_queue():
    """Approve batch callback must add pending warmup candidates to production queue."""
    from apps.backend.telegram.handlers.warmup import cb_approve_warmup_batch

    deps = _make_deps()
    deps.research_agent.memory = MagicMock()
    deps.research_agent.memory.query_insights_by_type = AsyncMock(return_value=[
        {
            "id": "doc-batch-001",
            "metadata": {
                "niche": "anxiety journal",
                "product_type": "printable_pdf",
                "score": 0.82,
                "status": "pending",
            },
        },
        {
            "id": "doc-batch-002",
            "metadata": {
                "niche": "gratitude journal",
                "product_type": "printable_pdf",
                "score": 0.91,
                "status": "pending",
            },
        },
    ])
    deps.production_queue = MagicMock()
    deps.production_queue.get_items_by_status = AsyncMock(return_value=[])
    deps.production_queue.create_item = AsyncMock(return_value=42)

    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.effective_chat.id = 12345
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()

    await cb_approve_warmup_batch(update, context, deps)

    assert deps.production_queue.create_item.call_count == 2
    deps.production_queue.create_item.assert_awaited()
    # Verify both items were added
    all_calls = deps.production_queue.create_item.call_args_list
    niches_added = {call[1]["niche"] for call in all_calls}
    assert niches_added == {"anxiety journal", "gratitude journal"}
    context.bot.send_message.assert_awaited()
    update.callback_query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_approve_warmup_niche_adds_single_item():
    """Approve niche callback must add single warmup candidate to production queue."""
    from apps.backend.telegram.handlers.warmup import cb_approve_warmup_niche

    deps = _make_deps()
    deps.research_agent.memory = MagicMock()
    deps.research_agent.memory.query_insights_by_type = AsyncMock(return_value=[
        {
            "id": "doc-abc-123",
            "metadata": {
                "niche": "wedding planner printable",
                "product_type": "printable_pdf",
                "score": 0.78,
                "status": "pending",
            },
        },
    ])
    deps.production_queue = MagicMock()
    deps.production_queue.get_items_by_status = AsyncMock(return_value=[])
    deps.production_queue.create_item = AsyncMock(return_value=43)

    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.data = "approve_warmup:doc-abc-123"
    update.callback_query.answer = AsyncMock()
    update.effective_chat.id = 12345
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()

    await cb_approve_warmup_niche(update, context, deps)

    deps.production_queue.create_item.assert_awaited_once()
    call_kwargs = deps.production_queue.create_item.call_args[1]
    assert call_kwargs["niche"] == "wedding planner printable"
    assert call_kwargs["product_type"] == "printable_pdf"
    context.bot.send_message.assert_awaited()
    update.callback_query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_reject_warmup_niche_updates_rejected_status():
    """Reject niche callback must update warmup candidate status to rejected."""
    from apps.backend.telegram.handlers.warmup import cb_reject_warmup_niche

    deps = _make_deps()
    deps.research_agent.memory = MagicMock()
    deps.research_agent.memory.query_insights_by_type = AsyncMock(return_value=[
        {
            "id": "doc-abc-123",
            "metadata": {
                "niche": "low competition niche",
                "product_type": "printable_pdf",
                "score": 0.65,
                "status": "pending",
            },
        },
    ])
    deps.research_agent.memory.update_insight_metadata = AsyncMock(return_value=True)

    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.data = "reject_warmup:doc-abc-123"
    update.callback_query.answer = AsyncMock()
    update.effective_chat.id = 12345
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()

    await cb_reject_warmup_niche(update, context, deps)

    deps.research_agent.memory.update_insight_metadata.assert_awaited_once()
    call_args = deps.research_agent.memory.update_insight_metadata.call_args[0]
    assert call_args[0] == "doc-abc-123"
    assert call_args[1]["status"] == "rejected"
    context.bot.send_message.assert_awaited()
    update.callback_query.answer.assert_awaited_once()
