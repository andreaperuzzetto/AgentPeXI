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
    await queue.set_design_ready(item_id, "p", "u", "/t", "T", "D", [], 9.99)
    await queue.set_approved(item_id, message_id=42, chat_id=99)
    row = await db.execute("SELECT status, approval_message_id FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["status"] == "approved"
    assert r["approval_message_id"] == 42


async def test_assign_slot_changes_status(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_design_ready(item_id, "p", "u", "/t", "T", "D", [], 9.99)
    await queue.set_approved(item_id)
    await queue.assign_slot(item_id, time.time() + 3600)
    row = await db.execute("SELECT status FROM production_queue WHERE id=?", (item_id,))
    r = await row.fetchone()
    assert r["status"] == "scheduled"


async def test_set_design_ready_from_wrong_state_raises(queue, db):
    """Contract: set_design_ready requires status == 'pending_design'."""
    item_id = await queue.create_item("planner", "printable_pdf", [])
    # Advance to pending_approval first
    await queue.set_design_ready(item_id, "p", "u", "/t", "T", "D", [], 9.99)
    # Calling again from pending_approval must raise
    with pytest.raises(ValueError, match="status is 'pending_approval'"):
        await queue.set_design_ready(item_id, "p2", "u2", "/t2", "T2", "D2", [], 9.99)


async def test_set_approved_from_wrong_state_raises(queue, db):
    """Contract: set_approved requires status == 'pending_approval'."""
    item_id = await queue.create_item("planner", "printable_pdf", [])
    # Item is in pending_design, not pending_approval
    with pytest.raises(ValueError, match="status is 'pending_design'"):
        await queue.set_approved(item_id)


async def test_assign_slot_from_wrong_state_raises(queue, db):
    """Contract: assign_slot requires status == 'approved'."""
    item_id = await queue.create_item("planner", "printable_pdf", [])
    # Item is in pending_design, not approved
    with pytest.raises(ValueError, match="status is 'pending_design'"):
        await queue.assign_slot(item_id, time.time() + 3600)


async def test_set_published_changes_status(queue, db):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    # Must go through the full state machine: pending_design → pending_approval → approved → scheduled → published
    await queue.set_design_ready(item_id, "p", "u", "/t", "T", "D", [], 9.99)
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

def test_set_skipped_column_whitelist_exists():
    """M1: set_skipped deve usare un dict whitelist per i nomi di colonna,
    non un f-string, per prevenire SQL injection.

    _SKIP_REASON_COL deve esistere a livello di modulo e mappare solo
    verso colonne note (skip_count_user, skip_count_timeout).
    """
    from apps.backend.core.production_queue import _SKIP_REASON_COL

    valid_cols = {"skip_count_user", "skip_count_timeout"}
    for reason, col in _SKIP_REASON_COL.items():
        assert col in valid_cols, (
            f"reason='{reason}' mappa a colonna sconosciuta '{col}'"
        )


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


async def test_set_failed_stores_error_in_error_message_not_skip_reason(queue, db):
    """L1: set_failed must write the error string to error_message, not skip_reason.

    skip_reason is semantically reserved for skip codes ('user', 'timeout', etc.)
    and must not be polluted with error stack traces.
    """
    item_id = await queue.create_item("planner", "printable_pdf", [])
    await queue.set_failed(item_id, "connection timeout error")

    row = await db.execute(
        "SELECT status, skip_reason, error_message FROM production_queue WHERE id=?",
        (item_id,),
    )
    r = await row.fetchone()
    assert r["status"] == "failed"
    assert "connection timeout error" in r["error_message"], (
        "set_failed must store error in error_message column, not skip_reason"
    )
    assert r["skip_reason"] is None, (
        "set_failed must not write to skip_reason — reserved for skip codes"
    )


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
    await queue.set_design_ready(item_id, "p", "u", "/t", "T", "D", [], 9.99)
    await queue.set_approved(item_id)
    result = await queue.get_approved_items()
    assert len(result) == 1


async def test_get_due_scheduled_empty(queue):
    result = await queue.get_due_scheduled(now=time.time())
    assert result == []


async def test_get_due_scheduled_finds_past_slot(queue):
    item_id = await queue.create_item("planner", "printable_pdf", [])
    past = time.time() - 3600
    await queue.set_design_ready(item_id, "p", "u", "/t", "T", "D", [], 9.99)
    await queue.set_approved(item_id)
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
    await queue.set_design_ready(item_id2, "p", "u", "/t", "T", "D", [], 9.99)
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


def test_consecutive_skip_window_is_named_constant():
    """M2/L2: LIMIT 20 in consecutive_user_skips/consecutive_timeouts deve essere
    una costante nominata a livello di modulo, non un magic number hardcoded.

    Garantisce che il ceiling sia documentato e modificabile in un solo posto.
    """
    from apps.backend.core.production_queue import _CONSECUTIVE_SKIP_WINDOW

    assert isinstance(_CONSECUTIVE_SKIP_WINDOW, int), "deve essere un intero"
    assert _CONSECUTIVE_SKIP_WINDOW > 0, "deve essere positivo"


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
    await queue.set_design_ready(item_id, "p", "u", "/t", "T", "D", [], 9.99)
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

    row2 = await db.execute(
        "SELECT status, skip_reason, error_message FROM production_queue WHERE id=?",
        (item_id,),
    )
    r2 = await row2.fetchone()
    assert r2["status"] == "failed"
    assert "generation error XYZ" in r2["error_message"], (
        "set_failed_by_task_id must store error in error_message, not skip_reason"
    )
    assert r2["skip_reason"] is None, (
        "set_failed_by_task_id must not write to skip_reason"
    )


async def test_set_failed_by_task_id_unknown_is_noop(queue):
    # must not raise
    await queue.set_failed_by_task_id("nonexistent-uuid", "error")


# ---------------------------------------------------------------------------
# M8: create_item must store created_at/updated_at as ISO strings, not floats
# ---------------------------------------------------------------------------

async def test_create_item_stores_iso_timestamps(queue, db):
    """M8: created_at and updated_at must be ISO-8601 strings, not Unix floats.

    Frontend new Date("1748...") → Invalid Date. ISO strings parse correctly
    in all JS environments.
    """
    from datetime import datetime

    item_id = await queue.create_item("planner", "printable_pdf", [])
    row = await db.execute(
        "SELECT created_at, updated_at FROM production_queue WHERE id=?", (item_id,)
    )
    r = await row.fetchone()
    created_at = r["created_at"]
    updated_at = r["updated_at"]

    # Must be a string, not a float
    assert isinstance(created_at, str), (
        f"created_at is {type(created_at).__name__}={created_at!r} — must be ISO string"
    )
    assert isinstance(updated_at, str), (
        f"updated_at is {type(updated_at).__name__}={updated_at!r} — must be ISO string"
    )

    # Must be parseable as a datetime (validates frontend new Date() compatibility)
    try:
        datetime.fromisoformat(created_at)
        datetime.fromisoformat(updated_at)
    except ValueError as exc:
        raise AssertionError(
            f"Timestamp not parseable as ISO date: {exc}"
        ) from exc
