"""Comprehensive coverage tests for telegram handlers/_queue sub-handlers.

Covers:
  _listings.py   (_pick_template, _pick_art_type, cmd_listings, cmd_niche)
  _personal.py   (cmd_remind, cmd_remind_list, cmd_summarize, cmd_research,
                  cmd_feedback, cmd_urgency)
  _analytics.py  (_fmt_ladder_single, _fmt_ladder_summary, cmd_analytics,
                  cmd_ladder)
  _bundle.py     (_fmt_bundle_spec, cmd_bundle)
  _design.py     (cmd_design_etsy)
  _finance.py    (_run_and_notify, cmd_finance)
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_update(text: str = ""):
    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.message.document = None
    update.effective_user = MagicMock()
    update.effective_user.id = 12345
    update.effective_chat = MagicMock()
    update.effective_chat.id = 12345
    return update


def _make_deps(**kwargs):
    deps = MagicMock()
    deps.pepe = MagicMock()
    deps.pepe.dispatch_task = AsyncMock()
    deps.pepe.memory = MagicMock()
    deps.pepe.memory.get_etsy_listings = AsyncMock()
    deps.pepe.memory.upsert_learning = AsyncMock()
    deps.scheduler = MagicMock()
    deps.scheduler._run_finance = AsyncMock(return_value=None)
    deps.production_queue = AsyncMock()
    deps.learning_loop = AsyncMock()
    deps.bundle_strategy = MagicMock()
    deps.bundle_strategy.should_create_bundle = AsyncMock()
    deps.bundle_strategy.generate_bundle_spec = AsyncMock()
    deps.bundle_strategy.check_all_niches = AsyncMock()
    deps.analytics_agent = AsyncMock()
    deps.analytics_agent.run_ladder_diagnostic_by_id = AsyncMock()
    deps.analytics_agent.run_ladder_diagnostic_all = AsyncMock()
    deps.finance_tracker = AsyncMock()
    for k, v in kwargs.items():
        setattr(deps, k, v)
    return deps


def _make_context(*args):
    ctx = MagicMock()
    ctx.args = list(args)
    ctx.bot = AsyncMock()
    return ctx


def _make_task_result(output_data=None, cost_usd: float = 0.0):
    result = MagicMock()
    result.output_data = output_data if output_data is not None else {}
    result.cost_usd = cost_usd
    return result


# ===========================================================================
# _listings.py — _pick_template
# ===========================================================================

from apps.backend.telegram.handlers._queue._listings import (
    _pick_art_type,
    _pick_template,
    cmd_listings,
    cmd_niche,
)


class TestPickTemplate:
    def test_habit(self):
        assert _pick_template("habit tracker") == "habit_tracker"

    def test_budget(self):
        assert _pick_template("budget planner") == "budget_tracker"

    def test_finance(self):
        assert _pick_template("personal finance") == "budget_tracker"

    def test_expense(self):
        assert _pick_template("expense tracker") == "budget_tracker"

    def test_meal(self):
        assert _pick_template("meal prep planner") == "meal_planner"

    def test_food(self):
        assert _pick_template("food journal") == "meal_planner"

    def test_recipe(self):
        assert _pick_template("recipe collection") == "meal_planner"

    def test_workout(self):
        assert _pick_template("workout schedule") == "workout_tracker"

    def test_fitness(self):
        assert _pick_template("fitness tracker") == "workout_tracker"

    def test_exercise(self):
        assert _pick_template("exercise log") == "workout_tracker"

    def test_journal(self):
        assert _pick_template("personal journal") == "gratitude_journal"

    def test_diary(self):
        assert _pick_template("personal diary") == "gratitude_journal"

    def test_gratitude(self):
        assert _pick_template("gratitude practice") == "gratitude_journal"

    def test_reading(self):
        assert _pick_template("reading log") == "reading_log"

    def test_book(self):
        assert _pick_template("book tracker") == "reading_log"

    def test_travel(self):
        assert _pick_template("travel planner") == "travel_planner"

    def test_trip(self):
        assert _pick_template("trip guide") == "travel_planner"

    def test_itinerary(self):
        assert _pick_template("trip itinerary") == "travel_planner"

    def test_goal(self):
        assert _pick_template("goal setting") == "goal_planner"

    def test_vision(self):
        assert _pick_template("vision board") == "goal_planner"

    def test_resolution(self):
        assert _pick_template("new year resolution") == "goal_planner"

    def test_project(self):
        assert _pick_template("project planner") == "project_planner"

    def test_task_keyword(self):
        assert _pick_template("task manager") == "project_planner"

    def test_checklist(self):
        assert _pick_template("shopping checklist") == "project_planner"

    def test_daily(self):
        assert _pick_template("daily planner") == "daily_planner"

    def test_day(self):
        assert _pick_template("my day organizer") == "daily_planner"

    def test_monthly(self):
        assert _pick_template("monthly overview") == "monthly_planner"

    def test_month(self):
        assert _pick_template("month at a glance") == "monthly_planner"

    def test_fallback_unknown(self):
        assert _pick_template("xyz unknown topic") == "weekly_planner"

    def test_fallback_empty(self):
        assert _pick_template("") == "weekly_planner"


# ===========================================================================
# _listings.py — _pick_art_type
# ===========================================================================

class TestPickArtType:
    def test_quote(self):
        assert _pick_art_type("inspirational quotes") == "quote_print"

    def test_motivation(self):
        assert _pick_art_type("motivation poster") == "quote_print"

    def test_saying(self):
        assert _pick_art_type("funny sayings wall art") == "quote_print"

    def test_inspirational(self):
        assert _pick_art_type("inspirational wall art") == "quote_print"

    def test_botanical(self):
        assert _pick_art_type("botanical print") == "botanical_print"

    def test_plant(self):
        assert _pick_art_type("plant lover art") == "botanical_print"

    def test_floral(self):
        assert _pick_art_type("floral watercolor") == "botanical_print"

    def test_flower(self):
        assert _pick_art_type("flower art print") == "botanical_print"

    def test_leaf(self):
        assert _pick_art_type("leaf pattern") == "botanical_print"

    def test_nursery(self):
        assert _pick_art_type("nursery decor") == "nursery_print"

    def test_kids(self):
        assert _pick_art_type("kids room art") == "nursery_print"

    def test_baby(self):
        assert _pick_art_type("baby shower art") == "nursery_print"

    def test_children(self):
        assert _pick_art_type("children illustration") == "nursery_print"

    def test_animal(self):
        assert _pick_art_type("animal portrait") == "nursery_print"

    def test_fallback_wall_art(self):
        assert _pick_art_type("minimalist landscape") == "wall_art"

    def test_fallback_unknown(self):
        assert _pick_art_type("abstract geometric") == "wall_art"


# ===========================================================================
# _listings.py — cmd_listings
# ===========================================================================

async def test_cmd_listings_empty_shows_no_listing_message():
    deps = _make_deps()
    deps.pepe.memory.get_etsy_listings.return_value = []
    update = _make_update()

    await cmd_listings(deps, update, _make_context())

    update.message.reply_text.assert_called_once_with("Nessun listing trovato.")


async def test_cmd_listings_with_items_formats_reply():
    deps = _make_deps()
    deps.pepe.memory.get_etsy_listings.return_value = [
        {"title": "Weekly Planner PDF", "status": "active", "sales": 5, "revenue_eur": 12.50},
        {"title": "Budget Tracker Sheet", "status": "inactive", "sales": 2, "revenue_eur": 5.00},
    ]
    update = _make_update()

    await cmd_listings(deps, update, _make_context())

    update.message.reply_text.assert_called_once()
    call_text = update.message.reply_text.call_args[0][0]
    assert "Weekly Planner" in call_text
    assert "active" in call_text


# ===========================================================================
# _listings.py — cmd_niche
# ===========================================================================

async def test_cmd_niche_no_args_shows_help():
    update = _make_update()
    await cmd_niche(_make_deps(), update, _make_context())
    update.message.reply_text.assert_called_once()


async def test_cmd_niche_single_deep_dispatches_research_task():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(output_data={
        "niches": [{
            "name": "weekly planner",
            "demand": {"level": "high", "trend": "up"},
            "competition": {"level": "medium"},
            "viable": True,
            "pricing": {"conversion_sweet_spot_usd": 8.99},
            "keywords": ["planner", "weekly"],
        }]
    })
    update = _make_update()

    with patch("apps.backend.telegram.handlers._queue._listings.reply_chunked", new=AsyncMock()):
        await cmd_niche(deps, update, _make_context("weekly", "planner"))

    deps.pepe.dispatch_task.assert_called_once()
    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.agent_name == "research"
    assert "weekly planner" in task_arg.input_data["niches"]
    assert task_arg.input_data["quick"] is False


async def test_cmd_niche_single_quick_mode():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(output_data={"niches": []})
    update = _make_update()

    with patch("apps.backend.telegram.handlers._queue._listings.reply_chunked", new=AsyncMock()):
        await cmd_niche(deps, update, _make_context("weekly", "planner", "quick"))

    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.input_data["quick"] is True
    assert task_arg.input_data["depth"] == "quick"


async def test_cmd_niche_multi_pipe_dispatches_multi_research():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(output_data={
        "niches": [
            {"name": "weekly planner", "demand": {"level": "high", "trend": "up"},
             "competition": {"level": "medium"}, "viable": True, "pricing": {}},
            {"name": "habit tracker", "demand": {"level": "medium", "trend": "stable"},
             "competition": {"level": "low"}, "viable": True, "pricing": {}},
        ],
        "recommended_niche": "weekly planner",
        "recommended_product_type": "printable_pdf",
        "summary": "Great niches",
    })
    update = _make_update()

    with patch("apps.backend.telegram.handlers._queue._listings.reply_chunked", new=AsyncMock()):
        await cmd_niche(deps, update, _make_context("weekly", "planner", "|", "habit", "tracker"))

    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert len(task_arg.input_data["niches"]) == 2


async def test_cmd_niche_fallback_with_winner_field():
    """When niches_data is empty, fallback to top-level 'winner' key."""
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(output_data={
        "niches": [],
        "winner": {"niche": "planner", "product_type": "pdf", "keywords": ["kw1", "kw2"]},
        "summary": "Good niche analysis",
    })
    update = _make_update()

    with patch("apps.backend.telegram.handlers._queue._listings.reply_chunked", new=AsyncMock()):
        await cmd_niche(deps, update, _make_context("weekly", "planner"))

    deps.pepe.dispatch_task.assert_called_once()


async def test_cmd_niche_fallback_empty_output():
    """When output_data is empty, replies with 'nessun dato strutturato'."""
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(output_data={})
    update = _make_update()

    with patch("apps.backend.telegram.handlers._queue._listings.reply_chunked", new=AsyncMock()):
        await cmd_niche(deps, update, _make_context("xyz", "random"))

    deps.pepe.dispatch_task.assert_called_once()


async def test_cmd_niche_dispatch_exception_replies_error():
    deps = _make_deps()
    deps.pepe.dispatch_task.side_effect = RuntimeError("API down")
    update = _make_update()

    await cmd_niche(deps, update, _make_context("weekly", "planner"))

    call_texts = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("❌" in t or "fallito" in t.lower() for t in call_texts)


# ===========================================================================
# _personal.py — cmd_remind
# ===========================================================================

from apps.backend.telegram.handlers._queue._personal import (
    cmd_feedback,
    cmd_remind,
    cmd_remind_list,
    cmd_research,
    cmd_summarize,
    cmd_urgency,
)

_PATCH_REPLY_CHUNKED_PERSONAL = "apps.backend.telegram.handlers._queue._personal.reply_chunked"


async def test_cmd_remind_no_args_shows_help():
    update = _make_update()
    await cmd_remind(_make_deps(), update, _make_context())
    update.message.reply_text.assert_called_once()
    assert "remind" in update.message.reply_text.call_args[0][0].lower()


async def test_cmd_remind_with_time_dispatches_task():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(
        output_data={"reply": "Reminder impostato!"}
    )
    update = _make_update()

    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()):
        await cmd_remind(deps, update, _make_context("riunione", "alle", "15:00"))

    deps.pepe.dispatch_task.assert_called_once()
    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.agent_name == "remind"
    assert task_arg.input_data["action"] == "create"


async def test_cmd_remind_with_recurring():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(
        output_data={"reply": "Reminder ricorrente impostato!"}
    )
    update = _make_update()

    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()):
        await cmd_remind(deps, update, _make_context("riunione", "alle", "9:00", "ogni", "lunedì"))

    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.input_data["recurring"] == "lunedì"


async def test_cmd_remind_dispatch_exception_handled():
    deps = _make_deps()
    deps.pepe.dispatch_task.side_effect = RuntimeError("Agent error")
    update = _make_update()

    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()) as mock_reply:
        await cmd_remind(deps, update, _make_context("riunione", "alle", "15:00"))

    mock_reply.assert_called_once()
    reply_text = mock_reply.call_args[0][1]
    assert "Errore" in reply_text or "errore" in reply_text.lower()


# ===========================================================================
# _personal.py — cmd_remind_list
# ===========================================================================

async def test_cmd_remind_list_dispatches_list_task():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(
        output_data={"reply": "Hai 2 reminder attivi."}
    )
    update = _make_update()

    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()):
        await cmd_remind_list(deps, update, _make_context())

    deps.pepe.dispatch_task.assert_called_once()
    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.agent_name == "remind"
    assert task_arg.input_data["action"] == "list"


async def test_cmd_remind_list_exception_handled():
    deps = _make_deps()
    deps.pepe.dispatch_task.side_effect = RuntimeError("DB error")
    update = _make_update()

    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()) as mock_reply:
        await cmd_remind_list(deps, update, _make_context())

    mock_reply.assert_called_once()
    assert "Errore" in mock_reply.call_args[0][1]


# ===========================================================================
# _personal.py — cmd_summarize
# ===========================================================================

async def test_cmd_summarize_no_content_shows_help():
    update = _make_update()
    await cmd_summarize(_make_deps(), update, _make_context())
    update.message.reply_text.assert_called_once()
    assert "summarize" in update.message.reply_text.call_args[0][0].lower()


async def test_cmd_summarize_with_url_dispatches_url_task():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(
        output_data={"reply": "Summary here."}
    )
    update = _make_update()

    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()):
        await cmd_summarize(deps, update, _make_context("https://example.com/article"))

    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.agent_name == "summarize"
    assert task_arg.input_data["source_type"] == "url"
    assert task_arg.input_data["length"] == "normal"


async def test_cmd_summarize_with_plain_text_dispatches_text_task():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(
        output_data={"reply": "Summary here."}
    )
    update = _make_update()

    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()):
        await cmd_summarize(deps, update, _make_context("questo", "è", "un", "testo"))

    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.input_data["source_type"] == "text"


async def test_cmd_summarize_short_mode_sets_brief_length():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(
        output_data={"reply": "Brief summary."}
    )
    update = _make_update()

    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()):
        await cmd_summarize(deps, update, _make_context("https://example.com", "short"))

    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.input_data["length"] == "brief"


async def test_cmd_summarize_with_document_dispatches_file_task():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(
        output_data={"reply": "File summary."}
    )
    update = _make_update()
    update.message.document = MagicMock()
    update.message.document.file_id = "file_id_abc123"

    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()):
        await cmd_summarize(deps, update, _make_context())

    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.input_data["source_type"] == "file"
    assert task_arg.input_data["content"] == "file_id_abc123"


# ===========================================================================
# _personal.py — cmd_research
# ===========================================================================

async def test_cmd_research_no_args_shows_help():
    update = _make_update()
    await cmd_research(_make_deps(), update, _make_context())
    update.message.reply_text.assert_called_once()


async def test_cmd_research_deep_mode():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(
        output_data={"response": "Research result."}
    )
    update = _make_update()

    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()):
        await cmd_research(deps, update, _make_context("vantaggi", "regime", "forfettario"))

    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.agent_name == "research_personal"
    assert task_arg.input_data["depth"] == "deep"
    assert "forfettario" in task_arg.input_data["query"]


async def test_cmd_research_quick_mode():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(
        output_data={"response": "Quick result."}
    )
    update = _make_update()

    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()):
        await cmd_research(deps, update, _make_context("forfettario", "quick"))

    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.input_data["depth"] == "quick"
    assert "forfettario" in task_arg.input_data["query"]


# ===========================================================================
# _personal.py — cmd_feedback
# ===========================================================================

async def test_cmd_feedback_insufficient_args_shows_help():
    update = _make_update()
    await cmd_feedback(_make_deps(), update, _make_context("positivo"))
    update.message.reply_text.assert_called_once()
    assert "feedback" in update.message.reply_text.call_args[0][0].lower()


async def test_cmd_feedback_positive_calls_upsert():
    deps = _make_deps()
    update = _make_update()

    await cmd_feedback(deps, update, _make_context("positivo", "scadenza"))

    deps.pepe.memory.upsert_learning.assert_called_once()
    kw = deps.pepe.memory.upsert_learning.call_args[1]
    assert kw["signal_type"] == "positive"
    assert kw["weight_delta"] == pytest.approx(0.1)
    assert kw["pattern_value"] == "scadenza"

    reply_text = update.message.reply_text.call_args[0][0]
    assert "✅" in reply_text or "prioritario" in reply_text


async def test_cmd_feedback_negative_calls_upsert():
    deps = _make_deps()
    update = _make_update()

    await cmd_feedback(deps, update, _make_context("negativo", "newsletter"))

    kw = deps.pepe.memory.upsert_learning.call_args[1]
    assert kw["signal_type"] == "negative"
    assert kw["weight_delta"] == pytest.approx(-0.1)
    assert kw["pattern_value"] == "newsletter"


async def test_cmd_feedback_invalid_signal_shows_error():
    deps = _make_deps()
    update = _make_update()

    await cmd_feedback(deps, update, _make_context("forse", "scadenza"))

    update.message.reply_text.assert_called_once()
    assert "non riconosciuto" in update.message.reply_text.call_args[0][0].lower()
    deps.pepe.memory.upsert_learning.assert_not_called()


async def test_cmd_feedback_upsert_exception_replies_error():
    deps = _make_deps()
    deps.pepe.memory.upsert_learning.side_effect = RuntimeError("DB error")
    update = _make_update()

    await cmd_feedback(deps, update, _make_context("positivo", "scadenza"))

    reply_text = update.message.reply_text.call_args[0][0]
    assert "❌" in reply_text


# ===========================================================================
# _personal.py — cmd_urgency
# ===========================================================================

async def test_cmd_urgency_no_args_shows_help():
    update = _make_update()
    await cmd_urgency(_make_deps(), update, _make_context())
    update.message.reply_text.assert_called_once()


async def test_cmd_urgency_missing_keyword_after_add():
    update = _make_update()
    await cmd_urgency(_make_deps(), update, _make_context("add"))
    update.message.reply_text.assert_called_once()


async def test_cmd_urgency_add_calls_upsert():
    deps = _make_deps()
    update = _make_update()

    await cmd_urgency(deps, update, _make_context("add", "scadenza"))

    deps.pepe.memory.upsert_learning.assert_called_once()
    kw = deps.pepe.memory.upsert_learning.call_args[1]
    assert kw["pattern_value"] == "scadenza"
    assert kw["signal_type"] == "explicit_positive"
    assert kw["weight_delta"] == pytest.approx(0.3)

    reply_text = update.message.reply_text.call_args[0][0]
    assert "scadenza" in reply_text


async def test_cmd_urgency_exception_replies_error():
    deps = _make_deps()
    deps.pepe.memory.upsert_learning.side_effect = RuntimeError("DB error")
    update = _make_update()

    await cmd_urgency(deps, update, _make_context("add", "scadenza"))

    reply_text = update.message.reply_text.call_args[0][0]
    assert "❌" in reply_text


# ===========================================================================
# _analytics.py — _fmt_ladder_single
# ===========================================================================

from apps.backend.telegram.handlers._queue._analytics import (
    _fmt_ladder_single,
    _fmt_ladder_summary,
    cmd_analytics,
    cmd_ladder,
)


class TestFmtLadderSingle:
    def test_error_key_shows_error_icon(self):
        result = _fmt_ladder_single({"error": "listing not found"})
        assert "❌" in result
        assert "listing not found" in result

    def test_full_dict_ok_level(self):
        r = {
            "item_id": 101,
            "level": "ok",
            "niche": "weekly planner",
            "action": None,
            "views": 500,
            "ctr": 0.05,
            "conv": 0.02,
            "days_live": 60,
        }
        result = _fmt_ladder_single(r)
        assert "✅" in result
        assert "weekly planner" in result
        assert "500" in result
        assert "—" in result

    def test_views_low_level_shows_correct_icon(self):
        r = {
            "item_id": 5,
            "level": "views_low",
            "niche": "habit tracker",
            "action": "optimize SEO",
            "views": 12,
            "ctr": 0.01,
            "conv": 0.001,
            "days_live": 25,
        }
        result = _fmt_ladder_single(r)
        assert "🔍" in result
        assert "optimize SEO" in result
        assert "12" in result

    def test_missing_optional_fields_use_question_marks(self):
        r = {"item_id": 99, "level": "too_new"}
        result = _fmt_ladder_single(r)
        assert "🕐" in result
        assert "?" in result

    def test_unknown_level_shows_question_mark_icon(self):
        r = {"item_id": 7, "level": "custom_level"}
        result = _fmt_ladder_single(r)
        assert "❓" in result

    def test_none_action_renders_dash(self):
        r = {"item_id": 3, "level": "ctr_low", "action": None}
        result = _fmt_ladder_single(r)
        assert "—" in result


class TestFmtLadderSummary:
    def test_empty_list_returns_info_message(self):
        result = _fmt_ladder_summary([])
        assert "ℹ️" in result or "Nessun" in result

    def test_with_results_shows_portfolio_header(self):
        results = [
            {"item_id": 1, "level": "ok", "niche": "planner", "action": "none"},
            {"item_id": 2, "level": "views_low", "niche": "tracker", "action": "SEO"},
            {"item_id": 3, "level": "views_low", "niche": "journal", "action": "SEO"},
        ]
        result = _fmt_ladder_summary(results)
        assert "📊" in result
        assert "2" in result

    def test_critical_items_highlighted(self):
        results = [
            {"item_id": i, "level": "conv_low", "niche": f"niche{i}", "action": "improve listing"}
            for i in range(3)
        ]
        result = _fmt_ladder_summary(results)
        assert "⚠️" in result or "Critici" in result

    def test_more_than_5_critical_shows_ellipsis(self):
        results = [
            {"item_id": i, "level": "ctr_low", "niche": f"niche{i}", "action": "thumbnail"}
            for i in range(7)
        ]
        result = _fmt_ladder_summary(results)
        assert "…" in result or "altri" in result


# ===========================================================================
# _analytics.py — cmd_analytics
# ===========================================================================

async def test_cmd_analytics_success_dispatches_analytics_task():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(
        output_data={"listings_analyzed": [1, 2, 3]}
    )
    update = _make_update()

    await cmd_analytics(deps, update, _make_context())

    deps.pepe.dispatch_task.assert_called_once()
    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.agent_name == "analytics"

    call_texts = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("completato" in t.lower() or "3" in t for t in call_texts)


async def test_cmd_analytics_exception_replies_error():
    deps = _make_deps()
    deps.pepe.dispatch_task.side_effect = RuntimeError("Analytics crashed")
    update = _make_update()

    await cmd_analytics(deps, update, _make_context())

    call_texts = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("❌" in t or "fallito" in t.lower() for t in call_texts)


# ===========================================================================
# _analytics.py — cmd_ladder
# ===========================================================================

async def test_cmd_ladder_no_agent_replies_unavailable():
    deps = _make_deps()
    deps.analytics_agent = None
    update = _make_update()

    await cmd_ladder(deps, update, _make_context())

    update.message.reply_text.assert_called_once()
    assert "❌" in update.message.reply_text.call_args[0][0]


async def test_cmd_ladder_invalid_id_replies_usage():
    deps = _make_deps()
    update = _make_update()

    await cmd_ladder(deps, update, _make_context("not-a-number"))

    update.message.reply_text.assert_called_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "ladder" in reply_text.lower() or "uso" in reply_text.lower()


async def test_cmd_ladder_by_id_calls_diagnostic():
    deps = _make_deps()
    deps.analytics_agent.run_ladder_diagnostic_by_id.return_value = {
        "item_id": 42, "level": "ok", "niche": "weekly planner", "action": None,
        "views": 100, "ctr": 0.05, "conv": 0.02, "days_live": 60,
    }
    update = _make_update()

    await cmd_ladder(deps, update, _make_context("42"))

    deps.analytics_agent.run_ladder_diagnostic_by_id.assert_called_once_with(42)
    assert update.message.reply_text.call_count >= 2


async def test_cmd_ladder_by_id_exception_replies_error():
    deps = _make_deps()
    deps.analytics_agent.run_ladder_diagnostic_by_id.side_effect = RuntimeError("DB error")
    update = _make_update()

    await cmd_ladder(deps, update, _make_context("99"))

    call_texts = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("❌" in t for t in call_texts)


async def test_cmd_ladder_portfolio_calls_diagnostic_all():
    deps = _make_deps()
    deps.analytics_agent.run_ladder_diagnostic_all.return_value = [
        {"item_id": 1, "level": "ok", "niche": "planner", "action": None},
        {"item_id": 2, "level": "views_low", "niche": "tracker", "action": "SEO"},
    ]
    update = _make_update()

    await cmd_ladder(deps, update, _make_context())

    deps.analytics_agent.run_ladder_diagnostic_all.assert_called_once()
    assert update.message.reply_text.call_count >= 2


async def test_cmd_ladder_portfolio_exception_replies_error():
    deps = _make_deps()
    deps.analytics_agent.run_ladder_diagnostic_all.side_effect = RuntimeError("timeout")
    update = _make_update()

    await cmd_ladder(deps, update, _make_context())

    call_texts = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("❌" in t for t in call_texts)


# ===========================================================================
# _bundle.py — _fmt_bundle_spec
# ===========================================================================

from apps.backend.telegram.handlers._queue._bundle import (
    _fmt_bundle_spec,
    cmd_bundle,
)


class TestFmtBundleSpec:
    def test_full_spec(self):
        spec = {
            "niche": "weekly planner",
            "component_titles": ["Weekly Planner v1", "Monthly Tracker", "Goal Sheet"],
            "keywords": ["planner", "weekly", "organizer", "printable", "pdf"],
            "suggested_price": 12.99,
            "n_components": 3,
            "entry_score": 0.75,
        }
        result = _fmt_bundle_spec(spec)
        assert "weekly planner" in result
        assert "12.99" in result
        assert "Weekly Planner v1" in result
        assert "0.750" in result

    def test_minimal_spec_empty_components(self):
        spec = {
            "niche": "habit tracker",
            "component_titles": [],
            "keywords": [],
            "suggested_price": 0.0,
            "n_components": 0,
            "entry_score": 0.0,
        }
        result = _fmt_bundle_spec(spec)
        assert "habit tracker" in result
        assert "nessuno" in result.lower()

    def test_keywords_over_8_shows_truncation(self):
        spec = {
            "niche": "test niche",
            "component_titles": ["Title 1"],
            "keywords": [f"kw{i}" for i in range(12)],
            "suggested_price": 5.0,
            "n_components": 1,
            "entry_score": 0.5,
        }
        result = _fmt_bundle_spec(spec)
        assert "…" in result


# ===========================================================================
# _bundle.py — cmd_bundle
# ===========================================================================

async def test_cmd_bundle_no_strategy_replies_unavailable():
    deps = _make_deps()
    deps.bundle_strategy = None
    update = _make_update()

    await cmd_bundle(deps, update, _make_context())

    update.message.reply_text.assert_called_once()
    assert "❌" in update.message.reply_text.call_args[0][0]


async def test_cmd_bundle_with_niche_zero_components_replies_error():
    deps = _make_deps()
    deps.bundle_strategy.should_create_bundle.return_value = False
    deps.bundle_strategy.generate_bundle_spec.return_value = {
        "niche": "weekly planner", "component_titles": [], "keywords": [],
        "suggested_price": 0.0, "n_components": 0, "entry_score": 0.0,
    }
    update = _make_update()

    await cmd_bundle(deps, update, _make_context("weekly", "planner"))

    update.message.reply_text.assert_called_once()
    assert "❌" in update.message.reply_text.call_args[0][0]


async def test_cmd_bundle_with_niche_trigger_satisfied():
    deps = _make_deps()
    deps.bundle_strategy.should_create_bundle.return_value = True
    deps.bundle_strategy.generate_bundle_spec.return_value = {
        "niche": "weekly planner",
        "component_titles": ["Planner v1", "Tracker", "Goal Sheet"],
        "keywords": ["planner", "weekly"],
        "suggested_price": 12.99,
        "n_components": 3,
        "entry_score": 0.8,
    }
    update = _make_update()

    await cmd_bundle(deps, update, _make_context("weekly", "planner"))

    update.message.reply_text.assert_called_once()
    call_text = update.message.reply_text.call_args[0][0]
    assert "weekly planner" in call_text.lower()
    assert "✅" in call_text


async def test_cmd_bundle_with_niche_trigger_not_satisfied():
    deps = _make_deps()
    deps.bundle_strategy.should_create_bundle.return_value = False
    deps.bundle_strategy.generate_bundle_spec.return_value = {
        "niche": "habit tracker",
        "component_titles": ["Habit v1", "Habit v2"],
        "keywords": ["habit"],
        "suggested_price": 7.99,
        "n_components": 2,
        "entry_score": 0.45,
    }
    update = _make_update()

    await cmd_bundle(deps, update, _make_context("habit", "tracker"))

    update.message.reply_text.assert_called_once()
    call_text = update.message.reply_text.call_args[0][0]
    assert "⚠️" in call_text


async def test_cmd_bundle_no_niche_no_candidates():
    deps = _make_deps()
    deps.bundle_strategy.check_all_niches.return_value = []
    update = _make_update()

    await cmd_bundle(deps, update, _make_context())

    call_texts = [c[0][0] for c in update.message.reply_text.call_args_list]
    assert any("nessuna" in t.lower() or "bundle-ready" in t.lower() for t in call_texts)


async def test_cmd_bundle_no_niche_with_candidates_sends_each_spec():
    deps = _make_deps()
    spec = {
        "niche": "habit tracker",
        "component_titles": ["Habit v1", "Habit v2", "Monthly Habit"],
        "keywords": ["habit", "tracker"],
        "suggested_price": 9.99,
        "n_components": 3,
        "entry_score": 0.7,
    }
    deps.bundle_strategy.check_all_niches.return_value = [{"spec": spec}]
    update = _make_update()

    await cmd_bundle(deps, update, _make_context())

    assert update.message.reply_text.call_count >= 2


async def test_cmd_bundle_check_all_exception_replies_error():
    deps = _make_deps()
    deps.bundle_strategy.check_all_niches.side_effect = RuntimeError("DB error")
    update = _make_update()

    await cmd_bundle(deps, update, _make_context())

    call_texts = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("❌" in t for t in call_texts)


# ===========================================================================
# _design.py — cmd_design_etsy
# ===========================================================================

from apps.backend.telegram.handlers._queue._design import cmd_design_etsy


async def test_cmd_design_no_args_shows_help():
    update = _make_update()
    await cmd_design_etsy(_make_deps(), update, _make_context())
    update.message.reply_text.assert_called_once()
    assert "design" in update.message.reply_text.call_args[0][0].lower()


async def test_cmd_design_only_png_arg_shows_help():
    """args=['png'] → niche is empty after stripping 'png' → help."""
    update = _make_update()
    await cmd_design_etsy(_make_deps(), update, _make_context("png"))
    update.message.reply_text.assert_called_once()
    assert "nicchia" in update.message.reply_text.call_args[0][0].lower()


async def test_cmd_design_pdf_dispatches_task_with_template():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(
        output_data={
            "variants": [{"pdf_path": "/tmp/planner_v1.pdf"}, {"pdf_path": "/tmp/planner_v2.pdf"}],
            "preset": "sage",
            "template": "weekly_planner",
        },
        cost_usd=0.0025,
    )
    update = _make_update()

    await cmd_design_etsy(deps, update, _make_context("weekly", "planner"))

    deps.pepe.dispatch_task.assert_called_once()
    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.agent_name == "design"
    assert task_arg.input_data["product_type"] == "printable_pdf"
    assert "template" in task_arg.input_data

    call_texts = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("completato" in t.lower() or "✅" in t for t in call_texts)


async def test_cmd_design_png_dispatches_task_with_art_type():
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(
        output_data={
            "variants": [{"file_path": "/tmp/art_v1.png"}, {"file_path": "/tmp/art_v2.png"}],
            "art_type": "botanical_print",
            "image_provider": "dalle",
        },
        cost_usd=0.05,
    )
    update = _make_update()

    await cmd_design_etsy(deps, update, _make_context("botanical", "wall", "art", "png"))

    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.input_data["product_type"] == "digital_art_png"
    assert task_arg.input_data["art_type"] == "botanical_print"


async def test_cmd_design_exception_replies_error():
    deps = _make_deps()
    deps.pepe.dispatch_task.side_effect = RuntimeError("Design crashed")
    update = _make_update()

    await cmd_design_etsy(deps, update, _make_context("weekly", "planner"))

    call_texts = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("❌" in t or "fallito" in t.lower() for t in call_texts)


# ===========================================================================
# _finance.py — _run_and_notify
# ===========================================================================

from apps.backend.telegram.handlers._queue._finance import (
    _run_and_notify,
    cmd_finance,
)


async def test_run_and_notify_success_sends_ok_message():
    bot = AsyncMock()

    async def _ok():
        pass

    await _run_and_notify(_ok(), chat_id=12345, bot=bot)

    bot.send_message.assert_called_once_with(12345, "✅ Finance report completato")


async def test_run_and_notify_exception_sends_error_message():
    bot = AsyncMock()

    async def _fail():
        raise RuntimeError("Finance API down")

    await _run_and_notify(_fail(), chat_id=12345, bot=bot)

    bot.send_message.assert_called_once()
    call_text = bot.send_message.call_args[0][1]
    assert "❌" in call_text
    assert "Finance API down" in call_text


# ===========================================================================
# _finance.py — cmd_finance
# ===========================================================================

async def test_cmd_finance_no_scheduler_replies_unavailable():
    deps = _make_deps()
    deps.scheduler = None
    update = _make_update()

    await cmd_finance(deps, update, _make_context())

    update.message.reply_text.assert_called_once()
    assert "❌" in update.message.reply_text.call_args[0][0]


async def test_cmd_finance_with_task_registry_creates_named_task():
    deps = _make_deps()
    update = _make_update()
    ctx = _make_context()

    created: list[tuple] = []

    def _capture_and_close(coro, *, name=None):
        try:
            coro.close()
        except Exception:
            pass
        created.append((coro, name))
        return MagicMock()

    mock_registry = MagicMock()
    mock_registry.create_task.side_effect = _capture_and_close

    with patch("apps.backend.telegram.handlers._queue._finance.app_state") as mock_state:
        mock_state.task_registry = mock_registry
        await cmd_finance(deps, update, ctx)

    mock_registry.create_task.assert_called_once()
    assert created[0][1] == "finance_manual"

    update.message.reply_text.assert_called_once()
    assert "⏳" in update.message.reply_text.call_args[0][0]


# ---------------------------------------------------------------------------
# Gap fillers — lines not yet covered
# ---------------------------------------------------------------------------

async def test_cmd_niche_empty_niches_after_quick_strip():
    """args=["|", "quick"] → niches=[] after stripping quick → shows 'Specifica' error."""
    update = _make_update()
    # raw = "| quick" → quick=True → raw stripped to "" → niches=[]
    await cmd_niche(_make_deps(), update, _make_context("|", "quick"))
    call_texts = [c[0][0] for c in update.message.reply_text.call_args_list]
    assert any("Specifica" in t for t in call_texts)


async def test_cmd_remind_no_time_keyword_uses_text_as_when():
    """Text without any time keyword → fallback: when = text (line 49)."""
    deps = _make_deps()
    deps.pepe.dispatch_task.return_value = _make_task_result(
        output_data={"reply": "Memo impostato!"}
    )
    update = _make_update()

    # "promemoria senza orario" has none of the time keywords → when = text branch
    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()):
        await cmd_remind(deps, update, _make_context("promemoria", "senza", "orario"))

    deps.pepe.dispatch_task.assert_called_once()
    task_arg = deps.pepe.dispatch_task.call_args[0][0]
    assert task_arg.input_data["action"] == "create"


async def test_cmd_summarize_dispatch_exception_handled():
    """Exception in dispatch_task → reply_chunked called with error text (lines 138-140)."""
    deps = _make_deps()
    deps.pepe.dispatch_task.side_effect = RuntimeError("Summarize crashed")
    update = _make_update()

    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()) as mock_reply:
        await cmd_summarize(deps, update, _make_context("https://example.com"))

    mock_reply.assert_called_once()
    reply_text = mock_reply.call_args[0][1]
    assert "Errore" in reply_text or "errore" in reply_text.lower()


async def test_cmd_research_dispatch_exception_handled():
    """Exception in dispatch_task → reply_chunked called with error text (lines 177-179)."""
    deps = _make_deps()
    deps.pepe.dispatch_task.side_effect = RuntimeError("Research failed")
    update = _make_update()

    with patch(_PATCH_REPLY_CHUNKED_PERSONAL, new=AsyncMock()) as mock_reply:
        await cmd_research(deps, update, _make_context("my", "query"))

    mock_reply.assert_called_once()
    reply_text = mock_reply.call_args[0][1]
    assert "Errore" in reply_text or "errore" in reply_text.lower()


async def test_cmd_urgency_empty_keyword_after_add_shows_specify_error():
    """args=['add', '  '] → keyword='' after strip → lines 249-252."""
    update = _make_update()
    # whitespace-only keyword → keyword.strip() == ""
    ctx = MagicMock()
    ctx.args = ["add", "  "]
    await cmd_urgency(_make_deps(), update, ctx)
    update.message.reply_text.assert_called_once()
    assert "Specifica" in update.message.reply_text.call_args[0][0]


async def test_cmd_finance_no_task_registry_creates_asyncio_task():
    deps = _make_deps()
    update = _make_update()
    ctx = _make_context()

    with patch("apps.backend.telegram.handlers._queue._finance.app_state") as mock_state:
        mock_state.task_registry = None
        await cmd_finance(deps, update, ctx)
        await asyncio.sleep(0.05)  # let background task finish

    update.message.reply_text.assert_called_once()
    assert "⏳" in update.message.reply_text.call_args[0][0]
    ctx.bot.send_message.assert_called_once_with(12345, "✅ Finance report completato")
