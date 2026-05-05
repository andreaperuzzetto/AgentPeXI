"""Tests for ProductionQueueService and helpers (pure + in-memory SQLite)."""
from __future__ import annotations

import time

import aiosqlite
import pytest

from apps.backend.core.production_queue import (
    ProductionQueueService,
    ProductionQueueItem,
    _loads_list,
    _dumps_list,
    _to_float,
)

# ---------------------------------------------------------------------------
# DB fixture — full schema
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
    created_at REAL DEFAULT (unixepoch()),
    updated_at REAL DEFAULT (unixepoch())
);
"""


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


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

def test_loads_list_none_returns_empty():
    assert _loads_list(None) == []


def test_loads_list_already_list():
    assert _loads_list(["a", "b"]) == ["a", "b"]


def test_loads_list_valid_json():
    assert _loads_list('["tag1","tag2"]') == ["tag1", "tag2"]


def test_loads_list_non_list_json_returns_empty():
    assert _loads_list('{"key":"val"}') == []


def test_loads_list_invalid_json_returns_empty():
    assert _loads_list("not json") == []


def test_dumps_list_none_returns_none():
    assert _dumps_list(None) is None


def test_dumps_list_empty():
    result = _dumps_list([])
    assert result == "[]"


def test_dumps_list_values():
    result = _dumps_list(["planner", "journal"])
    assert "planner" in result
    assert "journal" in result


def test_to_float_none_returns_recent_timestamp():
    result = _to_float(None)
    assert abs(result - time.time()) < 2


def test_to_float_int():
    assert _to_float(1000) == 1000.0


def test_to_float_float():
    assert _to_float(1234.56) == 1234.56


def test_to_float_iso_string():
    result = _to_float("2024-01-15 10:30:00")
    assert isinstance(result, float)
    assert result > 0


def test_to_float_invalid_string_returns_recent():
    result = _to_float("not-a-date")
    assert abs(result - time.time()) < 2


# ---------------------------------------------------------------------------
# create_item
# ---------------------------------------------------------------------------

async def test_create_item_returns_int(queue):
    item_id = await queue.create_item("planner", "printable_pdf", ["journal", "planner"])
    assert isinstance(item_id, int)
    assert item_id > 0


async def test_create_item_status_pending_design(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    row = await db.execute("SELECT status FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["status"] == "pending_design"


async def test_create_item_with_loop_run_id(queue, db):
    item_id = await queue.create_item("niche", "pdf", [], loop_run_id="run-123")
    row = await db.execute("SELECT loop_run_id FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["loop_run_id"] == "run-123"


# ---------------------------------------------------------------------------
# set_design_ready
# ---------------------------------------------------------------------------

async def test_set_design_ready_changes_status(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_design_ready(
        item_id, "prompt", "http://img", "/thumb.png",
        "Title", "Desc", ["tag1"], 9.99
    )
    row = await db.execute("SELECT status, listing_title FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["status"] == "pending_approval"
    assert r["listing_title"] == "Title"


# ---------------------------------------------------------------------------
# set_approved → assign_slot → set_published
# ---------------------------------------------------------------------------

async def test_set_approved_changes_status(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_approved(item_id, message_id=42, chat_id=99)
    row = await db.execute("SELECT status, approval_message_id FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["status"] == "approved"
    assert r["approval_message_id"] == 42


async def test_assign_slot_changes_status(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.assign_slot(item_id, time.time() + 3600)
    row = await db.execute("SELECT status FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["status"] == "scheduled"


async def test_set_published_changes_status(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    # Must go through the full state machine: approved → scheduled → published
    await queue.set_approved(item_id, message_id=42, chat_id=99)
    await queue.assign_slot(item_id, time.time() + 3600)
    await queue.set_published(item_id, "listing_123")
    row = await db.execute("SELECT status, etsy_listing_id FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["status"] == "published"
    assert r["etsy_listing_id"] == "listing_123"


async def test_set_published_before_approved_raises(queue, db):
    """Contract: set_published on a pending_design item must raise ValueError."""
    item_id = await queue.create_item("planner", "printable_pdf", [])
    # Item is in 'pending_design' — publishing without going through approved→scheduled is illegal
    with pytest.raises(ValueError, match="status is 'pending_design'"):
        await queue.set_published(item_id, "listing_illegal")


# ---------------------------------------------------------------------------
# set_skipped / set_failed
# ---------------------------------------------------------------------------

async def test_set_skipped_user(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_skipped(item_id, "user")
    row = await db.execute("SELECT status, skip_reason, skip_count_user FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["status"] == "skipped"
    assert r["skip_reason"] == "user"
    assert r["skip_count_user"] == 1


async def test_set_skipped_timeout(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_skipped(item_id, "timeout")
    row = await db.execute("SELECT skip_count_timeout FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["skip_count_timeout"] == 1


async def test_set_skipped_other_reason_uses_user_counter(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_skipped(item_id, "budget")
    row = await db.execute("SELECT skip_count_user FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["skip_count_user"] == 1


async def test_set_failed(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_failed(item_id, "api error")
    row = await db.execute("SELECT status FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["status"] == "failed"


# ---------------------------------------------------------------------------
# ads activation / pause
# ---------------------------------------------------------------------------

async def test_set_ads_activated(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_ads_activated(item_id)
    row = await db.execute("SELECT ads_activated FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["ads_activated"] == 1


async def test_set_ads_paused(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_ads_paused(item_id)
    row = await db.execute("SELECT ads_paused FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["ads_paused"] == 1


# ---------------------------------------------------------------------------
# Read methods
# ---------------------------------------------------------------------------

async def test_get_item_returns_none_for_missing(queue):
    assert await queue.get_item(99999) is None


async def test_get_item_returns_production_queue_item(queue):
    item_id = await queue.create_item("planner", "printable_pdf", ["tag1"])
    item = await queue.get_item(item_id)
    assert isinstance(item, ProductionQueueItem)
    assert item.niche == "planner"
    assert item.keywords == ["tag1"]


async def test_get_pending_approval_empty(queue):
    result = await queue.get_pending_approval()
    assert result == []


async def test_get_pending_approval_finds_item(queue):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_design_ready(item_id, "p", "u", "t", "T", "D", [], 9.99)
    result = await queue.get_pending_approval()
    assert len(result) == 1
    assert result[0].id == item_id


async def test_get_approved_items_empty(queue):
    assert await queue.get_approved_items() == []


async def test_get_approved_items_finds_approved(queue):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_approved(item_id)
    result = await queue.get_approved_items()
    assert len(result) == 1


async def test_get_due_scheduled_empty(queue):
    result = await queue.get_due_scheduled(now=time.time())
    assert result == []


async def test_get_due_scheduled_finds_past_slot(queue):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    past = time.time() - 3600
    await queue.assign_slot(item_id, past)
    result = await queue.get_due_scheduled(now=time.time())
    assert any(r.id == item_id for r in result)


async def test_get_items_by_status_empty(queue):
    result = await queue.get_items_by_status("failed")
    assert result == []


async def test_get_items_by_status_finds_items(queue):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_failed(item_id, "err")
    result = await queue.get_items_by_status("failed")
    assert len(result) == 1


async def test_get_recent_returns_items(queue):
    await queue.create_item("niche1", "printable_pdf", [])
    await queue.create_item("niche2", "printable_pdf", [])
    result = await queue.get_recent(limit=10)
    assert len(result) >= 2


async def test_get_recent_with_status_filter(queue):
    item_id = await queue.create_item("niche1", "printable_pdf", [])
    await queue.set_failed(item_id, "err")
    result = await queue.get_recent(status="failed")
    assert all(r.status == "failed" for r in result)


async def test_get_recent_with_days_filter(queue):
    result = await queue.get_recent(days=1)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# get_last_skipped
# ---------------------------------------------------------------------------

async def test_get_last_skipped_empty(queue):
    result = await queue.get_last_skipped()
    assert result == []


async def test_get_last_skipped_with_reason(queue):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_skipped(item_id, "user")
    result = await queue.get_last_skipped(reason="user")
    assert len(result) == 1


async def test_get_last_skipped_no_reason_filter(queue):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_skipped(item_id, "timeout")
    result = await queue.get_last_skipped()
    assert len(result) >= 1


# ---------------------------------------------------------------------------
# consecutive_user_skips / consecutive_timeouts
# ---------------------------------------------------------------------------

async def test_consecutive_user_skips_zero(queue):
    assert await queue.consecutive_user_skips() == 0


async def test_consecutive_user_skips_counts(queue):
    for _ in range(3):
        item_id = await queue.create_item("planner", "printable_pdf", [])
        await queue.set_skipped(item_id, "user")
    assert await queue.consecutive_user_skips() == 3


async def test_consecutive_user_skips_resets_on_approval(queue):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_skipped(item_id, "user")
    item_id2 = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_approved(item_id2)
    item_id3 = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_skipped(item_id3, "user")
    assert await queue.consecutive_user_skips() == 1


async def test_consecutive_timeouts_zero(queue):
    assert await queue.consecutive_timeouts() == 0


async def test_consecutive_timeouts_counts(queue):
    for _ in range(2):
        item_id = await queue.create_item("planner", "printable_pdf", [])
        await queue.set_skipped(item_id, "timeout")
    assert await queue.consecutive_timeouts() == 2


# ---------------------------------------------------------------------------
# discard_stale_approvals
# ---------------------------------------------------------------------------

async def test_discard_stale_approvals_returns_count(queue):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    old_ts = time.time() - 2 * 86400
    await queue.set_design_ready(item_id, "p", "u", "t", "T", "D", [], 9.99)
    await queue._db.execute(
        "UPDATE production_queue SET approval_sent_at=? WHERE id=?", (old_ts, item_id)
    )
    await queue._db.commit()
    count = await queue.discard_stale_approvals(max_age_seconds=86400)
    assert count == 1


async def test_discard_stale_approvals_keeps_recent(queue):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_design_ready(item_id, "p", "u", "t", "T", "D", [], 9.99)
    count = await queue.discard_stale_approvals(max_age_seconds=86400)
    assert count == 0


# ---------------------------------------------------------------------------
# count_published_today
# ---------------------------------------------------------------------------

async def test_count_published_today_zero(queue):
    assert await queue.count_published_today() == 0


async def test_count_published_today_counts(queue):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_approved(item_id, message_id=1, chat_id=1)
    await queue.assign_slot(item_id, time.time() + 3600)
    await queue.set_published(item_id, "listing_abc")
    count = await queue.count_published_today()
    assert count == 1


# ---------------------------------------------------------------------------
# PA-1: new task-id-based helper methods
# ---------------------------------------------------------------------------

async def test_get_item_by_task_id_returns_item(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", ["journal"])
    row = await db.execute("SELECT task_id FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    task_id = r["task_id"]

    item = await queue.get_item_by_task_id(task_id)
    assert item is not None
    assert item.id == item_id


async def test_get_item_by_task_id_unknown_returns_none(queue):
    result = await queue.get_item_by_task_id("nonexistent-task-id")
    assert result is None


async def test_set_design_started_updates_timestamp(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    row = await db.execute("SELECT task_id, updated_at FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    task_id = r["task_id"]
    original_updated_at = r["updated_at"]

    import asyncio
    await asyncio.sleep(0.02)  # ensure time advances
    await queue.set_design_started(task_id)

    row2 = await db.execute("SELECT status, updated_at FROM production_queue WHERE id=?", (item_id,))
    r2 = await row2.fetchone()
    assert r2["status"] == "pending_design"  # status must NOT change
    assert r2["updated_at"] >= original_updated_at


async def test_set_design_started_unknown_task_id_is_noop(queue):
    # must not raise
    await queue.set_design_started("nonexistent-uuid")


async def test_set_files_generated_stores_paths(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    row = await db.execute("SELECT task_id FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    task_id = r["task_id"]

    await queue.set_files_generated(task_id, ["/tmp/file1.pdf", "/tmp/file2.pdf"])

    row2 = await db.execute("SELECT status, file_paths FROM production_queue WHERE id=?", (item_id,))
    r2 = await row2.fetchone()
    assert r2["status"] == "pending_design"  # status must NOT change
    import json
    paths = json.loads(r2["file_paths"])
    assert paths == ["/tmp/file1.pdf", "/tmp/file2.pdf"]


async def test_set_failed_by_task_id_changes_status(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    row = await db.execute("SELECT task_id FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    task_id = r["task_id"]

    await queue.set_failed_by_task_id(task_id, "generation error XYZ")

    row2 = await db.execute("SELECT status, skip_reason FROM production_queue WHERE id=?", (item_id,))
    r2 = await row2.fetchone()
    assert r2["status"] == "failed"
    assert "generation error XYZ" in r2["skip_reason"]


async def test_set_failed_by_task_id_unknown_is_noop(queue):
    # must not raise
    await queue.set_failed_by_task_id("nonexistent-uuid", "error")
