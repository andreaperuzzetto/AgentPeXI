# MOCK CONTRACT — AutopilotLoop
# ============================================================
# AutopilotLoop assembles 5 mixins:
#   _CommandsMixin, _LoopMixin, _DecisionMixin, _ApprovalMixin, _StateMixin
#
# External dependency mock patterns:
#   queue.get_pending_approval()                → AsyncMock(return_value=[])
#   queue.get_items_by_status(status)           → AsyncMock(return_value=[])
#   queue.get_item(item_id)                     → AsyncMock(return_value=None | item)
#   queue.create_item(niche, product_type, ...) → AsyncMock(return_value=42)
#   queue.assign_slot(item_id, timestamp)       → AsyncMock(return_value=None)
#   queue.set_skipped(item_id, reason)          → AsyncMock(return_value=None)
#   queue.get_last_skipped(limit, reason)       → AsyncMock(return_value=[])
#   queue.discard_stale_approvals()             → AsyncMock(return_value=0)
#   budget.check_budget()                       → AsyncMock(return_value=BudgetStatus.OK)
#   budget.get_status_summary()                 → AsyncMock(return_value=<summary>)
#   policy.is_in_availability_window()          → AsyncMock(return_value=True)
#   policy.can_publish_today()                  → AsyncMock(return_value=True)
#   policy.next_available_slot()                → AsyncMock(return_value=datetime+2h)
#   policy.published_today_count()              → AsyncMock(return_value=0)
#   policy._get_int(key, default)               → AsyncMock(return_value=5)
#   bot_send(text)                              → AsyncMock(return_value=None)
#   bot_send_photo(path, caption)               → AsyncMock(return_value=None)
#   bot_send_media_group(paths, caption=...)    → AsyncMock(return_value=None)
#   bot_send_markup(text, keyboard)             → AsyncMock(return_value=None)
#   design_pipeline(item_id, niche_data)        → AsyncMock(return_value=None)
#   niche_picker()                              → AsyncMock(return_value={"niche": "art"})
#   bundle_checker()                            → AsyncMock(return_value=None)
#
# Note: _approval_lock and _cmd_lock are REAL asyncio.Lock() — never mock them.
# Note: patch build_approval_keyboard via
#       "apps.backend.telegram.callbacks.build_approval_keyboard"
"""Tests for AutopilotLoop coverage.

Covers _commands_mixin, _loop_mixin, _approval_mixin, _decision_mixin,
_state_mixin.  Does NOT duplicate tests already in test_autopilot_loop.py.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from apps.backend.core.autopilot_loop import AutopilotLoop
from apps.backend.core.budget_manager import BudgetStatus
from apps.backend.core._autopilot._constants import (
    LOOP_SLEEP_BUDGET,
    LOOP_SLEEP_EMPTY,
    LOOP_SLEEP_NIGHT,
    LOOP_SLEEP_NORMAL,
    LOOP_SLEEP_PAUSED,
    LOOP_SLEEP_QUOTA,
)

# ---------------------------------------------------------------------------
# Schema (shared with test_autopilot_loop.py)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS production_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL DEFAULT (hex(randomblob(8))),
    product_type TEXT NOT NULL DEFAULT 'printable_pdf',
    niche TEXT NOT NULL DEFAULT '',
    brief TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending_design',
    keywords TEXT,
    entry_score REAL DEFAULT 0.0,
    design_prompt TEXT,
    image_url TEXT,
    thumbnail_path TEXT,
    listing_title TEXT,
    listing_description TEXT,
    listing_tags TEXT,
    listing_price REAL,
    approval_sent_at REAL,
    approval_message_id INTEGER,
    approval_chat_id INTEGER,
    skip_reason TEXT,
    skip_count_user INTEGER DEFAULT 0,
    skip_count_timeout INTEGER DEFAULT 0,
    error_message TEXT,
    scheduled_publish_at REAL,
    published_at REAL,
    etsy_listing_id TEXT,
    llm_cost_usd REAL DEFAULT 0.0,
    image_cost_usd REAL DEFAULT 0.0,
    listing_fee_usd REAL DEFAULT 0.20,
    ads_activated INTEGER DEFAULT 0,
    ads_paused INTEGER DEFAULT 0,
    loop_run_id TEXT,
    ab_price_variant TEXT,
    file_paths TEXT,
    product_tier TEXT DEFAULT 'core',
    cluster_id TEXT,
    release_order INTEGER NOT NULL DEFAULT 0,
    etsy_listing_url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS autopilot_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS niche_intelligence (
    niche TEXT PRIMARY KEY,
    product_type TEXT,
    performance_score REAL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL DEFAULT 0
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_item(
    item_id: int = 1,
    niche: str = "test_niche",
    product_type: str = "printable_pdf",
    status: str = "pending_approval",
    entry_score: float = 0.8,
    keywords: list | None = None,
    listing_title: str = "Test Title",
    listing_price: float = 9.99,
    thumbnail_path: str | None = None,
    llm_cost_usd: float = 0.01,
    image_cost_usd: float = 0.02,
    skip_reason: str | None = None,
) -> MagicMock:
    item = MagicMock()
    item.id = item_id
    item.niche = niche
    item.product_type = product_type
    item.status = status
    item.entry_score = entry_score
    item.keywords = keywords if keywords is not None else ["kw1", "kw2"]
    item.listing_title = listing_title
    item.listing_price = listing_price
    item.thumbnail_path = thumbnail_path
    item.llm_cost_usd = llm_cost_usd
    item.image_cost_usd = image_cost_usd
    item.skip_reason = skip_reason
    return item


def _make_budget_summary() -> MagicMock:
    s = MagicMock()
    s.llm_today = 0.005
    s.llm_limit = 10.0
    s.llm_pct = 0.05
    s.image_today = 0.003
    s.image_limit = 5.0
    s.image_pct = 0.03
    s.fee_today = 0.20
    s.fee_limit = 20.0
    s.fee_pct = 0.01
    s.status = BudgetStatus.OK
    return s


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_SCHEMA)
        await conn.commit()
        yield conn


@pytest.fixture
def mock_queue():
    q = AsyncMock()
    q.get_pending_approval = AsyncMock(return_value=[])
    q.get_items_by_status = AsyncMock(return_value=[])
    q.get_item = AsyncMock(return_value=None)
    q.create_item = AsyncMock(return_value=42)
    q.assign_slot = AsyncMock(return_value=None)
    q.set_skipped = AsyncMock(return_value=None)
    q.get_last_skipped = AsyncMock(return_value=[])
    q.discard_stale_approvals = AsyncMock(return_value=0)
    return q


@pytest.fixture
def mock_budget():
    b = AsyncMock()
    b.check_budget = AsyncMock(return_value=BudgetStatus.OK)
    b.get_status_summary = AsyncMock(return_value=_make_budget_summary())
    return b


@pytest.fixture
def mock_policy():
    p = AsyncMock()
    p.is_in_availability_window = AsyncMock(return_value=True)
    p.can_publish_today = AsyncMock(return_value=True)
    p.next_available_slot = AsyncMock(return_value=datetime.now() + timedelta(hours=2))
    p.published_today_count = AsyncMock(return_value=2)
    p._get_int = AsyncMock(return_value=5)
    return p


@pytest.fixture
def bot_send():
    return AsyncMock()


@pytest.fixture
async def loop_fix(db, mock_queue, mock_budget, mock_policy, bot_send):
    loop = AutopilotLoop(
        db=db,
        queue=mock_queue,
        budget=mock_budget,
        policy=mock_policy,
        bot_send=bot_send,
    )
    yield loop, mock_queue, db


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

async def _cancel_all_tasks(loop: AutopilotLoop) -> None:
    """Cancel and await all background tasks to prevent asyncio warnings."""
    for t in list(loop._bg_tasks):
        t.cancel()
    if loop._bg_tasks:
        await asyncio.gather(*loop._bg_tasks, return_exceptions=True)
    loop._bg_tasks.clear()
    if loop._loop_task and not loop._loop_task.done():
        loop._loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop._loop_task


# ===========================================================================
# _state_mixin
# ===========================================================================

async def test_get_quota_resume_valid_timestamp(loop_fix):
    """_get_quota_resume returns stored datetime when value is a valid timestamp."""
    loop, _, _ = loop_fix
    dt = datetime(2025, 6, 15, 10, 0, 0)
    await loop._set_quota_resume(dt)
    result = await loop._get_quota_resume()
    assert abs((result - dt).total_seconds()) < 1


async def test_get_quota_resume_default_returns_datetime(loop_fix):
    """_get_quota_resume with no stored value returns a valid datetime."""
    loop, _, _ = loop_fix
    result = await loop._get_quota_resume()
    assert isinstance(result, datetime)


async def test_get_quota_resume_invalid_value_returns_now(loop_fix):
    """_get_quota_resume returns datetime.now() when stored value raises ValueError."""
    loop, _, db = loop_fix
    await db.execute(
        "INSERT OR REPLACE INTO autopilot_state(key, value, updated_at) VALUES(?, ?, ?)",
        ("loop.quota_resume_at", "not_a_timestamp", 0.0),
    )
    await db.commit()
    result = await loop._get_quota_resume()
    assert isinstance(result, datetime)


async def test_set_quota_resume_persists(loop_fix):
    """_set_quota_resume stores the datetime and _get_quota_resume retrieves it."""
    loop, _, _ = loop_fix
    dt = datetime(2025, 12, 31, 8, 0, 0)
    await loop._set_quota_resume(dt)
    retrieved = await loop._get_quota_resume()
    assert abs((retrieved - dt).total_seconds()) < 1


async def test_get_user_skip_count_invalid_value_returns_zero(loop_fix):
    """_get_user_skip_count returns 0 when stored value cannot be parsed as int."""
    loop, _, db = loop_fix
    await db.execute(
        "INSERT OR REPLACE INTO autopilot_state(key, value, updated_at) VALUES(?, ?, ?)",
        ("loop.consecutive_user_skips", "bad_value", 0.0),
    )
    await db.commit()
    result = await loop._get_user_skip_count()
    assert result == 0


async def test_get_timeout_count_invalid_value_returns_zero(loop_fix):
    """_get_timeout_count returns 0 when stored value cannot be parsed as int."""
    loop, _, db = loop_fix
    await db.execute(
        "INSERT OR REPLACE INTO autopilot_state(key, value, updated_at) VALUES(?, ?, ?)",
        ("loop.consecutive_timeouts", "bad_value", 0.0),
    )
    await db.commit()
    result = await loop._get_timeout_count()
    assert result == 0


async def test_increment_user_skip_increments_correctly(loop_fix):
    """_increment_user_skip increments and returns the new counter value."""
    loop, _, _ = loop_fix
    n1 = await loop._increment_user_skip()
    n2 = await loop._increment_user_skip()
    assert n1 == 1
    assert n2 == 2


async def test_reset_skip_counters_clears_both(loop_fix):
    """_reset_skip_counters sets both user and timeout skip counters to 0."""
    loop, _, _ = loop_fix
    await loop._increment_user_skip()
    await loop._increment_user_skip()
    await loop._increment_timeout_skip()
    await loop._reset_skip_counters()
    assert await loop._get_user_skip_count() == 0
    assert await loop._get_timeout_count() == 0


# ===========================================================================
# _decision_mixin
# ===========================================================================

async def test_handle_decision_approved_assigns_slot_and_resets(loop_fix, bot_send):
    """_handle_decision('approved') assigns slot, resets skip counters, notifies."""
    loop, queue, _ = loop_fix
    slot = datetime.now() + timedelta(hours=2)
    loop.policy.next_available_slot = AsyncMock(return_value=slot)

    await loop._handle_decision(1, "approved")

    queue.assign_slot.assert_called_once_with(1, slot.timestamp())
    assert await loop._get_user_skip_count() == 0
    bot_send.assert_called_once()
    assert "Approvato" in bot_send.call_args[0][0]


async def test_handle_decision_skipped_user_below_threshold(loop_fix, bot_send):
    """_handle_decision('skipped_user') with consec < 3 sends a skip count message."""
    loop, queue, _ = loop_fix

    await loop._handle_decision(1, "skipped_user")

    queue.set_skipped.assert_called_once_with(1, "user")
    bot_send.assert_called_once()
    assert "1/3" in bot_send.call_args[0][0]


async def test_handle_decision_skipped_user_triggers_pause(loop_fix):
    """_handle_decision('skipped_user') with consec >= 3 triggers _handle_skip_pause."""
    loop, queue, _ = loop_fix
    queue.get_last_skipped = AsyncMock(return_value=[])
    await loop._increment_user_skip()
    await loop._increment_user_skip()

    await loop._handle_decision(1, "skipped_user")

    assert await loop._get_status() == "paused_skip"


async def test_handle_decision_skipped_budget_is_noop(loop_fix, bot_send):
    """_handle_decision('skipped_budget') is a no-op — budget was handled in the loop."""
    loop, queue, _ = loop_fix

    await loop._handle_decision(1, "skipped_budget")

    bot_send.assert_not_called()
    queue.set_skipped.assert_not_called()


async def test_handle_decision_unknown_logs_warning(loop_fix, bot_send):
    """_handle_decision with an unrecognised decision logs a warning, doesn't raise."""
    loop, _, _ = loop_fix

    await loop._handle_decision(1, "totally_unknown_decision")

    bot_send.assert_not_called()


