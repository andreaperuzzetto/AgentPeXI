"""Tests for AutopilotLoop — approval flow, lifecycle, consecutive-timeout pause."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from apps.backend.core.autopilot_loop import AutopilotLoop
from apps.backend.core.budget_manager import BudgetStatus
from apps.backend.core.production_queue import ProductionQueueService

# ---------------------------------------------------------------------------
# Schema
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
def mock_budget():
    budget = AsyncMock()
    budget.check_budget = AsyncMock(return_value=BudgetStatus.OK)
    return budget


@pytest.fixture
def mock_policy():
    policy = AsyncMock()
    policy.is_in_availability_window = AsyncMock(return_value=True)
    policy.can_publish_today = AsyncMock(return_value=True)
    policy.next_available_slot = AsyncMock(
        return_value=datetime.now() + timedelta(hours=2)
    )
    policy._get_int = AsyncMock(return_value=5)
    return policy


@pytest.fixture
def bot_send():
    return AsyncMock()


@pytest.fixture
async def loop_fixture(db, mock_budget, mock_policy, bot_send):
    queue = ProductionQueueService(db)
    loop = AutopilotLoop(
        db=db,
        queue=queue,
        budget=mock_budget,
        policy=mock_policy,
        bot_send=bot_send,
    )
    yield loop, queue, db


async def _insert_pending_approval(db) -> int:
    """Helper: insert a production_queue item in pending_approval status."""
    cursor = await db.execute(
        """
        INSERT INTO production_queue
            (niche, product_type, status, entry_score)
        VALUES (?, ?, 'pending_approval', 1.0)
        """,
        ("test_niche", "printable_pdf"),
    )
    await db.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# _wait_for_approval — happy path
# ---------------------------------------------------------------------------

async def test_wait_for_approval_happy_path(loop_fixture):
    """Pre-registered event + result → returns 'approved' without hitting DB poll."""
    loop, queue, db = loop_fixture
    item_id = await _insert_pending_approval(db)

    # Pre-wire event and result (simulates Telegram callback arriving first)
    event = asyncio.Event()
    event.set()
    loop._approval_events[item_id] = event
    loop._approval_results[item_id] = "approved"

    result = await loop._wait_for_approval(item_id)

    assert result == "approved"


# ---------------------------------------------------------------------------
# _wait_for_approval — timeout path
# ---------------------------------------------------------------------------

async def test_wait_for_approval_timeout(loop_fixture):
    """With APPROVAL_TIMEOUT patched to -1, deadline is immediately exceeded."""
    loop, queue, db = loop_fixture
    item_id = await _insert_pending_approval(db)

    with patch("apps.backend.core._autopilot._approval_mixin.APPROVAL_TIMEOUT", -1):
        result = await loop._wait_for_approval(item_id)

    assert result == "skipped_timeout"

    item = await queue.get_item(item_id)
    assert item is not None
    assert item.status == "skipped"
    assert item.skip_reason == "timeout"


# ---------------------------------------------------------------------------
# consecutive_timeouts → paused_manual
# ---------------------------------------------------------------------------

async def test_consecutive_timeouts_enters_paused(loop_fixture):
    """After 3 skipped_timeout decisions the loop status becomes paused_manual."""
    loop, queue, db = loop_fixture
    fake_id = 9999  # no DB item needed for this code path

    await loop._handle_decision(fake_id, "skipped_timeout")  # count = 1
    await loop._handle_decision(fake_id, "skipped_timeout")  # count = 2 → sends warning
    await loop._handle_decision(fake_id, "skipped_timeout")  # count = 3 → pause

    status = await loop._get_status()
    assert status == "paused_manual"


async def test_second_timeout_sends_warning(loop_fixture, bot_send):
    """Second consecutive timeout sends a warning message."""
    loop, queue, db = loop_fixture

    await loop._handle_decision(9998, "skipped_timeout")
    await loop._handle_decision(9998, "skipped_timeout")

    assert bot_send.call_count == 1
    assert "2°" in bot_send.call_args[0][0]


# ---------------------------------------------------------------------------
# register_approval — graceful handling when event not registered
# ---------------------------------------------------------------------------

async def test_register_approval_no_event_no_error(loop_fixture):
    """register_approval with no matching event stores result without KeyError."""
    loop, queue, db = loop_fixture
    item_id = 42  # not in _approval_events

    await loop.register_approval(item_id, "approved")

    assert loop._approval_results.get(item_id) == "approved"


async def test_register_approval_sets_event_if_present(loop_fixture):
    """register_approval sets the event when one is registered."""
    loop, queue, db = loop_fixture
    item_id = 55
    event = asyncio.Event()
    loop._approval_events[item_id] = event

    await loop.register_approval(item_id, "skipped_user")

    assert event.is_set()
    assert loop._approval_results[item_id] == "skipped_user"


# ---------------------------------------------------------------------------
# stop() — cancels _loop_task and sets paused_manual
# ---------------------------------------------------------------------------

async def test_stop_cancels_loop_task(loop_fixture):
    """stop() cancels the running loop task and updates status to paused_manual."""
    loop, queue, db = loop_fixture

    async def _long_sleep():
        await asyncio.sleep(999)

    loop._running = True
    loop._loop_task = asyncio.create_task(_long_sleep(), name="autopilot_loop")

    await loop.stop()

    assert loop._running is False
    assert loop._loop_task.done()
    assert await loop._get_status() == "paused_manual"


async def test_stop_cancels_bg_tasks(loop_fixture):
    """stop() cancels all background tasks in _bg_tasks."""
    loop, queue, db = loop_fixture

    async def _long_sleep():
        await asyncio.sleep(999)

    bg = asyncio.create_task(_long_sleep())
    loop._bg_tasks.add(bg)
    loop._loop_task = asyncio.create_task(_long_sleep())
    loop._running = True

    await loop.stop()

    assert bg.done()


# ---------------------------------------------------------------------------
# resume() — previous task cancelled, new task created
# ---------------------------------------------------------------------------

async def test_resume_creates_loop_task(loop_fixture):
    """resume() starts a new loop task and sets running=True."""
    loop, queue, db = loop_fixture

    async def _long_sleep():
        await asyncio.sleep(999)

    loop.run_loop = _long_sleep
    loop._running = False

    await loop.resume()

    assert loop._running is True
    assert loop._loop_task is not None
    assert not loop._loop_task.done()

    # Cleanup
    loop._loop_task.cancel()
    try:
        await loop._loop_task
    except asyncio.CancelledError:
        pass


async def test_resume_twice_cancels_previous_task(loop_fixture):
    """Calling resume() twice cancels the first task and creates a new one."""
    loop, queue, db = loop_fixture

    async def _long_sleep():
        await asyncio.sleep(999)

    loop.run_loop = _long_sleep
    loop._running = False

    await loop.resume()
    task1 = loop._loop_task

    await loop.resume()
    task2 = loop._loop_task

    assert task1 is not task2
    assert task1.cancelled()
    assert not task2.done()

    # Cleanup
    task2.cancel()
    try:
        await task2
    except asyncio.CancelledError:
        pass
