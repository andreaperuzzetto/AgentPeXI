"""tests/e2e/test_full_pipeline.py — Full status-transition pipeline integration test.

Exercises: create_item (pending_design) → set_design_ready → set_approved
           → assign_slot (scheduled) → set_published (published).

All external APIs are mocked. Uses :memory: SQLite for aiosqlite.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from apps.backend.core.production_queue import ProductionQueueService
from apps.backend.core.autopilot_loop import AutopilotLoop
from apps.backend.core.budget_manager import BudgetManager, BudgetStatus

# ---------------------------------------------------------------------------
# Full schema — reused from test_production_queue.py pattern
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

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS niche_intelligence (
    niche TEXT PRIMARY KEY,
    product_type TEXT,
    performance_score REAL DEFAULT 0.0
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
async def queue(db):
    return ProductionQueueService(db)


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
        return_value=datetime.now() + timedelta(hours=1)
    )
    return policy


@pytest.fixture
def bot_send():
    return AsyncMock()


# ---------------------------------------------------------------------------
# P6a — Full status-transition pipeline
# ---------------------------------------------------------------------------

async def test_full_pipeline_status_transitions(queue, db):
    """Item flows through all statuses: pending_design → pending_approval
    → approved → scheduled → published.
    No unhandled exceptions occur.
    """
    # 1. Create (pending_design)
    item_id = await queue.create_item(
        niche="test_niche",
        product_type="printable_pdf",
        keywords=["planner", "printable"],
        entry_score=0.85,
    )
    item = await queue.get_item(item_id)
    assert item.status == "pending_design"

    # 2. Design complete → pending_approval
    await queue.set_design_ready(
        item_id=item_id,
        design_prompt="minimalist daily planner",
        image_url="https://cdn.example.com/img.png",
        thumbnail_path="/tmp/thumb.jpg",
        title="Daily Planner Printable",
        description="A beautiful daily planner",
        tags=["planner", "printable", "organization"],
        price=4.99,
        llm_cost=0.003,
        image_cost=0.002,
    )
    item = await queue.get_item(item_id)
    assert item.status == "pending_approval"

    # 3. Approval received → approved
    await queue.set_approved(item_id)
    item = await queue.get_item(item_id)
    assert item.status == "approved"

    # 4. Slot assigned → scheduled
    slot_ts = (datetime.now() + timedelta(hours=2)).timestamp()
    await queue.assign_slot(item_id, slot_ts)
    item = await queue.get_item(item_id)
    assert item.status == "scheduled"
    assert item.scheduled_publish_at == pytest.approx(slot_ts, abs=1)

    # 5. Published → published
    await queue.set_published(item_id, etsy_listing_id="etsy_12345")
    item = await queue.get_item(item_id)
    assert item.status == "published"
    assert item.etsy_listing_id == "etsy_12345"


async def test_pipeline_item_exists_after_completion(queue, db):
    """After full pipeline, the queue item persists with correct final status."""
    item_id = await queue.create_item(niche="zen_planner", product_type="printable_pdf", keywords=["zen"])
    await queue.set_design_ready(
        item_id=item_id,
        design_prompt="zen",
        image_url="url",
        thumbnail_path="path",
        title="Zen Planner",
        description="desc",
        tags=["zen"],
        price=3.99,
    )
    await queue.set_approved(item_id)
    await queue.assign_slot(item_id, time.time() + 100)
    await queue.set_published(item_id, "listing_xyz")

    published = await queue.get_items_by_status("published")
    assert any(i.id == item_id for i in published)


# ---------------------------------------------------------------------------
# P6b — AutopilotLoop integration: approval signal flows correctly
# ---------------------------------------------------------------------------

async def test_autopilot_approval_signal_reaches_pipeline(queue, db, mock_budget, mock_policy, bot_send):
    """register_approval() from Telegram correctly signals the wait loop."""
    import asyncio

    loop = AutopilotLoop(
        db=db,
        queue=queue,
        budget=mock_budget,
        policy=mock_policy,
        bot_send=bot_send,
    )

    # Create item and set it to pending_approval state
    item_id = await queue.create_item(niche="test", product_type="printable_pdf", keywords=[])

    # Simulate the loop having registered an event for this item
    event = asyncio.Event()
    loop._approval_events[item_id] = event

    # Telegram callback arrives — registers approval
    await loop.register_approval(item_id, "approved")

    # Event is now set and result is stored
    assert event.is_set()
    assert loop._approval_results[item_id] == "approved"


# ---------------------------------------------------------------------------
# P6c — Skip flow: item ends in skipped status
# ---------------------------------------------------------------------------

async def test_pipeline_skip_flow(queue, db):
    """User skip transitions item to skipped status."""
    item_id = await queue.create_item(niche="skip_test", product_type="printable_pdf", keywords=[])
    await queue.set_design_ready(
        item_id=item_id,
        design_prompt="p",
        image_url="u",
        thumbnail_path="t",
        title="title",
        description="desc",
        tags=[],
        price=5.0,
    )
    await queue.set_skipped(item_id, "user")
    item = await queue.get_item(item_id)
    assert item.status == "skipped"
    assert item.skip_reason == "user"
    assert item.skip_count_user == 1


# ---------------------------------------------------------------------------
# P6d — Status validation: invalid transitions raise ValueError
# ---------------------------------------------------------------------------

async def test_assign_slot_requires_approved_status(queue, db):
    """assign_slot() raises ValueError if item is not in 'approved' status."""
    item_id = await queue.create_item(niche="v_test", product_type="printable_pdf", keywords=[])

    with pytest.raises(ValueError, match="expected 'approved'"):
        await queue.assign_slot(item_id, time.time() + 100)


async def test_set_published_requires_scheduled_status(queue, db):
    """set_published() raises ValueError if item is not in 'scheduled' status."""
    item_id = await queue.create_item(niche="v_test2", product_type="printable_pdf", keywords=[])

    with pytest.raises(ValueError, match="expected 'scheduled'"):
        await queue.set_published(item_id, "etsy_id")