async def test_handle_skip_pause_no_photos(loop_fix, bot_send):
    """_handle_skip_pause sends plain text when no thumbnails are available."""
    loop, queue, _ = loop_fix
    items = [_make_mock_item(thumbnail_path=None) for _ in range(3)]
    queue.get_last_skipped = AsyncMock(return_value=items)

    await loop._handle_skip_pause()

    assert await loop._get_status() == "paused_skip"
    bot_send.assert_called_once()
    assert "3 listing" in bot_send.call_args[0][0]


async def test_handle_skip_pause_with_photos(loop_fix, bot_send):
    """_handle_skip_pause sends media group when thumbnails are available."""
    loop, queue, _ = loop_fix
    items = [_make_mock_item(thumbnail_path="/tmp/t1.jpg")]
    queue.get_last_skipped = AsyncMock(return_value=items)
    media_mock = AsyncMock()
    loop._bot_send_media_group = media_mock

    await loop._handle_skip_pause()

    media_mock.assert_called_once()
    bot_send.assert_not_called()


async def test_handle_skip_pause_photos_fail_falls_back_to_text(loop_fix, bot_send):
    """_handle_skip_pause falls back to plain text when media group send raises."""
    loop, queue, _ = loop_fix
    items = [_make_mock_item(thumbnail_path="/tmp/t1.jpg")]
    queue.get_last_skipped = AsyncMock(return_value=items)
    loop._bot_send_media_group = AsyncMock(side_effect=Exception("Telegram error"))

    await loop._handle_skip_pause()

    bot_send.assert_called_once()


