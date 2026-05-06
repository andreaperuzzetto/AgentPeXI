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
