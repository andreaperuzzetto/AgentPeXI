"""Tests for:
  - apps/backend/telegram/handlers/system.py   (cmd_status, cmd_report,
    cmd_pause, cmd_resume, cmd_ask, cmd_new, cmd_retry, cmd_resume_agent,
    cmd_personal, cmd_etsy, cmd_screen, cmd_list, cmd_wiki,
    handle_text, handle_voice)
  - apps/backend/telegram/handlers/config.py   (cmd_budget, cmd_mock,
    cmd_policy, cmd_config, cmd_ads, cb_ads_confirm)
  - apps/backend/telegram/handlers/shop_setup.py (cmd_shop, cmd_shopsetup,
    cb_approve_setup, cb_skip_setup)
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ===========================================================================
# Shared helpers
# ===========================================================================

def _make_update(text: str = "/cmd", chat_id: int = 12345):
    upd = MagicMock()
    upd.message = AsyncMock()
    upd.message.text = text
    upd.message.reply_text = AsyncMock()
    upd.message.reply_voice = AsyncMock()
    upd.message.reply_to_message = None
    upd.message.voice = MagicMock()
    upd.message.voice.file_id = "voice_file_123"
    upd.effective_user = MagicMock()
    upd.effective_user.id = 12345
    upd.effective_chat = MagicMock()
    upd.effective_chat.id = chat_id
    return upd


def _make_context(*args):
    ctx = MagicMock()
    ctx.args = list(args)
    ctx.bot = AsyncMock()
    mock_file = AsyncMock()
    mock_file.download_to_drive = AsyncMock()
    ctx.bot.get_file = AsyncMock(return_value=mock_file)
    return ctx


def _make_deps(**overrides):
    """BotDependencies-like mock with sensible defaults for all fields."""
    deps = MagicMock()

    # ── DB mock (used by _persist_mock_mode and others) ───────────────────
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    # ── pepe ──────────────────────────────────────────────────────────────
    deps.pepe = MagicMock()
    deps.pepe.wiki = None           # must be explicit None for getattr check
    deps.pepe.memory = MagicMock()
    deps.pepe.memory.get_db = AsyncMock(return_value=mock_db)
    deps.pepe.memory.clear_session = AsyncMock()
    deps.pepe.memory.acknowledge_reminder = AsyncMock(return_value=False)
    deps.pepe.memory.get_reminder_notion_id = AsyncMock(return_value=None)
    deps.pepe.memory.get_etsy_listings = AsyncMock(return_value=[])
    deps.pepe.handle_user_message = AsyncMock(return_value="risposta mock")
    deps.pepe.get_agent_statuses = MagicMock(return_value={})
    deps.pepe.get_active_domain = MagicMock(return_value=None)
    deps.pepe._queue = MagicMock()
    deps.pepe._queue.qsize = MagicMock(return_value=0)
    deps.pepe.mock_mode = False
    deps.pepe._ws_broadcast = None  # None → branch skipped
    deps.pepe.stop = AsyncMock()
    deps.pepe.start = AsyncMock()
    deps.pepe.retry_task = AsyncMock()
    deps.pepe.resume_agent = MagicMock(return_value=True)
    deps.pepe.set_active_domain = MagicMock()
    deps.pepe.set_mock_mode = MagicMock()
    deps.pepe.client = MagicMock()
    deps.pepe._local_client = MagicMock()

    # ── optional services — None by default ───────────────────────────────
    deps.autopilot_loop = None
    deps.screen_watcher = None
    deps.scheduler = None
    deps.budget_manager = None
    deps.publication_policy = None
    deps.etsy_api = None
    deps.shop_optimizer = None

    for k, v in overrides.items():
        setattr(deps, k, v)
    return deps


def _make_budget_status(status_name: str = "OK"):
    """Return a budget status mock with real float attributes."""
    s = MagicMock()
    s.status.name = status_name
    s.llm_today = 0.1234
    s.llm_limit = 1.0
    s.llm_pct = 0.1234
    s.image_today = 0.0
    s.image_limit = 0.5
    s.image_pct = 0.0
    s.fee_today = 0.02
    s.fee_limit = 0.5
    s.fee_pct = 0.04
    s.total_today = 0.1434
    s.total_limit = 2.0
    return s


_cb_id_counter = 0


def _make_callback_update(data: str):
    """Build (update, query) pair for callback query tests."""
    global _cb_id_counter
    _cb_id_counter += 1
    query = AsyncMock()
    query.id = f"cb_id_{_cb_id_counter}"
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    upd = MagicMock()
    upd.callback_query = query
    return upd, query


@pytest.fixture(autouse=True)
def _clear_setup_approvals():
    """Reset shop_setup dedup set before each test."""
    from apps.backend.telegram.handlers.shop_setup import _processed_setup_approvals
    _processed_setup_approvals.clear()
    yield
    _processed_setup_approvals.clear()


# ===========================================================================
# system.py — /status
# ===========================================================================

async def test_status_basic_sends_reply():
    from apps.backend.telegram.handlers.system import cmd_status
    deps = _make_deps()
    upd = _make_update()
    await cmd_status(deps, upd, _make_context())
    upd.message.reply_text.assert_called_once()
    text = upd.message.reply_text.call_args[0][0]
    assert "AgentPeXI" in text


async def test_status_with_autopilot_loop_includes_loop_status():
    from apps.backend.telegram.handlers.system import cmd_status
    loop = AsyncMock()
    loop.cmd_status = AsyncMock(return_value="📋 Loop: idle")
    deps = _make_deps(autopilot_loop=loop)
    upd = _make_update()
    await cmd_status(deps, upd, _make_context())
    loop.cmd_status.assert_awaited_once()
    text = upd.message.reply_text.call_args[0][0]
    assert "Loop: idle" in text


async def test_status_loop_raises_shows_error_message():
    from apps.backend.telegram.handlers.system import cmd_status
    loop = AsyncMock()
    loop.cmd_status = AsyncMock(side_effect=RuntimeError("loop esploso"))
    deps = _make_deps(autopilot_loop=loop)
    upd = _make_update()
    await cmd_status(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "errore" in text.lower()


async def test_status_mock_mode_shown_in_reply():
    from apps.backend.telegram.handlers.system import cmd_status
    deps = _make_deps()
    deps.pepe.mock_mode = True
    upd = _make_update()
    await cmd_status(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "MOCK MODE" in text


async def test_status_agent_statuses_included_in_reply():
    from apps.backend.telegram.handlers.system import cmd_status
    deps = _make_deps()
    deps.pepe.get_agent_statuses = MagicMock(
        return_value={"design": "idle", "research": "running"}
    )
    upd = _make_update()
    await cmd_status(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "design" in text
    assert "research" in text


# ===========================================================================
# system.py — /report
# ===========================================================================

async def test_report_calls_handle_user_message():
    from apps.backend.telegram.handlers.system import cmd_report
    deps = _make_deps()
    upd = _make_update()
    await cmd_report(deps, upd, _make_context())
    deps.pepe.handle_user_message.assert_awaited_once()
    arg = deps.pepe.handle_user_message.call_args[0][0]
    assert "report" in arg.lower()


# ===========================================================================
# system.py — /pause / /resume
# ===========================================================================

async def test_pause_stops_pepe_workers():
    from apps.backend.telegram.handlers.system import cmd_pause
    deps = _make_deps()
    upd = _make_update()
    await cmd_pause(deps, upd, _make_context())
    deps.pepe.stop.assert_awaited_once()
    upd.message.reply_text.assert_called_once()


async def test_resume_starts_pepe_workers():
    from apps.backend.telegram.handlers.system import cmd_resume
    deps = _make_deps()
    upd = _make_update()
    await cmd_resume(deps, upd, _make_context())
    deps.pepe.start.assert_awaited_once()
    upd.message.reply_text.assert_called_once()


# ===========================================================================
# system.py — /ask
# ===========================================================================

async def test_ask_no_args_sends_usage():
    from apps.backend.telegram.handlers.system import cmd_ask
    deps = _make_deps()
    upd = _make_update()
    await cmd_ask(deps, upd, _make_context())
    upd.message.reply_text.assert_called_once_with("Uso: /ask <la tua domanda>")
    deps.pepe.handle_user_message.assert_not_awaited()


async def test_ask_with_text_dispatches_to_pepe():
    from apps.backend.telegram.handlers.system import cmd_ask
    deps = _make_deps()
    upd = _make_update()
    await cmd_ask(deps, upd, _make_context("ciao", "come", "stai"))
    deps.pepe.handle_user_message.assert_awaited_once_with(
        "ciao come stai",
        source="telegram",
        session_id=str(upd.effective_chat.id),
    )


# ===========================================================================
# system.py — /new
# ===========================================================================

async def test_new_clears_session_for_chat_id():
    from apps.backend.telegram.handlers.system import cmd_new
    deps = _make_deps()
    upd = _make_update()
    await cmd_new(deps, upd, _make_context())
    deps.pepe.memory.clear_session.assert_awaited_once_with(str(upd.effective_chat.id))
    upd.message.reply_text.assert_called_once()


# ===========================================================================
# system.py — /retry
# ===========================================================================

async def test_retry_no_task_id_success():
    from apps.backend.telegram.handlers.system import cmd_retry
    result = MagicMock()
    result.agent_name = "design"
    result.status.value = "completed"
    deps = _make_deps()
    deps.pepe.retry_task = AsyncMock(return_value=result)
    upd = _make_update()
    await cmd_retry(deps, upd, _make_context())
    deps.pepe.retry_task.assert_awaited_once_with(task_id=None)
    text = upd.message.reply_text.call_args[0][0]
    assert "Retry completato" in text


async def test_retry_specific_task_id_passed_through():
    from apps.backend.telegram.handlers.system import cmd_retry
    result = MagicMock()
    result.agent_name = "research"
    result.status.value = "completed"
    deps = _make_deps()
    deps.pepe.retry_task = AsyncMock(return_value=result)
    upd = _make_update()
    await cmd_retry(deps, upd, _make_context("task-42"))
    deps.pepe.retry_task.assert_awaited_once_with(task_id="task-42")


async def test_retry_value_error_shows_message():
    from apps.backend.telegram.handlers.system import cmd_retry
    deps = _make_deps()
    deps.pepe.retry_task = AsyncMock(side_effect=ValueError("nessun task fallito"))
    upd = _make_update()
    await cmd_retry(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "nessun task fallito" in text


async def test_retry_runtime_error_shows_warning():
    from apps.backend.telegram.handlers.system import cmd_retry
    deps = _make_deps()
    deps.pepe.retry_task = AsyncMock(side_effect=RuntimeError("agente occupato"))
    upd = _make_update()
    await cmd_retry(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "agente occupato" in text


# ===========================================================================
# system.py — /resume_agent
# ===========================================================================

async def test_resume_agent_no_args_sends_usage():
    from apps.backend.telegram.handlers.system import cmd_resume_agent
    deps = _make_deps()
    upd = _make_update()
    await cmd_resume_agent(deps, upd, _make_context())
    upd.message.reply_text.assert_called_once_with("Uso: /resume_agent <nome_agente>")


async def test_resume_agent_found_confirms_reactivation():
    from apps.backend.telegram.handlers.system import cmd_resume_agent
    deps = _make_deps()
    deps.pepe.resume_agent = MagicMock(return_value=True)
    upd = _make_update()
    await cmd_resume_agent(deps, upd, _make_context("design"))
    deps.pepe.resume_agent.assert_called_once_with("design")
    text = upd.message.reply_text.call_args[0][0]
    assert "riattivato" in text


async def test_resume_agent_not_found_reports_error():
    from apps.backend.telegram.handlers.system import cmd_resume_agent
    deps = _make_deps()
    deps.pepe.resume_agent = MagicMock(return_value=False)
    upd = _make_update()
    await cmd_resume_agent(deps, upd, _make_context("ghost"))
    text = upd.message.reply_text.call_args[0][0]
    assert "non trovato" in text


# ===========================================================================
# system.py — /personal / /etsy
# ===========================================================================

async def test_personal_sets_active_domain_to_none():
    from apps.backend.telegram.handlers.system import cmd_personal
    deps = _make_deps()
    upd = _make_update()
    await cmd_personal(deps, upd, _make_context())
    deps.pepe.set_active_domain.assert_called_once_with(None)
    upd.message.reply_text.assert_called_once()


async def test_personal_broadcasts_when_ws_available():
    from apps.backend.telegram.handlers.system import cmd_personal
    deps = _make_deps()
    deps.pepe._ws_broadcast = AsyncMock()
    upd = _make_update()
    await cmd_personal(deps, upd, _make_context())
    deps.pepe._ws_broadcast.assert_awaited_once()
    payload = deps.pepe._ws_broadcast.call_args[0][0]
    assert payload["domain"] == "personal"


async def test_etsy_sets_active_domain_to_etsy():
    from apps.backend.telegram.handlers.system import cmd_etsy
    from apps.backend.core.domains import DOMAIN_ETSY
    deps = _make_deps()
    upd = _make_update()
    await cmd_etsy(deps, upd, _make_context())
    deps.pepe.set_active_domain.assert_called_once_with(DOMAIN_ETSY)
    upd.message.reply_text.assert_called_once()


async def test_etsy_broadcasts_when_ws_available():
    from apps.backend.telegram.handlers.system import cmd_etsy
    deps = _make_deps()
    deps.pepe._ws_broadcast = AsyncMock()
    upd = _make_update()
    await cmd_etsy(deps, upd, _make_context())
    deps.pepe._ws_broadcast.assert_awaited_once()
    payload = deps.pepe._ws_broadcast.call_args[0][0]
    assert payload["domain"] == "etsy_store"


# ===========================================================================
# system.py — /screen
# ===========================================================================

async def test_screen_no_watcher_sends_error():
    from apps.backend.telegram.handlers.system import cmd_screen
    deps = _make_deps()  # screen_watcher = None
    upd = _make_update()
    await cmd_screen(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "non disponibile" in text.lower()


async def test_screen_off_calls_pause():
    from apps.backend.telegram.handlers.system import cmd_screen
    watcher = MagicMock()
    deps = _make_deps(screen_watcher=watcher)
    upd = _make_update()
    await cmd_screen(deps, upd, _make_context("off"))
    watcher.pause.assert_called_once()
    upd.message.reply_text.assert_called_once()


async def test_screen_on_calls_resume():
    from apps.backend.telegram.handlers.system import cmd_screen
    watcher = MagicMock()
    deps = _make_deps(screen_watcher=watcher)
    upd = _make_update()
    await cmd_screen(deps, upd, _make_context("on"))
    watcher.resume.assert_called_once()
    upd.message.reply_text.assert_called_once()


async def test_screen_status_shows_watcher_info():
    from apps.backend.telegram.handlers.system import cmd_screen
    watcher = MagicMock()
    watcher.get_status = MagicMock(return_value={
        "active": True,
        "captures_today": 7,
        "last_capture_app": "Figma",
        "last_capture_time": None,
    })
    deps = _make_deps(screen_watcher=watcher)
    upd = _make_update()
    await cmd_screen(deps, upd, _make_context("status"))
    text = upd.message.reply_text.call_args[0][0]
    assert "Figma" in text
    assert "7" in text


# ===========================================================================
# system.py — /list
# ===========================================================================

async def test_list_contains_key_commands():
    from apps.backend.telegram.handlers.system import cmd_list
    deps = _make_deps()
    upd = _make_update()
    await cmd_list(deps, upd, _make_context())
    upd.message.reply_text.assert_called_once()
    text = upd.message.reply_text.call_args[0][0]
    assert "/status" in text
    assert "/ask" in text
    assert "/pause" in text


# ===========================================================================
# system.py — /wiki
# ===========================================================================

async def test_wiki_no_wiki_attribute_shows_error():
    from apps.backend.telegram.handlers.system import cmd_wiki
    deps = _make_deps()
    # deps.pepe.wiki = None (default)
    upd = _make_update()
    await cmd_wiki(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "WikiManager" in text


async def test_wiki_stats_default_subcommand():
    from apps.backend.telegram.handlers.system import cmd_wiki
    wiki = MagicMock()
    wiki.get_stats = AsyncMock(return_value={
        "etsy_niches": 5, "etsy_patterns": 12,
        "personal_files": 3, "total_raw": 8, "pending_raw": 1,
    })
    deps = _make_deps()
    deps.pepe.wiki = wiki
    upd = _make_update()
    await cmd_wiki(deps, upd, _make_context())  # no args → stats
    wiki.get_stats.assert_awaited_once()
    text = upd.message.reply_text.call_args[0][0]
    assert "Statistiche" in text


async def test_wiki_stats_explicit_subcommand():
    from apps.backend.telegram.handlers.system import cmd_wiki
    wiki = MagicMock()
    wiki.get_stats = AsyncMock(return_value={
        "etsy_niches": 0, "etsy_patterns": 0,
        "personal_files": 0, "total_raw": 0, "pending_raw": 0,
    })
    deps = _make_deps()
    deps.pepe.wiki = wiki
    upd = _make_update()
    await cmd_wiki(deps, upd, _make_context("stats"))
    wiki.get_stats.assert_awaited_once()


async def test_wiki_query_no_text_sends_usage():
    from apps.backend.telegram.handlers.system import cmd_wiki
    wiki = MagicMock()
    deps = _make_deps()
    deps.pepe.wiki = wiki
    upd = _make_update()
    await cmd_wiki(deps, upd, _make_context("query"))  # no query text
    text = upd.message.reply_text.call_args[0][0]
    assert "query" in text.lower()


async def test_wiki_query_with_text_calls_wiki_query():
    from apps.backend.telegram.handlers.system import cmd_wiki
    wiki = MagicMock()
    wiki.query = AsyncMock(return_value="Risultato wiki")
    deps = _make_deps()
    deps.pepe.wiki = wiki
    upd = _make_update()
    await cmd_wiki(deps, upd, _make_context("query", "weekly", "planner"))
    wiki.query.assert_awaited_once_with("etsy", "weekly planner", deps.pepe.client)


async def test_wiki_lint_default_etsy_domain():
    from apps.backend.telegram.handlers.system import cmd_wiki
    wiki = MagicMock()
    wiki.lint = AsyncMock(return_value="OK")
    deps = _make_deps()
    deps.pepe.wiki = wiki
    upd = _make_update()
    await cmd_wiki(deps, upd, _make_context("lint"))
    wiki.lint.assert_awaited_once_with("etsy", deps.pepe.client)


async def test_wiki_lint_personal_uses_local_client():
    from apps.backend.telegram.handlers.system import cmd_wiki
    wiki = MagicMock()
    wiki.lint = AsyncMock(return_value="OK")
    deps = _make_deps()
    deps.pepe.wiki = wiki
    upd = _make_update()
    await cmd_wiki(deps, upd, _make_context("lint", "personal"))
    wiki.lint.assert_awaited_once_with("personal", deps.pepe._local_client)


async def test_wiki_health_without_scheduler_calls_compact_and_index():
    from apps.backend.telegram.handlers.system import cmd_wiki
    wiki = MagicMock()
    wiki.compact_wiki = AsyncMock()
    wiki.update_index = AsyncMock()
    deps = _make_deps()  # scheduler = None
    deps.pepe.wiki = wiki
    upd = _make_update()
    await cmd_wiki(deps, upd, _make_context("health"))
    assert wiki.compact_wiki.await_count == 2   # etsy + personal
    assert wiki.update_index.await_count == 2
    final_text = upd.message.reply_text.call_args_list[-1][0][0]
    assert "completato" in final_text.lower()


async def test_wiki_health_with_scheduler_creates_task():
    from apps.backend.telegram.handlers.system import cmd_wiki
    wiki = MagicMock()
    scheduler = MagicMock()
    scheduler._run_wiki_health_check = AsyncMock()
    deps = _make_deps(scheduler=scheduler)
    deps.pepe.wiki = wiki
    upd = _make_update()
    with patch("apps.backend.telegram.handlers.system.asyncio.create_task") as mock_ct:
        mock_ct.return_value = MagicMock()
        await cmd_wiki(deps, upd, _make_context("health"))
    mock_ct.assert_called_once()
    upd.message.reply_text.assert_called()


async def test_wiki_unknown_subcommand_shows_help():
    from apps.backend.telegram.handlers.system import cmd_wiki
    wiki = MagicMock()
    deps = _make_deps()
    deps.pepe.wiki = wiki
    upd = _make_update()
    await cmd_wiki(deps, upd, _make_context("not_a_command"))
    text = upd.message.reply_text.call_args[0][0]
    assert "stats" in text


# ===========================================================================
# system.py — handle_text
# ===========================================================================

async def test_handle_text_dispatches_plain_message_to_pepe():
    from apps.backend.telegram.handlers.system import handle_text
    deps = _make_deps()
    upd = _make_update("ciao pepe come stai")
    await handle_text(deps, upd, _make_context())
    deps.pepe.handle_user_message.assert_awaited_once_with(
        "ciao pepe come stai",
        source="telegram",
        session_id=str(upd.effective_chat.id),
    )
    upd.message.reply_text.assert_called()


async def test_handle_text_acks_reminder_on_reply():
    from apps.backend.telegram.handlers.system import handle_text
    deps = _make_deps()
    deps.pepe.memory.acknowledge_reminder = AsyncMock(return_value=True)
    deps.pepe.memory.get_reminder_notion_id = AsyncMock(return_value=None)
    upd = _make_update("ok fatto")
    upd.message.reply_to_message = MagicMock()
    upd.message.reply_to_message.message_id = 99
    await handle_text(deps, upd, _make_context())
    deps.pepe.memory.acknowledge_reminder.assert_awaited_once_with(99)
    upd.message.reply_text.assert_called_once_with("✅ Reminder confermato.")
    deps.pepe.handle_user_message.assert_not_awaited()


async def test_handle_text_reply_not_reminder_dispatches_normally():
    from apps.backend.telegram.handlers.system import handle_text
    deps = _make_deps()
    deps.pepe.memory.acknowledge_reminder = AsyncMock(return_value=False)
    upd = _make_update("ciao")
    upd.message.reply_to_message = MagicMock()
    upd.message.reply_to_message.message_id = 7
    await handle_text(deps, upd, _make_context())
    deps.pepe.handle_user_message.assert_awaited_once()


# ===========================================================================
# system.py — handle_voice
# ===========================================================================

async def test_handle_voice_empty_transcription_replies_error():
    from apps.backend.telegram.handlers.system import handle_voice
    deps = _make_deps()
    upd = _make_update()
    ctx = _make_context()
    with patch(
        "apps.backend.telegram.handlers.system._transcribe",
        new=AsyncMock(return_value=""),
    ):
        await handle_voice(deps, upd, ctx)
    upd.message.reply_text.assert_called_once()
    text = upd.message.reply_text.call_args[0][0]
    assert "Non ho capito" in text


async def test_handle_voice_transcription_dispatches_to_pepe():
    from apps.backend.telegram.handlers.system import handle_voice
    deps = _make_deps()
    upd = _make_update()
    upd.message.reply_voice = AsyncMock()
    ctx = _make_context()
    with patch(
        "apps.backend.telegram.handlers.system._transcribe",
        new=AsyncMock(return_value="testo trascritto"),
    ):
        with patch(
            "apps.backend.telegram.handlers.system._synthesize",
            new=AsyncMock(return_value=None),
        ):
            await handle_voice(deps, upd, ctx)
    deps.pepe.handle_user_message.assert_awaited_once()
    # First reply_text call echoes the transcription
    first_call_text = upd.message.reply_text.call_args_list[0][0][0]
    assert "testo trascritto" in first_call_text


async def test_handle_voice_sends_audio_reply_when_tts_available():
    from apps.backend.telegram.handlers.system import handle_voice
    deps = _make_deps()
    upd = _make_update()
    upd.message.reply_voice = AsyncMock()
    ctx = _make_context()
    with patch(
        "apps.backend.telegram.handlers.system._transcribe",
        new=AsyncMock(return_value="ho capito"),
    ):
        with patch(
            "apps.backend.telegram.handlers.system._synthesize",
            new=AsyncMock(return_value=b"audio_bytes"),
        ):
            await handle_voice(deps, upd, ctx)
    upd.message.reply_voice.assert_called_once()


# ===========================================================================
# config.py — /budget
# ===========================================================================

async def test_budget_no_budget_manager_sends_warning():
    from apps.backend.telegram.handlers.config import cmd_budget
    deps = _make_deps()  # budget_manager = None
    upd = _make_update()
    await cmd_budget(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "BudgetManager non disponibile" in text


async def test_budget_snapshot_shows_ok_status():
    from apps.backend.telegram.handlers.config import cmd_budget
    bm = AsyncMock()
    bm.get_status_summary = AsyncMock(return_value=_make_budget_status("OK"))
    deps = _make_deps(budget_manager=bm)
    upd = _make_update()
    await cmd_budget(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "Budget giornaliero" in text
    assert "OK" in text


async def test_budget_snapshot_shows_warning_status():
    from apps.backend.telegram.handlers.config import cmd_budget
    bm = AsyncMock()
    bm.get_status_summary = AsyncMock(return_value=_make_budget_status("WARNING"))
    deps = _make_deps(budget_manager=bm)
    upd = _make_update()
    await cmd_budget(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "WARNING" in text


async def test_budget_set_valid_key_calls_set_limit():
    from apps.backend.telegram.handlers.config import cmd_budget
    bm = AsyncMock()
    bm.set_limit = AsyncMock()
    deps = _make_deps(budget_manager=bm)
    upd = _make_update()
    await cmd_budget(deps, upd, _make_context("set", "daily_llm_usd", "0.75"))
    bm.set_limit.assert_awaited_once_with("daily_llm_usd", 0.75)
    text = upd.message.reply_text.call_args[0][0]
    assert "aggiornato" in text


async def test_budget_set_invalid_key_shows_error():
    from apps.backend.telegram.handlers.config import cmd_budget
    bm = AsyncMock()
    deps = _make_deps(budget_manager=bm)
    upd = _make_update()
    await cmd_budget(deps, upd, _make_context("set", "unknown_key", "1.0"))
    bm.set_limit.assert_not_awaited()
    text = upd.message.reply_text.call_args[0][0]
    assert "non valida" in text.lower()


async def test_budget_set_invalid_value_shows_error():
    from apps.backend.telegram.handlers.config import cmd_budget
    bm = AsyncMock()
    deps = _make_deps(budget_manager=bm)
    upd = _make_update()
    await cmd_budget(deps, upd, _make_context("set", "daily_llm_usd", "notanumber"))
    bm.set_limit.assert_not_awaited()
    text = upd.message.reply_text.call_args[0][0]
    assert "non valido" in text.lower()


# ===========================================================================
# config.py — /mock
# ===========================================================================

async def test_mock_on_sets_mock_mode_true():
    from apps.backend.telegram.handlers.config import cmd_mock
    deps = _make_deps()
    upd = _make_update()
    await cmd_mock(deps, upd, _make_context("on"))
    deps.pepe.set_mock_mode.assert_called_once_with(True)
    text = upd.message.reply_text.call_args[0][0]
    assert "ATTIVO" in text


async def test_mock_off_sets_mock_mode_false():
    from apps.backend.telegram.handlers.config import cmd_mock
    deps = _make_deps()
    upd = _make_update()
    await cmd_mock(deps, upd, _make_context("off"))
    deps.pepe.set_mock_mode.assert_called_once_with(False)
    text = upd.message.reply_text.call_args[0][0]
    assert "disattivato" in text.lower()


async def test_mock_no_arg_shows_inactive_status():
    from apps.backend.telegram.handlers.config import cmd_mock
    deps = _make_deps()
    deps.pepe.mock_mode = False
    upd = _make_update()
    await cmd_mock(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "INATTIVO" in text


async def test_mock_no_arg_shows_active_status():
    from apps.backend.telegram.handlers.config import cmd_mock
    deps = _make_deps()
    deps.pepe.mock_mode = True
    upd = _make_update()
    await cmd_mock(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "ATTIVO" in text


# ===========================================================================
# config.py — /policy
# ===========================================================================

async def test_policy_no_publication_policy_sends_warning():
    from apps.backend.telegram.handlers.config import cmd_policy
    deps = _make_deps()  # publication_policy = None
    upd = _make_update()
    await cmd_policy(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "non disponibile" in text.lower()


async def test_policy_snapshot_shows_policy_keys():
    from apps.backend.telegram.handlers.config import cmd_policy
    pp = AsyncMock()
    pp.get_all = AsyncMock(return_value={
        "policy.max_per_day": "3",
        "policy.min_gap_hours": "2",
    })
    deps = _make_deps(publication_policy=pp)
    upd = _make_update()
    await cmd_policy(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "max_per_day" in text


async def test_policy_set_valid_key_calls_set_config():
    from apps.backend.telegram.handlers.config import cmd_policy
    pp = AsyncMock()
    pp.set_config = AsyncMock()
    deps = _make_deps(publication_policy=pp)
    upd = _make_update()
    await cmd_policy(deps, upd, _make_context("set", "max_per_day", "4"))
    pp.set_config.assert_awaited_once_with("policy.max_per_day", "4")
    text = upd.message.reply_text.call_args[0][0]
    assert "policy.max_per_day" in text


async def test_policy_set_invalid_key_shows_error():
    from apps.backend.telegram.handlers.config import cmd_policy
    pp = AsyncMock()
    pp.set_config = AsyncMock()
    deps = _make_deps(publication_policy=pp)
    upd = _make_update()
    await cmd_policy(deps, upd, _make_context("set", "nonexistent_key", "val"))
    pp.set_config.assert_not_awaited()
    text = upd.message.reply_text.call_args[0][0]
    assert "sconosciuta" in text.lower()


# ===========================================================================
# config.py — /config
# ===========================================================================

async def test_config_no_args_sends_usage():
    from apps.backend.telegram.handlers.config import cmd_config
    deps = _make_deps()
    upd = _make_update()
    await cmd_config(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "Uso" in text


async def test_config_policy_key_calls_set_config():
    from apps.backend.telegram.handlers.config import cmd_config
    pp = AsyncMock()
    pp.set_config = AsyncMock()
    deps = _make_deps(publication_policy=pp)
    upd = _make_update()
    await cmd_config(deps, upd, _make_context("policy.max_per_day", "3"))
    pp.set_config.assert_awaited_once_with("policy.max_per_day", "3")


async def test_config_budget_key_calls_set_limit():
    from apps.backend.telegram.handlers.config import cmd_config
    bm = AsyncMock()
    bm.set_limit = AsyncMock()
    deps = _make_deps(budget_manager=bm)
    upd = _make_update()
    await cmd_config(deps, upd, _make_context("budget.daily_llm_usd", "0.80"))
    bm.set_limit.assert_awaited_once_with("daily_llm_usd", 0.80)


async def test_config_budget_invalid_value_shows_error():
    from apps.backend.telegram.handlers.config import cmd_config
    bm = AsyncMock()
    deps = _make_deps(budget_manager=bm)
    upd = _make_update()
    await cmd_config(deps, upd, _make_context("budget.daily_llm_usd", "abc"))
    bm.set_limit.assert_not_awaited()
    text = upd.message.reply_text.call_args[0][0]
    assert "non numerico" in text.lower()


async def test_config_unknown_namespace_shows_error():
    from apps.backend.telegram.handlers.config import cmd_config
    deps = _make_deps()
    upd = _make_update()
    await cmd_config(deps, upd, _make_context("unknown.key", "val"))
    text = upd.message.reply_text.call_args[0][0]
    assert "Namespace non riconosciuto" in text


# ===========================================================================
# config.py — /ads
# ===========================================================================

async def test_ads_no_publication_policy_sends_warning():
    from apps.backend.telegram.handlers.config import cmd_ads
    deps = _make_deps()  # publication_policy = None
    upd = _make_update()
    await cmd_ads(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "non disponibile" in text.lower()


async def test_ads_on_enables_ads():
    from apps.backend.telegram.handlers.config import cmd_ads
    pp = AsyncMock()
    pp.set_config = AsyncMock()
    deps = _make_deps(publication_policy=pp)
    upd = _make_update()
    await cmd_ads(deps, upd, _make_context("on"))
    pp.set_config.assert_awaited_once_with("policy.etsy_ads_on_publish", "true")
    text = upd.message.reply_text.call_args[0][0]
    assert "abilitati" in text.lower()


async def test_ads_off_disables_ads():
    from apps.backend.telegram.handlers.config import cmd_ads
    pp = AsyncMock()
    pp.set_config = AsyncMock()
    deps = _make_deps(publication_policy=pp)
    upd = _make_update()
    await cmd_ads(deps, upd, _make_context("off"))
    pp.set_config.assert_awaited_once_with("policy.etsy_ads_on_publish", "false")


async def test_ads_budget_set_valid_value():
    from apps.backend.telegram.handlers.config import cmd_ads
    pp = AsyncMock()
    pp.set_config = AsyncMock()
    deps = _make_deps(publication_policy=pp)
    upd = _make_update()
    await cmd_ads(deps, upd, _make_context("budget", "2.50"))
    pp.set_config.assert_awaited_once_with("policy.etsy_ads_daily_budget", "2.5")
    text = upd.message.reply_text.call_args[0][0]
    assert "2.50" in text


async def test_ads_status_shows_current_state():
    from apps.backend.telegram.handlers.config import cmd_ads
    pp = AsyncMock()
    pp.ads_enabled = AsyncMock(return_value=True)
    pp.ads_daily_budget = AsyncMock(return_value=1.50)
    deps = _make_deps(publication_policy=pp)
    upd = _make_update()
    await cmd_ads(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "ATTIVI" in text
    assert "1.50" in text


# ===========================================================================
# config.py — cb_ads_confirm
# ===========================================================================

async def test_cb_ads_confirm_on_sets_true_and_edits_message():
    from apps.backend.telegram.handlers.config import cb_ads_confirm
    pp = AsyncMock()
    pp.set_config = AsyncMock()
    pp.ads_daily_budget = AsyncMock(return_value=1.0)
    deps = _make_deps(publication_policy=pp)
    upd, query = _make_callback_update("ads_confirm:on")
    await cb_ads_confirm(deps, upd, _make_context())
    query.answer.assert_awaited_once()
    pp.set_config.assert_awaited_once_with("policy.etsy_ads_on_publish", "true")
    text = query.edit_message_text.call_args[0][0]
    assert "ATTIVI" in text


async def test_cb_ads_confirm_off_sets_false_and_edits_message():
    from apps.backend.telegram.handlers.config import cb_ads_confirm
    pp = AsyncMock()
    pp.set_config = AsyncMock()
    pp.ads_daily_budget = AsyncMock(return_value=0.5)
    deps = _make_deps(publication_policy=pp)
    upd, query = _make_callback_update("ads_confirm:off")
    await cb_ads_confirm(deps, upd, _make_context())
    pp.set_config.assert_awaited_once_with("policy.etsy_ads_on_publish", "false")
    text = query.edit_message_text.call_args[0][0]
    assert "INATTIVI" in text


# ===========================================================================
# shop_setup.py — /shop
# ===========================================================================

async def test_shop_no_etsy_api_sends_warning():
    from apps.backend.telegram.handlers.shop_setup import cmd_shop
    deps = _make_deps()  # etsy_api = None
    upd = _make_update()
    await cmd_shop(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "non disponibile" in text.lower()


async def test_shop_success_shows_shop_name():
    from apps.backend.telegram.handlers.shop_setup import cmd_shop
    etsy = AsyncMock()
    etsy.mock_mode = False
    etsy.get_shop = AsyncMock(return_value={
        "shop_name": "MyCoolShop",
        "title": "Printables for everyone",
        "announcement": "Welcome to the shop!",
        "currency_code": "EUR",
        "is_vacation": False,
        "url": "https://www.etsy.com/shop/MyCoolShop",
    })
    deps = _make_deps(etsy_api=etsy)
    deps.pepe.memory.get_etsy_listings = AsyncMock(return_value=[
        {"state": "active"},
        {"state": "inactive"},
    ])
    upd = _make_update()
    await cmd_shop(deps, upd, _make_context())
    all_text = " ".join(c[0][0] for c in upd.message.reply_text.call_args_list)
    assert "MyCoolShop" in all_text


async def test_shop_api_error_shows_warning():
    from apps.backend.telegram.handlers.shop_setup import cmd_shop
    etsy = AsyncMock()
    etsy.mock_mode = False
    etsy.get_shop = AsyncMock(side_effect=RuntimeError("connection refused"))
    deps = _make_deps(etsy_api=etsy)
    upd = _make_update()
    await cmd_shop(deps, upd, _make_context())
    all_text = " ".join(c[0][0] for c in upd.message.reply_text.call_args_list)
    assert "Errore" in all_text


# ===========================================================================
# shop_setup.py — /shopsetup
# ===========================================================================

async def test_shopsetup_no_optimizer_sends_warning():
    from apps.backend.telegram.handlers.shop_setup import cmd_shopsetup
    deps = _make_deps()  # shop_optimizer = None
    upd = _make_update()
    await cmd_shopsetup(deps, upd, _make_context())
    text = upd.message.reply_text.call_args[0][0]
    assert "ShopProfileOptimizer non disponibile" in text


async def test_shopsetup_preview_calls_optimizer_and_shows_title():
    from apps.backend.telegram.handlers.shop_setup import cmd_shopsetup
    optimizer = AsyncMock()
    optimizer.preview = AsyncMock(return_value={
        "title": "Best Printables Shop",
        "about": "We create beautiful printables.",
        "niches": ["wedding", "birthday"],
        "changed": True,
        "last_applied_title": "Old Title",
    })
    deps = _make_deps(shop_optimizer=optimizer)
    upd = _make_update()
    await cmd_shopsetup(deps, upd, _make_context())
    optimizer.preview.assert_awaited_once_with(focus_niche=None)
    all_text = " ".join(c[0][0] for c in upd.message.reply_text.call_args_list)
    assert "Best Printables Shop" in all_text


async def test_shopsetup_preview_shows_unchanged_note():
    from apps.backend.telegram.handlers.shop_setup import cmd_shopsetup
    optimizer = AsyncMock()
    optimizer.preview = AsyncMock(return_value={
        "title": "Same Title",
        "about": "Same about.",
        "niches": ["wedding"],
        "changed": False,
        "last_applied_title": "Same Title",
    })
    deps = _make_deps(shop_optimizer=optimizer)
    upd = _make_update()
    await cmd_shopsetup(deps, upd, _make_context())
    all_text = " ".join(c[0][0] for c in upd.message.reply_text.call_args_list)
    assert "force" in all_text.lower()


async def test_shopsetup_confirm_success():
    from apps.backend.telegram.handlers.shop_setup import cmd_shopsetup
    optimizer = AsyncMock()
    optimizer.apply_shop_profile = AsyncMock(return_value={
        "status": "ok",
        "title": "Applied Title",
        "about": "Applied about.",
        "niches": ["wedding", "birthday"],
    })
    deps = _make_deps(shop_optimizer=optimizer)
    upd = _make_update()
    await cmd_shopsetup(deps, upd, _make_context("confirm"))
    optimizer.apply_shop_profile.assert_awaited_once_with(focus_niche=None, force=False)
    all_text = " ".join(c[0][0] for c in upd.message.reply_text.call_args_list)
    assert "Applied Title" in all_text


async def test_shopsetup_confirm_skipped_shows_info():
    from apps.backend.telegram.handlers.shop_setup import cmd_shopsetup
    optimizer = AsyncMock()
    optimizer.apply_shop_profile = AsyncMock(return_value={"status": "skipped"})
    deps = _make_deps(shop_optimizer=optimizer)
    upd = _make_update()
    await cmd_shopsetup(deps, upd, _make_context("confirm"))
    all_text = " ".join(c[0][0] for c in upd.message.reply_text.call_args_list)
    assert "cambiate" in all_text.lower()


async def test_shopsetup_force_calls_apply_with_force_true():
    from apps.backend.telegram.handlers.shop_setup import cmd_shopsetup
    optimizer = AsyncMock()
    optimizer.apply_shop_profile = AsyncMock(return_value={
        "status": "ok",
        "title": "Forced Title",
        "about": "Forced about.",
        "niches": ["birthday"],
    })
    deps = _make_deps(shop_optimizer=optimizer)
    upd = _make_update()
    await cmd_shopsetup(deps, upd, _make_context("force"))
    optimizer.apply_shop_profile.assert_awaited_once_with(focus_niche=None, force=True)


async def test_shopsetup_apply_raises_shows_error():
    from apps.backend.telegram.handlers.shop_setup import cmd_shopsetup
    optimizer = AsyncMock()
    optimizer.apply_shop_profile = AsyncMock(side_effect=RuntimeError("API down"))
    deps = _make_deps(shop_optimizer=optimizer)
    upd = _make_update()
    await cmd_shopsetup(deps, upd, _make_context("confirm"))
    all_text = " ".join(c[0][0] for c in upd.message.reply_text.call_args_list)
    assert "Errore" in all_text


# ===========================================================================
# shop_setup.py — cb_approve_setup
# ===========================================================================

async def test_cb_approve_setup_success_edits_message():
    from apps.backend.telegram.handlers.shop_setup import cb_approve_setup
    optimizer = AsyncMock()
    optimizer.apply_shop_profile = AsyncMock(return_value={
        "status": "ok",
        "title": "Approved Title",
        "about": "Great about section.",
        "niches": ["planners"],
    })
    deps = _make_deps(shop_optimizer=optimizer)
    upd, query = _make_callback_update("approve_setup")
    await cb_approve_setup(deps, upd, _make_context())
    query.answer.assert_awaited()
    optimizer.apply_shop_profile.assert_awaited_once()
    text = query.edit_message_text.call_args[0][0]
    assert "Approved Title" in text


async def test_cb_approve_setup_skipped_shows_info():
    from apps.backend.telegram.handlers.shop_setup import cb_approve_setup
    optimizer = AsyncMock()
    optimizer.apply_shop_profile = AsyncMock(return_value={"status": "skipped"})
    deps = _make_deps(shop_optimizer=optimizer)
    upd, query = _make_callback_update("approve_setup")
    await cb_approve_setup(deps, upd, _make_context())
    text = query.edit_message_text.call_args[0][0]
    assert "cambiate" in text.lower()


async def test_cb_approve_setup_dedup_ignores_double_tap():
    from apps.backend.telegram.handlers.shop_setup import (
        cb_approve_setup,
        _processed_setup_approvals,
    )
    optimizer = AsyncMock()
    optimizer.apply_shop_profile = AsyncMock(return_value={
        "status": "ok",
        "title": "T",
        "about": "A",
        "niches": [],
    })
    deps = _make_deps(shop_optimizer=optimizer)
    upd, query = _make_callback_update("approve_setup")
    # Pre-seed the dedup set with this callback id
    _processed_setup_approvals.add(query.id)
    await cb_approve_setup(deps, upd, _make_context())
    # Should return early — apply_shop_profile must NOT be called
    optimizer.apply_shop_profile.assert_not_awaited()


# ===========================================================================
# shop_setup.py — cb_skip_setup
# ===========================================================================

async def test_cb_skip_setup_answers_and_removes_keyboard():
    from apps.backend.telegram.handlers.shop_setup import cb_skip_setup
    deps = _make_deps()
    upd, query = _make_callback_update("skip_setup")
    await cb_skip_setup(deps, upd, _make_context())
    query.answer.assert_awaited_once()
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