async def test_handle_timeout_pause_sets_status_and_notifies(loop_fix, bot_send):
    """_handle_timeout_pause sets status to paused_manual and sends a message."""
    loop, _, _ = loop_fix

    await loop._handle_timeout_pause()

    assert await loop._get_status() == "paused_manual"
    bot_send.assert_called_once()
    assert "timeout" in bot_send.call_args[0][0].lower()


# ===========================================================================
# _approval_mixin
# ===========================================================================

async def test_send_approval_notification_item_not_found(loop_fix, bot_send):
    """_send_approval_notification returns early when the item is not in the queue."""
    loop, queue, _ = loop_fix
    queue.get_item = AsyncMock(return_value=None)

    await loop._send_approval_notification(99)

    bot_send.assert_not_called()


async def test_send_approval_notification_text_fallback(loop_fix, bot_send):
    """_send_approval_notification sends plain text when no thumbnail and no markup."""
    loop, queue, _ = loop_fix
    item = _make_mock_item(thumbnail_path=None)
    queue.get_item = AsyncMock(return_value=item)
    loop._bot_send_markup = None

    with patch("apps.backend.telegram.callbacks.build_approval_keyboard",
               return_value=MagicMock()):
        await loop._send_approval_notification(item.id)

    bot_send.assert_called_once()
    assert str(item.id) in bot_send.call_args[0][0]


async def test_send_approval_notification_registers_event(loop_fix):
    """_send_approval_notification registers an asyncio.Event in _approval_events."""
    loop, queue, _ = loop_fix
    item = _make_mock_item(thumbnail_path=None)
    queue.get_item = AsyncMock(return_value=item)
    loop._bot_send_markup = None

    with patch("apps.backend.telegram.callbacks.build_approval_keyboard",
               return_value=MagicMock()):
        await loop._send_approval_notification(item.id)

    assert item.id in loop._approval_events


async def test_send_approval_notification_with_thumbnail(loop_fix, bot_send):
    """_send_approval_notification sends photo when thumbnail_path is set."""
    loop, queue, _ = loop_fix
    item = _make_mock_item(thumbnail_path="/tmp/thumb.jpg")
    queue.get_item = AsyncMock(return_value=item)
    photo_mock = AsyncMock()
    loop._bot_send_photo = photo_mock

    with patch("apps.backend.telegram.callbacks.build_approval_keyboard",
               return_value=MagicMock()):
        await loop._send_approval_notification(item.id)

    photo_mock.assert_called_once()
    assert photo_mock.call_args[0][0] == "/tmp/thumb.jpg"
    bot_send.assert_not_called()


async def test_send_approval_notification_thumbnail_fails_uses_markup(loop_fix, bot_send):
    """Thumbnail send failure falls back to markup send."""
    loop, queue, _ = loop_fix
    item = _make_mock_item(thumbnail_path="/tmp/thumb.jpg")
    queue.get_item = AsyncMock(return_value=item)
    loop._bot_send_photo = AsyncMock(side_effect=Exception("photo fail"))
    markup_mock = AsyncMock()
    loop._bot_send_markup = markup_mock

    with patch("apps.backend.telegram.callbacks.build_approval_keyboard",
               return_value=MagicMock()):
        await loop._send_approval_notification(item.id)

    markup_mock.assert_called_once()
    bot_send.assert_not_called()


async def test_send_approval_notification_all_fail_text_fallback(loop_fix, bot_send):
    """When thumbnail and markup both fail, falls back to plain text."""
    loop, queue, _ = loop_fix
    item = _make_mock_item(thumbnail_path="/tmp/thumb.jpg")
    queue.get_item = AsyncMock(return_value=item)
    loop._bot_send_photo = AsyncMock(side_effect=Exception("photo fail"))
    loop._bot_send_markup = AsyncMock(side_effect=Exception("markup fail"))

    with patch("apps.backend.telegram.callbacks.build_approval_keyboard",
               return_value=MagicMock()):
        await loop._send_approval_notification(item.id)

    bot_send.assert_called_once()


async def test_wait_for_approval_event_timeout_hits_except_clause(loop_fix):
    """asyncio.TimeoutError path (lines 94-95) is hit when event never fires."""
    loop, queue, _ = loop_fix
    evt = asyncio.Event()  # deliberately never set
    loop._approval_events[1] = evt
    queue.get_item = AsyncMock(return_value=_make_mock_item(status="approved"))

    with patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_TIMEOUT", 999), \
         patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_POLL", 0.001):
        result = await asyncio.wait_for(loop._wait_for_approval(1), timeout=5.0)

    assert result == "approved"


async def test_wait_for_approval_no_event_sleeps_then_db_poll(loop_fix):
    """No-event path (else branch, line 102): sleeps APPROVAL_POLL then polls DB."""
    loop, queue, _ = loop_fix
    queue.get_item = AsyncMock(return_value=_make_mock_item(status="approved"))

    with patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_TIMEOUT", 999), \
         patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_POLL", 0.001):
        result = await asyncio.wait_for(loop._wait_for_approval(1), timeout=5.0)

    assert result == "approved"


async def test_wait_for_approval_db_returns_skipped(loop_fix):
    """_wait_for_approval returns 'skipped_<reason>' when DB item is skipped."""
    loop, queue, _ = loop_fix
    queue.get_item = AsyncMock(
        return_value=_make_mock_item(status="skipped", skip_reason="user")
    )

    with patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_TIMEOUT", 999), \
         patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_POLL", 0.001):
        result = await asyncio.wait_for(loop._wait_for_approval(1), timeout=5.0)

    assert result == "skipped_user"


async def test_wait_for_approval_db_returns_discarded(loop_fix):
    """_wait_for_approval returns 'discarded' when DB item is discarded."""
    loop, queue, _ = loop_fix
    queue.get_item = AsyncMock(return_value=_make_mock_item(status="discarded"))

    with patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_TIMEOUT", 999), \
         patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_POLL", 0.001):
        result = await asyncio.wait_for(loop._wait_for_approval(1), timeout=5.0)

    assert result == "discarded"


async def test_wait_for_approval_budget_exceeded_during_wait(loop_fix):
    """_wait_for_approval returns 'skipped_budget' when budget is EXCEEDED mid-wait."""
    loop, queue, _ = loop_fix
    queue.get_item = AsyncMock(
        return_value=_make_mock_item(status="pending_approval")
    )
    loop.budget.check_budget = AsyncMock(return_value=BudgetStatus.EXCEEDED)

    with patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_TIMEOUT", 999), \
         patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_POLL", 0.001):
        result = await asyncio.wait_for(loop._wait_for_approval(1), timeout=5.0)

    assert result == "skipped_budget"
    queue.set_skipped.assert_called_with(1, "budget")


async def test_wait_for_approval_out_of_window_sleeps_paused(loop_fix):
    """Out-of-availability-window path triggers asyncio.sleep(LOOP_SLEEP_PAUSED)."""
    loop, queue, _ = loop_fix
    call_count = 0

    async def get_item_side(_id):
        nonlocal call_count
        call_count += 1
        return _make_mock_item(
            status="pending_approval" if call_count == 1 else "approved"
        )

    queue.get_item = get_item_side
    loop.budget.check_budget = AsyncMock(return_value=BudgetStatus.OK)
    loop.policy.is_in_availability_window = AsyncMock(side_effect=[False, True])

    sleep_calls: list[float] = []

    async def capturing_sleep(n: float) -> None:
        sleep_calls.append(n)

    with patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_TIMEOUT", 999), \
         patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_POLL", 0.001), \
         patch("asyncio.sleep", capturing_sleep):
        result = await asyncio.wait_for(loop._wait_for_approval(1), timeout=5.0)

    assert result == "approved"
    assert LOOP_SLEEP_PAUSED in sleep_calls


# ===========================================================================
# _commands_mixin
# ===========================================================================

async def test_cmd_run_already_running_with_pending(loop_fix, bot_send):
    """cmd_run when already running shows pending approval items."""
    loop, queue, _ = loop_fix
    await loop._set_status("running")
    loop._running = True
    queue.get_pending_approval = AsyncMock(return_value=[_make_mock_item(item_id=5)])

    result = await loop.cmd_run()

    assert "già in esecuzione" in result
    assert "Item 5" in result


async def test_cmd_run_already_running_no_pending(loop_fix):
    """cmd_run when already running and no pending items returns simple message."""
    loop, queue, _ = loop_fix
    await loop._set_status("running")
    loop._running = True
    queue.get_pending_approval = AsyncMock(return_value=[])

    result = await loop.cmd_run()

    assert result == "▶️ Loop già in esecuzione."


async def test_cmd_run_starts_with_pending(loop_fix, bot_send):
    """cmd_run when stopped calls resume() and shows pending items in response."""
    loop, queue, _ = loop_fix
    loop._running = False
    queue.get_pending_approval = AsyncMock(
        return_value=[_make_mock_item(item_id=3)]
    )
    loop.resume = AsyncMock()

    result = await loop.cmd_run()

    loop.resume.assert_called_once()
    assert "AutopilotLoop avviato" in result
    assert "Item 3" in result


async def test_cmd_run_starts_no_pending(loop_fix):
    """cmd_run when stopped calls resume() and returns simple started message."""
    loop, queue, _ = loop_fix
    loop._running = False
    queue.get_pending_approval = AsyncMock(return_value=[])
    loop.resume = AsyncMock()

    result = await loop.cmd_run()

    loop.resume.assert_called_once()
    assert result == "▶️ AutopilotLoop avviato."


async def test_cmd_stop_returns_pause_message(loop_fix):
    """cmd_stop calls stop() and returns a pause message with /run hint."""
    loop, _, _ = loop_fix
    loop._running = False

    result = await loop.cmd_stop()

    assert "in pausa" in result
    assert "/run" in result


async def test_cmd_queue_clear_discards_pending_and_clears_state(loop_fix):
    """cmd_queue('clear') updates DB items to discarded and wipes in-memory state."""
    loop, _, db = loop_fix
    await db.execute(
        "INSERT INTO production_queue (niche, product_type, status, entry_score)"
        " VALUES (?, ?, ?, ?)",
        ("niche1", "printable_pdf", "pending_approval", 0.5),
    )
    await db.commit()
    loop._approval_events[1] = asyncio.Event()
    loop._approval_results[1] = "approved"

    result = await loop.cmd_queue("clear")

    assert "svuotata" in result
    assert not loop._approval_events
    assert not loop._approval_results

    cursor = await db.execute(
        "SELECT status FROM production_queue WHERE niche='niche1'"
    )
    row = await cursor.fetchone()
    assert row[0] == "discarded"


async def test_cmd_queue_shows_status_counts(loop_fix):
    """cmd_queue() without args shows item counts grouped by status."""
    loop, queue, _ = loop_fix
    item = _make_mock_item(item_id=7, status="pending_approval")

    async def get_items(st: str):
        return [item] if st == "pending_approval" else []

    queue.get_items_by_status = get_items

    result = await loop.cmd_queue()

    assert "pending_approval: 1" in result
    assert "id=7" in result
    assert "Totale: 1 item" in result


async def test_cmd_queue_empty_shows_zero_total(loop_fix):
    """cmd_queue() with empty queue reports 0 total items."""
    loop, queue, _ = loop_fix
    queue.get_items_by_status = AsyncMock(return_value=[])

    result = await loop.cmd_queue("")

    assert "Totale: 0 item" in result


async def test_cmd_status_returns_full_status_string(loop_fix, mock_budget, mock_policy):
    """cmd_status returns a formatted status string with all budget/policy metrics."""
    loop, queue, _ = loop_fix
    queue.get_pending_approval = AsyncMock(return_value=[_make_mock_item()])
    queue.get_items_by_status = AsyncMock(return_value=[])
    mock_budget.get_status_summary = AsyncMock(return_value=_make_budget_summary())
    mock_policy.published_today_count = AsyncMock(return_value=2)
    mock_policy._get_int = AsyncMock(return_value=5)

    result = await loop.cmd_status()

    assert "AutopilotLoop" in result
    assert "Budget" in result
    assert "LLM" in result
    assert "2/5" in result


def test_tomorrow_08_00_returns_next_day_at_8():
    """_tomorrow_08_00 returns tomorrow's date at exactly 08:00:00."""
    dt = AutopilotLoop._tomorrow_08_00()
    assert dt.hour == 8
    assert dt.minute == 0
    assert dt.second == 0
    assert dt.microsecond == 0
    assert dt.date() > datetime.now().date()


# ===========================================================================
# _loop_mixin — noop fallbacks
# ===========================================================================

async def test_noop_photo_forwards_caption(loop_fix, bot_send):
    """_noop_photo forwards the caption string to _bot_send."""
    loop, _, _ = loop_fix
    await loop._noop_photo("/tmp/photo.jpg", "the caption")
    bot_send.assert_called_once_with("the caption")


async def test_noop_media_forwards_caption(loop_fix, bot_send):
    """_noop_media forwards the caption string to _bot_send."""
    loop, _, _ = loop_fix
    await loop._noop_media(["/tmp/a.jpg", "/tmp/b.jpg"], "media caption")
    bot_send.assert_called_once_with("media caption")


async def test_noop_design_does_not_raise(loop_fix):
    """_noop_design logs a warning and does not raise."""
    loop, _, _ = loop_fix
    await loop._noop_design(42, {"niche": "art", "product_type": "print"})


# ===========================================================================
# _loop_mixin — _add_bg_task
# ===========================================================================

async def test_add_bg_task_registers_and_auto_discards(loop_fix):
    """_add_bg_task adds task to _bg_tasks and the done_callback removes it."""
    loop, _, _ = loop_fix
    done = asyncio.Event()

    async def _quick() -> None:
        done.set()

    task = loop._add_bg_task(_quick())
    assert task in loop._bg_tasks

    await asyncio.wait_for(done.wait(), timeout=2.0)
    await asyncio.sleep(0)  # Yield to event loop so the done_callback fires
    assert task not in loop._bg_tasks


# ===========================================================================
# _loop_mixin — default pickers
# ===========================================================================

async def test_default_niche_picker_returns_top_row(loop_fix):
    """_default_niche_picker queries niche_intelligence and returns the best row."""
    loop, _, db = loop_fix
    await db.execute(
        "INSERT INTO niche_intelligence (niche, product_type, performance_score)"
        " VALUES (?, ?, ?)",
        ("wall_art", "digital_print", 0.95),
    )
    await db.commit()

    result = await loop._default_niche_picker()

    assert result == {"niche": "wall_art", "product_type": "digital_print"}


async def test_default_niche_picker_returns_none_when_empty(loop_fix):
    """_default_niche_picker returns None when niche_intelligence is empty."""
    loop, _, _ = loop_fix
    result = await loop._default_niche_picker()
    assert result is None


async def test_default_niche_picker_returns_none_on_db_error(loop_fix):
    """_default_niche_picker catches DB exceptions and returns None."""
    loop, _, _ = loop_fix
    with patch.object(
        loop._db, "execute", new=AsyncMock(side_effect=Exception("db error"))
    ):
        result = await loop._default_niche_picker()
    assert result is None


async def test_default_bundle_checker_returns_none(loop_fix):
    """_default_bundle_checker always returns None (placeholder for Block 4)."""
    loop, _, _ = loop_fix
    result = await loop._default_bundle_checker()
    assert result is None


# ===========================================================================
# _loop_mixin — start / stop(final=True)
# ===========================================================================

async def test_start_sets_running_and_creates_loop_task(loop_fix):
    """start() sets _running=True, status='running', and creates a loop_task."""
    loop, _, _ = loop_fix
    loop.run_loop = AsyncMock()

    await loop.start()

    assert loop._running is True
    assert loop._loop_task is not None
    assert await loop._get_status() == "running"
    await _cancel_all_tasks(loop)


async def test_stop_final_sets_idle_and_clears_niche(loop_fix):
    """stop(final=True) sets status='idle' and clears loop.current_niche (line 96)."""
    loop, _, _ = loop_fix
    await loop._state_set("loop.current_niche", "art_prints")
    loop._running = True
    loop._loop_task = asyncio.create_task(asyncio.sleep(999))

    await loop.stop(final=True)

    assert loop._running is False
    assert await loop._get_status() == "idle"
    assert await loop._state_get("loop.current_niche") == ""


# ===========================================================================
# _loop_mixin — run_loop
# ===========================================================================

async def test_run_loop_exits_immediately_when_not_running(loop_fix):
    """run_loop exits immediately when _running is False."""
    loop, _, _ = loop_fix
    loop._running = False
    await asyncio.wait_for(loop.run_loop(), timeout=2.0)


async def test_run_loop_handles_tick_exception_and_retries(loop_fix):
    """run_loop catches tick exceptions, sleeps LOOP_SLEEP_NORMAL, and retries."""
    loop, _, _ = loop_fix
    loop._running = True
    call_count = 0

    async def _mock_tick() -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated tick error")
        loop._running = False

    loop._tick = _mock_tick

    with patch("asyncio.sleep", AsyncMock()):
        await asyncio.wait_for(loop.run_loop(), timeout=5.0)

    assert call_count == 2


# ===========================================================================
# _loop_mixin — _tick: paused states
# ===========================================================================

async def test_tick_paused_budget_sleeps(loop_fix):
    """_tick sleeps LOOP_SLEEP_BUDGET when status is paused_budget."""
    loop, _, _ = loop_fix
    await loop._set_status("paused_budget")
    loop._first_iteration = False
    sleep_calls: list[float] = []

    async def mock_sleep(n: float) -> None:
        sleep_calls.append(n)

    with patch("asyncio.sleep", mock_sleep):
        await loop._tick()

    assert LOOP_SLEEP_BUDGET in sleep_calls


async def test_tick_paused_skip_sleeps(loop_fix):
    """_tick sleeps LOOP_SLEEP_PAUSED when status is paused_skip."""
    loop, _, _ = loop_fix
    await loop._set_status("paused_skip")
    loop._first_iteration = False

    with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        await loop._tick()

    mock_sleep.assert_called_with(LOOP_SLEEP_PAUSED)


async def test_tick_paused_manual_sleeps(loop_fix):
    """_tick sleeps LOOP_SLEEP_PAUSED when status is paused_manual."""
    loop, _, _ = loop_fix
    await loop._set_status("paused_manual")
    loop._first_iteration = False

    with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        await loop._tick()

    mock_sleep.assert_called_with(LOOP_SLEEP_PAUSED)


async def test_tick_paused_quota_expired_resets_to_running(loop_fix):
    """_tick resets status to 'running' when quota_resume time has passed."""
    loop, queue, _ = loop_fix
    await loop._set_status("paused_quota")
    await loop._set_quota_resume(datetime.now() - timedelta(minutes=5))
    loop._first_iteration = False
    queue.get_pending_approval = AsyncMock(return_value=[])
    queue.get_items_by_status = AsyncMock(return_value=[])
    loop._bundle_checker = AsyncMock(return_value=None)
    loop._niche_picker = AsyncMock(return_value=None)

    with patch("asyncio.sleep", AsyncMock()):
        await loop._tick()

    assert await loop._get_status() == "running"


async def test_tick_paused_quota_not_yet_sleeps(loop_fix):
    """_tick sleeps LOOP_SLEEP_QUOTA when quota_resume time is in the future."""
    loop, _, _ = loop_fix
    await loop._set_status("paused_quota")
    await loop._set_quota_resume(datetime.now() + timedelta(hours=8))
    loop._first_iteration = False
    sleep_calls: list[float] = []

    async def mock_sleep(n: float) -> None:
        sleep_calls.append(n)

    with patch("asyncio.sleep", mock_sleep):
        await loop._tick()

    assert LOOP_SLEEP_QUOTA in sleep_calls


async def test_tick_out_of_window_sleeps_night(loop_fix):
    """_tick sleeps LOOP_SLEEP_NIGHT when outside the availability window."""
    loop, _, _ = loop_fix
    loop._first_iteration = False
    await loop._set_status("running")
    loop.policy.is_in_availability_window = AsyncMock(return_value=False)

    with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        await loop._tick()

    mock_sleep.assert_called_with(LOOP_SLEEP_NIGHT)


# ===========================================================================
# _loop_mixin — _tick: budget / quota checks
# ===========================================================================

async def test_tick_budget_exceeded_pauses_and_notifies(loop_fix, bot_send):
    """_tick sets status=paused_budget and notifies when budget is EXCEEDED."""
    loop, _, _ = loop_fix
    loop._first_iteration = False
    await loop._set_status("running")
    loop.budget.check_budget = AsyncMock(return_value=BudgetStatus.EXCEEDED)

    await loop._tick()

    assert await loop._get_status() == "paused_budget"
    bot_send.assert_called_once()
    assert "Budget" in bot_send.call_args[0][0]


async def test_tick_budget_warning_sends_warning_message(loop_fix, bot_send):
    """_tick sends a budget-warning message when BudgetStatus is WARNING."""
    loop, queue, _ = loop_fix
    loop._first_iteration = False
    await loop._set_status("running")
    loop.budget.check_budget = AsyncMock(return_value=BudgetStatus.WARNING)
    queue.get_pending_approval = AsyncMock(return_value=[])
    queue.get_items_by_status = AsyncMock(return_value=[])
    loop._bundle_checker = AsyncMock(return_value=None)
    loop._niche_picker = AsyncMock(return_value=None)

    with patch("asyncio.sleep", AsyncMock()):
        await loop._tick()

    texts = [str(c) for c in bot_send.call_args_list]
    assert any("75%" in t or "⚠️" in t for t in texts)


async def test_tick_quota_exceeded_pauses_and_notifies(loop_fix, bot_send):
    """_tick sets status=paused_quota and notifies when daily quota is reached."""
    loop, _, _ = loop_fix
    loop._first_iteration = False
    await loop._set_status("running")
    loop.budget.check_budget = AsyncMock(return_value=BudgetStatus.OK)
    loop.policy.can_publish_today = AsyncMock(return_value=False)

    with patch("asyncio.sleep", AsyncMock()):
        await loop._tick()

    assert await loop._get_status() == "paused_quota"
    bot_send.assert_called_once()
    assert "Quota" in bot_send.call_args[0][0]


# ===========================================================================
# _loop_mixin — _tick: queue depth
# ===========================================================================

async def test_tick_queue_depth_creates_event_and_recovery_task(loop_fix):
    """_tick at queue depth registers event and spawns a recovery task."""
    loop, queue, _ = loop_fix
    loop._first_iteration = False
    await loop._set_status("running")
    item = _make_mock_item(item_id=10)
    queue.get_pending_approval = AsyncMock(return_value=[item])
    queue.get_items_by_status = AsyncMock(return_value=[])
    loop._send_approval_notification = AsyncMock()
    loop._wait_for_approval = AsyncMock(return_value="approved")
    loop._handle_decision = AsyncMock()

    with patch("asyncio.sleep", AsyncMock()):
        await loop._tick()

    assert 10 in loop._approval_events
    loop._send_approval_notification.assert_called_once_with(10)
    await _cancel_all_tasks(loop)


async def test_tick_queue_depth_skips_item_with_existing_event(loop_fix):
    """_tick skips recovery for items that already have a registered event."""
    loop, queue, _ = loop_fix
    loop._first_iteration = False
    await loop._set_status("running")
    item = _make_mock_item(item_id=11)
    existing_evt = asyncio.Event()
    loop._approval_events[11] = existing_evt
    queue.get_pending_approval = AsyncMock(return_value=[item])
    queue.get_items_by_status = AsyncMock(return_value=[])
    loop._send_approval_notification = AsyncMock()

    with patch("asyncio.sleep", AsyncMock()):
        await loop._tick()

    assert loop._approval_events[11] is existing_evt
    loop._send_approval_notification.assert_not_called()


async def test_tick_queue_depth_pre_approved_sets_event(loop_fix):
    """_tick sets the event immediately for items already in _approval_results."""
    loop, queue, _ = loop_fix
    loop._first_iteration = False
    await loop._set_status("running")
    item = _make_mock_item(item_id=12)
    loop._approval_results[12] = "approved"
    queue.get_pending_approval = AsyncMock(return_value=[item])
    queue.get_items_by_status = AsyncMock(return_value=[])
    loop._send_approval_notification = AsyncMock()
    loop._wait_for_approval = AsyncMock(return_value="approved")
    loop._handle_decision = AsyncMock()

    with patch("asyncio.sleep", AsyncMock()):
        await loop._tick()

    assert 12 in loop._approval_events
    assert loop._approval_events[12].is_set()
    loop._send_approval_notification.assert_not_called()
    await _cancel_all_tasks(loop)


async def test_tick_queue_depth_notification_exception_caught(loop_fix):
    """_tick catches notification exceptions and still spawns a recovery task."""
    loop, queue, _ = loop_fix
    loop._first_iteration = False
    await loop._set_status("running")
    item = _make_mock_item(item_id=13)
    queue.get_pending_approval = AsyncMock(return_value=[item])
    queue.get_items_by_status = AsyncMock(return_value=[])
    loop._send_approval_notification = AsyncMock(side_effect=Exception("notif fail"))
    loop._wait_for_approval = AsyncMock(return_value="approved")
    loop._handle_decision = AsyncMock()

    with patch("asyncio.sleep", AsyncMock()):
        await loop._tick()

    assert 13 in loop._approval_events
    await _cancel_all_tasks(loop)


# ===========================================================================
# _loop_mixin — _tick: full pipeline run
# ===========================================================================

async def test_tick_niche_from_bundle_checker(loop_fix):
    """_tick uses bundle_checker result and skips niche_picker when bundle returns data."""
    loop, queue, _ = loop_fix
    loop._first_iteration = False
    await loop._set_status("running")
    niche_data = {"niche": "bundle_niche", "product_type": "bundle_type"}
    queue.get_pending_approval = AsyncMock(return_value=[])
    queue.get_items_by_status = AsyncMock(return_value=[])
    loop._bundle_checker = AsyncMock(return_value=niche_data)
    loop._niche_picker = AsyncMock(return_value=None)
    queue.create_item = AsyncMock(return_value=99)
    design = AsyncMock()
    loop._design_pipeline = design
    loop._send_approval_notification = AsyncMock()
    loop._wait_for_approval = AsyncMock(return_value="approved")
    loop._handle_decision = AsyncMock()

    with patch("asyncio.sleep", AsyncMock()):
        await loop._tick()

    loop._bundle_checker.assert_called_once()
    loop._niche_picker.assert_not_called()
    design.assert_called_once_with(99, niche_data)


async def test_tick_full_run_creates_item_and_runs_pipeline(loop_fix):
    """_tick runs the complete pipeline: create item → design → approval → decision."""
    loop, queue, _ = loop_fix
    loop._first_iteration = False
    await loop._set_status("running")
    niche_data = {
        "niche": "art_prints",
        "product_type": "digital_print",
        "keywords": ["art", "print"],
        "entry_score": 0.9,
    }
    queue.get_pending_approval = AsyncMock(return_value=[])
    queue.get_items_by_status = AsyncMock(return_value=[])
    loop._bundle_checker = AsyncMock(return_value=None)
    loop._niche_picker = AsyncMock(return_value=niche_data)
    queue.create_item = AsyncMock(return_value=42)
    design = AsyncMock()
    loop._design_pipeline = design
    loop._send_approval_notification = AsyncMock()
    loop._wait_for_approval = AsyncMock(return_value="approved")
    loop._handle_decision = AsyncMock()

    with patch("asyncio.sleep", AsyncMock()):
        await loop._tick()

    queue.create_item.assert_called_once()
    kw = queue.create_item.call_args.kwargs
    assert kw["niche"] == "art_prints"
    assert kw["product_type"] == "digital_print"
    design.assert_called_once_with(42, niche_data)
    loop._send_approval_notification.assert_called_once_with(42)
    loop._wait_for_approval.assert_called_once_with(42)
    loop._handle_decision.assert_called_once_with(42, "approved")


async def test_tick_no_niche_sleeps_empty(loop_fix):
    """_tick sleeps LOOP_SLEEP_EMPTY when no niche data is available."""
    loop, queue, _ = loop_fix
    loop._first_iteration = False
    await loop._set_status("running")
    queue.get_pending_approval = AsyncMock(return_value=[])
    queue.get_items_by_status = AsyncMock(return_value=[])
    loop._bundle_checker = AsyncMock(return_value=None)
    loop._niche_picker = AsyncMock(return_value=None)

    with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        await loop._tick()

    mock_sleep.assert_called_with(LOOP_SLEEP_EMPTY)


# ===========================================================================
# _loop_mixin — _tick: first iteration / startup recovery
# ===========================================================================

async def test_tick_first_iteration_calls_startup_recovery(loop_fix):
    """First _tick call invokes discard_stale_approvals and _on_startup_recovery."""
    loop, queue, _ = loop_fix
    loop._first_iteration = True
    queue.discard_stale_approvals = AsyncMock(return_value=2)
    loop._on_startup_recovery = AsyncMock()
    await loop._set_status("paused_manual")  # early exit after recovery

    with patch("asyncio.sleep", AsyncMock()):
        await loop._tick()

    queue.discard_stale_approvals.assert_called_once()
    loop._on_startup_recovery.assert_called_once()
    assert loop._first_iteration is False


# ===========================================================================
# _loop_mixin — _on_startup_recovery
# ===========================================================================

async def test_on_startup_recovery_empty_queue_is_noop(loop_fix, bot_send):
    """_on_startup_recovery does nothing when there are no pending items."""
    loop, queue, _ = loop_fix
    queue.get_pending_approval = AsyncMock(return_value=[])

    await loop._on_startup_recovery()

    bot_send.assert_not_called()
    assert not loop._bg_tasks


async def test_on_startup_recovery_sends_summary_and_spawns_tasks(loop_fix, bot_send):
    """_on_startup_recovery sends a queue summary and spawns one task per item."""
    loop, queue, _ = loop_fix
    item = _make_mock_item(item_id=20)
    queue.get_pending_approval = AsyncMock(return_value=[item])
    loop._send_approval_notification = AsyncMock()
    loop._wait_for_approval = AsyncMock(return_value="approved")
    loop._handle_decision = AsyncMock()

    await loop._on_startup_recovery()

    bot_send.assert_called_once()
    assert "20" in bot_send.call_args[0][0]
    loop._send_approval_notification.assert_called_once_with(20)
    assert len(loop._bg_tasks) >= 1
    await _cancel_all_tasks(loop)


async def test_on_startup_recovery_pre_approved_sets_event(loop_fix, bot_send):
    """_on_startup_recovery sets the event immediately for pre-approved items."""
    loop, queue, _ = loop_fix
    item = _make_mock_item(item_id=21)
    queue.get_pending_approval = AsyncMock(return_value=[item])
    loop._approval_results[21] = "approved"
    loop._wait_for_approval = AsyncMock(return_value="approved")
    loop._handle_decision = AsyncMock()

    await loop._on_startup_recovery()

    assert 21 in loop._approval_events
    assert loop._approval_events[21].is_set()
    await _cancel_all_tasks(loop)


async def test_on_startup_recovery_idempotent_for_tracked_items(loop_fix, bot_send):
    """_on_startup_recovery skips items already tracked in _approval_events."""
    loop, queue, _ = loop_fix
    item = _make_mock_item(item_id=22)
    existing_evt = asyncio.Event()
    loop._approval_events[22] = existing_evt
    queue.get_pending_approval = AsyncMock(return_value=[item])
    loop._send_approval_notification = AsyncMock()

    await loop._on_startup_recovery()

    assert loop._approval_events[22] is existing_evt
    loop._send_approval_notification.assert_not_called()


async def test_on_startup_recovery_bot_send_failure_continues(loop_fix, bot_send):
    """_on_startup_recovery continues processing when the summary message fails."""
    loop, queue, _ = loop_fix
    item = _make_mock_item(item_id=23)
    queue.get_pending_approval = AsyncMock(return_value=[item])
    bot_send.side_effect = Exception("telegram down")
    loop._send_approval_notification = AsyncMock()
    loop._wait_for_approval = AsyncMock(return_value="approved")
    loop._handle_decision = AsyncMock()

    await loop._on_startup_recovery()  # must not raise

    loop._send_approval_notification.assert_called_once_with(23)
    await _cancel_all_tasks(loop)


async def test_on_startup_recovery_notification_failure_still_spawns_task(
    loop_fix, bot_send
):
    """_on_startup_recovery spawns recovery task even when notification fails."""
    loop, queue, _ = loop_fix
    item = _make_mock_item(item_id=24)
    queue.get_pending_approval = AsyncMock(return_value=[item])
    loop._send_approval_notification = AsyncMock(
        side_effect=Exception("notif fail")
    )
    loop._wait_for_approval = AsyncMock(return_value="approved")
    loop._handle_decision = AsyncMock()

    await loop._on_startup_recovery()

    assert len(loop._bg_tasks) >= 1
    await _cancel_all_tasks(loop)


# ===========================================================================
# Concurrency — lock discipline (CNC-001, RF1)
# ===========================================================================

async def test_concurrent_register_approval_uses_real_lock(loop_fix):
    """Two simultaneous register_approval calls both commit under _approval_lock."""
    loop, _, _ = loop_fix
    evt1, evt2 = asyncio.Event(), asyncio.Event()
    loop._approval_events[1] = evt1
    loop._approval_events[2] = evt2

    await asyncio.gather(
        loop.register_approval(1, "approved"),
        loop.register_approval(2, "skipped_user"),
    )

    assert loop._approval_results[1] == "approved"
    assert loop._approval_results[2] == "skipped_user"
    assert evt1.is_set()
    assert evt2.is_set()


async def test_stop_and_resume_concurrent_no_deadlock(loop_fix):
    """Concurrent stop() and resume() serialise via _cmd_lock without deadlock."""
    loop, _, _ = loop_fix
    loop._running = False
    loop.run_loop = AsyncMock()

    await asyncio.wait_for(
        asyncio.gather(loop.stop(), loop.resume(), return_exceptions=True),
        timeout=5.0,
    )
    # Cleanup any created task
    await _cancel_all_tasks(loop)


async def test_cmd_queue_clear_uses_approval_lock(loop_fix):
    """cmd_queue('clear') clears _approval_events under _approval_lock (RF1 NEW-001)."""
    loop, _, _ = loop_fix
    loop._approval_events[99] = asyncio.Event()
    loop._approval_results[99] = "approved"

    result = await loop.cmd_queue("clear")

    assert "svuotata" in result
    assert 99 not in loop._approval_events
    assert 99 not in loop._approval_results


async def test_cmd_queue_clear_cancels_bg_tasks(loop_fix):
    """cmd_queue('clear') cancels in-flight bg_tasks before clearing state (line 45)."""
    loop, _, _ = loop_fix
    bg_task = asyncio.create_task(asyncio.sleep(999))
    loop._bg_tasks.add(bg_task)

    result = await loop.cmd_queue("clear")

    assert "svuotata" in result
    assert bg_task.cancelled()
    assert not loop._bg_tasks


async def test_stop_cancels_bg_tasks(loop_fix):
    """stop() cancels all _bg_tasks in addition to the main loop_task (line 102)."""
    loop, _, _ = loop_fix
    bg_task = asyncio.create_task(asyncio.sleep(999))
    loop._bg_tasks.add(bg_task)
    loop._loop_task = asyncio.create_task(asyncio.sleep(999))
    loop._running = True

    await loop.stop()

    assert bg_task.cancelled()
    assert not loop._bg_tasks


async def test_resume_cancels_existing_loop_task(loop_fix):
    """resume() cancels an existing non-done loop_task (lines 111-113)."""
    loop, _, _ = loop_fix
    loop.run_loop = AsyncMock()
    existing_task = asyncio.create_task(asyncio.sleep(999))
    loop._loop_task = existing_task

    await loop.resume()

    assert existing_task.cancelled()
    assert loop._loop_task is not existing_task
    await _cancel_all_tasks(loop)


async def test_handle_decision_skipped_timeout_second_warning(loop_fix, bot_send):
    """_handle_decision('skipped_timeout') with consec==2 sends a 2nd-timeout warning."""
    loop, _, _ = loop_fix
    await loop._increment_timeout_skip()  # consec_to will be 2

    await loop._handle_decision(1, "skipped_timeout")

    bot_send.assert_called_once()
    assert "2°" in bot_send.call_args[0][0]


async def test_handle_decision_skipped_timeout_triggers_timeout_pause(loop_fix):
    """_handle_decision('skipped_timeout') with consec >= 3 triggers timeout pause."""
    loop, _, _ = loop_fix
    await loop._increment_timeout_skip()
    await loop._increment_timeout_skip()  # consec_to will be 3

    await loop._handle_decision(1, "skipped_timeout")

    assert await loop._get_status() == "paused_manual"


async def test_wait_for_approval_event_fires_returns_result(loop_fix):
    """Line 99: event pre-set with result already stored → returns immediately."""
    loop, queue, _ = loop_fix
    evt = asyncio.Event()
    evt.set()
    loop._approval_events[1] = evt
    loop._approval_results[1] = "approved"
    queue.get_item = AsyncMock(return_value=_make_mock_item(status="pending_approval"))

    with patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_TIMEOUT", 999), \
         patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_POLL", 0.001):
        result = await asyncio.wait_for(loop._wait_for_approval(1), timeout=5.0)

    assert result == "approved"


async def test_wait_for_approval_global_timeout(loop_fix):
    """Lines 123-124: deadline expired → set_skipped(timeout), return 'skipped_timeout'."""
    loop, queue, _ = loop_fix

    with patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_TIMEOUT", -1):
        result = await asyncio.wait_for(loop._wait_for_approval(1), timeout=5.0)

    assert result == "skipped_timeout"
    queue.set_skipped.assert_called_once_with(1, "timeout")


async def test_tick_queue_depth_recover_cleans_up_after_completion(loop_fix):
    """_recover_queued finally block (lines 212-220) removes item from events/results."""
    loop, queue, _ = loop_fix
    loop._first_iteration = False
    await loop._set_status("running")
    item = _make_mock_item(item_id=30)
    queue.get_pending_approval = AsyncMock(return_value=[item])
    queue.get_items_by_status = AsyncMock(return_value=[])
    loop._send_approval_notification = AsyncMock()
    loop._wait_for_approval = AsyncMock(return_value="approved")
    loop._handle_decision = AsyncMock()

    with patch("asyncio.sleep", AsyncMock()):
        await loop._tick()

    # Allow bg tasks to run to completion
    if loop._bg_tasks:
        await asyncio.gather(*list(loop._bg_tasks), return_exceptions=True)
    await asyncio.sleep(0)

    assert 30 not in loop._approval_events
    assert 30 not in loop._approval_results


async def test_on_startup_recovery_recover_cleans_up_after_completion(loop_fix, bot_send):
    """_recover_item finally block (lines 329-337) removes item from events/results."""
    loop, queue, _ = loop_fix
    item = _make_mock_item(item_id=40)
    queue.get_pending_approval = AsyncMock(return_value=[item])
    loop._send_approval_notification = AsyncMock()
    loop._wait_for_approval = AsyncMock(return_value="approved")
    loop._handle_decision = AsyncMock()

    await loop._on_startup_recovery()

    if loop._bg_tasks:
        await asyncio.gather(*list(loop._bg_tasks), return_exceptions=True)
    await asyncio.sleep(0)

    assert 40 not in loop._approval_events
    assert 40 not in loop._approval_results


async def test_tick_queue_depth_recover_exception_still_cleans_up(loop_fix):
    """_recover_queued except block (lines 215-216): exception is caught and item cleaned up."""
    loop, queue, _ = loop_fix
    loop._first_iteration = False
    await loop._set_status("running")
    item = _make_mock_item(item_id=50)
    queue.get_pending_approval = AsyncMock(return_value=[item])
    queue.get_items_by_status = AsyncMock(return_value=[])
    loop._send_approval_notification = AsyncMock()
    loop._wait_for_approval = AsyncMock(side_effect=RuntimeError("recovery error"))

    with patch("asyncio.sleep", AsyncMock()):
        await loop._tick()

    if loop._bg_tasks:
        await asyncio.gather(*list(loop._bg_tasks), return_exceptions=True)
    await asyncio.sleep(0)

    assert 50 not in loop._approval_events
    assert 50 not in loop._approval_results


async def test_on_startup_recovery_recover_exception_still_cleans_up(loop_fix, bot_send):
    """_recover_item except block (lines 332-333): exception is caught and item cleaned up."""
    loop, queue, _ = loop_fix
    item = _make_mock_item(item_id=60)
    queue.get_pending_approval = AsyncMock(return_value=[item])
    loop._send_approval_notification = AsyncMock()
    loop._wait_for_approval = AsyncMock(side_effect=RuntimeError("recovery error"))

    await loop._on_startup_recovery()

    if loop._bg_tasks:
        await asyncio.gather(*list(loop._bg_tasks), return_exceptions=True)
    await asyncio.sleep(0)

    assert 60 not in loop._approval_events
    assert 60 not in loop._approval_results


