"""tests/e2e/test_resilience_e2e.py — DB resilience under runtime exceptions.

Verifies that when an agent raises an exception at runtime, the DB state remains
consistent: no corruption, no items stuck in transient state, and the system can
resume normal operation.

All tests are async def (asyncio_mode="auto", no asyncio.run()).

RE1: Publisher exception in _publish_single → PQ item stays in valid state
RE2: Agent log records failure correctly (status='failed', error in output_data)
RE3: DB queryable after a forced aiosqlite.Error in a mixin-level operation
RE4: Recovery — second run() after first failure publishes item correctly
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from apps.backend.agents.publisher import PublisherAgent
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentTask, TaskStatus
from apps.backend.core.production_queue import ProductionQueueService

from tests.e2e.conftest import _make_memory_manager

# ---------------------------------------------------------------------------
# Full production_queue schema for stand-alone PQ tests (RE3).
# Mirrors the schema used in test_full_pipeline.py — no MemoryManager.init()
# migrations required since the table is created fully-featured from the start.
# ---------------------------------------------------------------------------

_FULL_PQ_SCHEMA = """
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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _advance_to_scheduled(queue: ProductionQueueService) -> tuple[int, str]:
    """Creates a PQ item and moves it through all states up to 'scheduled'.

    Returns (item_id: int, pq_task_id: str).

    pq_task_id is the UUID stored in production_queue.task_id (the column
    used by the publisher to look up items via get_item_by_task_id).
    It is distinct from AgentTask.task_id.
    """
    item_id = await queue.create_item(
        niche="resilience_niche",
        product_type="printable_pdf",
        keywords=["planner", "minimal"],
        entry_score=0.75,
    )
    await queue.set_design_ready(
        item_id=item_id,
        design_prompt="minimal planner",
        image_url="https://cdn.example.com/test.png",
        thumbnail_path="/tmp/thumb.jpg",
        title="Minimal Planner",
        description="A clean minimal planner",
        tags=["planner", "minimal"],
        price=4.99,
    )
    await queue.set_approved(item_id)
    await queue.assign_slot(item_id, time.time() + 3600)

    # Retrieve the UUID task_id from the DB row directly (not in the dataclass)
    cursor = await queue._db.execute(
        "SELECT task_id FROM production_queue WHERE id = ?", (item_id,)
    )
    row = await cursor.fetchone()
    pq_task_id: str = row["task_id"]
    return item_id, pq_task_id


def _make_publisher(
    memory: MemoryManager,
) -> tuple[PublisherAgent, MagicMock]:
    """Builds a PublisherAgent with mocked external dependencies.

    Returns (publisher, mock_storage).
    mock_storage.move_to_uploaded is a plain MagicMock (sync) so call counts
    can be asserted without awaiting.
    """
    mock_anthropic = MagicMock()

    mock_storage = MagicMock()
    mock_storage.is_available.return_value = True
    mock_storage.move_to_uploaded = MagicMock()  # sync method on StorageManager

    mock_etsy = MagicMock()
    mock_etsy.mock_mode = True  # triggers _generate_mock_thumbnail path in _publish_single

    publisher = PublisherAgent(
        anthropic_client=mock_anthropic,
        memory=memory,
        storage=mock_storage,
        etsy_api=mock_etsy,
    )
    return publisher, mock_storage


# ---------------------------------------------------------------------------
# RE1 — Publisher exception in _publish_single → PQ item stays in valid state
# ---------------------------------------------------------------------------

async def test_re1_publish_single_exception_leaves_db_consistent(tmp_path):
    """RE1: RuntimeError from _publish_single is caught by run(); PQ item not corrupted.

    The publisher.run() catches per-file exceptions in a try/except loop and
    appends an error entry to publish_results without propagating the exception.
    When all files fail (listing_ids is empty), set_published is never called,
    so the PQ item stays in its original 'scheduled' state — a valid, well-defined
    state suitable for retry.  The DB must remain queryable afterwards.
    """
    mm = _make_memory_manager(tmp_path)
    mm._chroma_lock = asyncio.Lock()
    await mm.init()

    queue = ProductionQueueService(await mm.get_db())
    item_id, pq_task_id = await _advance_to_scheduled(queue)

    # Pre-condition: item is in 'scheduled'
    assert (await queue.get_item(item_id)).status == "scheduled"

    # publisher.run() validates file existence before the loop — needs a real file
    fake_pdf = tmp_path / "product_re1.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake content re1")

    publisher, _ = _make_publisher(mm)

    task = AgentTask(
        agent_name="publisher",
        task_id="re1-task-001",
        input_data={
            "file_paths": [str(fake_pdf)],
            "niche": "resilience_niche",
            "product_type": "printable_pdf",
            "production_queue_task_id": pq_task_id,
        },
    )

    # --- Fault injection: _publish_single raises for every file ---
    with patch.object(publisher, "_publish_single", side_effect=RuntimeError("test failure")):
        result = await asyncio.wait_for(publisher.run(task), timeout=10)

    # run() must NOT propagate the exception — it catches per-file errors internally
    assert result is not None
    # All files failed → _calculate_status returns FAILED
    assert result.status == TaskStatus.FAILED

    # PQ item must remain in a valid, consistent state.
    # set_published was never called (listing_ids is empty), so status stays 'scheduled'.
    item_after = await asyncio.wait_for(queue.get_item(item_id), timeout=10)
    assert item_after is not None
    assert item_after.status == "scheduled"  # unchanged — not stuck in a partial/unknown state

    # DB must still be queryable (no connection or transaction corruption)
    scheduled_items = await asyncio.wait_for(
        queue.get_items_by_status("scheduled"), timeout=10
    )
    assert any(i.id == item_id for i in scheduled_items)

    await mm.close()


# ---------------------------------------------------------------------------
# RE2 — Agent log records failure correctly
# ---------------------------------------------------------------------------

async def test_re2_agent_log_records_failure_correctly(tmp_path):
    """RE2: finalize_agent_task(status='failed') persists the error; other tasks unchanged.

    Verifies:
    - The failed task has status='failed' in agent_logs
    - The error string is stored in output_data['error'] (the field used by execute())
    - A control task created alongside has its status and output_data untouched
    """
    mm = _make_memory_manager(tmp_path)
    mm._chroma_lock = asyncio.Lock()
    await mm.init()

    task_id_fail = "re2-task-001-fail"
    task_id_ctrl = "re2-task-002-control"
    error_msg = "simulated runtime failure for RE2"

    # Create two tasks both initially 'running'
    await mm.log_agent_task(
        agent_name="test_agent",
        task_id=task_id_fail,
        status="running",
        input_data={"action": "publish"},
    )
    await mm.log_agent_task(
        agent_name="test_agent",
        task_id=task_id_ctrl,
        status="running",
        input_data={"action": "research"},
    )

    # Finalize the first task as failed
    await mm.finalize_agent_task(
        task_id=task_id_fail,
        status="failed",
        output_data={"error": error_msg},
    )

    # --- Verify: failed task has correct status and error ---
    task_fail = await asyncio.wait_for(mm.get_task_by_id(task_id_fail), timeout=10)
    assert task_fail is not None
    assert task_fail["status"] == "failed"
    assert task_fail["output_data"] is not None
    assert task_fail["output_data"]["error"] == error_msg

    # --- Verify: control task is completely unchanged (no side effect) ---
    task_ctrl = await asyncio.wait_for(mm.get_task_by_id(task_id_ctrl), timeout=10)
    assert task_ctrl is not None
    assert task_ctrl["status"] == "running"  # not affected by the other task's failure
    assert task_ctrl["output_data"] is None  # never finalized

    await mm.close()


# ---------------------------------------------------------------------------
# RE3 — DB queryable after aiosqlite.Error in a mixin-level operation
# ---------------------------------------------------------------------------

async def test_re3_db_queryable_after_mixin_exception():
    """RE3: after a forced aiosqlite.Error, existing items remain readable and new writes work.

    Uses an in-memory SQLite DB with the full PQ schema.
    Patches conn.execute to raise aiosqlite.Error exactly once (simulating a
    transient DB failure at the mixin level), then verifies DB integrity.
    """
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_FULL_PQ_SCHEMA)
        await conn.commit()

        queue = ProductionQueueService(conn)

        # Insert 3 items normally
        item_ids: list[int] = []
        for i in range(3):
            iid = await queue.create_item(
                niche=f"resilience_re3_niche_{i}",
                product_type="printable_pdf",
                keywords=[f"tag{i}"],
                entry_score=0.5 + i * 0.1,
            )
            item_ids.append(iid)

        # Pre-condition: all 3 items exist and are readable
        for iid in item_ids:
            assert (await queue.get_item(iid)) is not None

        # --- Fault injection: force aiosqlite.Error on the first execute call ---
        _fired = False
        _original_execute = conn.execute  # capture the bound method before patching

        async def _flaky_execute(sql, params=()):
            nonlocal _fired
            if not _fired:
                _fired = True
                raise aiosqlite.Error("injected DB error for RE3")
            return await _original_execute(sql, params)

        with patch.object(conn, "execute", new=_flaky_execute):
            with pytest.raises(aiosqlite.Error, match="injected DB error"):
                await asyncio.wait_for(queue.get_item(item_ids[0]), timeout=10)

        # After the patch is removed, all 3 original items must still be readable
        for iid in item_ids:
            item = await asyncio.wait_for(queue.get_item(iid), timeout=10)
            assert item is not None, f"item {iid} missing after injected error — DB corrupted"

        # New writes must also succeed (DB not in an unrecoverable state)
        new_id = await asyncio.wait_for(
            queue.create_item(
                niche="resilience_re3_post_error",
                product_type="printable_pdf",
                keywords=["recovery"],
            ),
            timeout=10,
        )
        new_item = await asyncio.wait_for(queue.get_item(new_id), timeout=10)
        assert new_item is not None
        assert new_item.niche == "resilience_re3_post_error"
        assert new_item.status == "pending_design"


# ---------------------------------------------------------------------------
# RE4 — Recovery: second run() after first failure publishes correctly
# ---------------------------------------------------------------------------

async def test_re4_recovery_second_run_publishes(tmp_path):
    """RE4: after _publish_single fails (item stays 'scheduled'), second run publishes it.

    First run:
      - _publish_single raises RuntimeError → listing_ids is empty
      - set_published is NOT called → item stays in 'scheduled'
      - storage.move_to_uploaded is NOT called

    Second run (recovery):
      - _publish_single returns a valid result with listing_id
      - set_published IS called → item transitions to 'published'
      - storage.move_to_uploaded IS called exactly once
    """
    mm = _make_memory_manager(tmp_path)
    mm._chroma_lock = asyncio.Lock()
    await mm.init()

    queue = ProductionQueueService(await mm.get_db())
    item_id, pq_task_id = await _advance_to_scheduled(queue)

    # publisher.run() requires at least one existing file
    fake_pdf = tmp_path / "product_re4.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake content re4")

    publisher, mock_storage = _make_publisher(mm)

    task = AgentTask(
        agent_name="publisher",
        task_id="re4-task-001",
        input_data={
            "file_paths": [str(fake_pdf)],
            "niche": "resilience_niche",
            "product_type": "printable_pdf",
            "production_queue_task_id": pq_task_id,
        },
    )

    # --- First run: _publish_single raises → no listing created, item stays 'scheduled' ---
    with patch.object(publisher, "_publish_single", side_effect=RuntimeError("first run failure")):
        result1 = await asyncio.wait_for(publisher.run(task), timeout=10)

    assert result1.status == TaskStatus.FAILED  # all files failed
    item_after_first = await asyncio.wait_for(queue.get_item(item_id), timeout=10)
    assert item_after_first.status == "scheduled"   # not published — retryable
    assert mock_storage.move_to_uploaded.call_count == 0  # never reached

    # --- Second run: _publish_single returns a valid result (recovery) ---
    fake_listing_id = "fake_listing_re4_001"
    successful_publish_result = {
        "niche": "resilience_niche",
        "file_type": "printable_pdf",
        "template": "",
        "color_scheme": "",
        "ab_variant": "A",
        "listing_id": fake_listing_id,
        "images_uploaded": 0,
        "seo_validated": False,
        "price_source": "fallback_hardcoded",
        "status": "published",
    }
    mock_publish_single = AsyncMock(return_value=successful_publish_result)

    with patch.object(publisher, "_publish_single", mock_publish_single):
        result2 = await asyncio.wait_for(publisher.run(task), timeout=10)

    # Verify: second run returned completed status (100% success rate)
    assert result2.status == TaskStatus.COMPLETED

    # Verify: PQ item is now 'published' with the correct etsy_listing_id
    item_final = await asyncio.wait_for(queue.get_item(item_id), timeout=10)
    assert item_final.status == "published"
    assert item_final.etsy_listing_id == fake_listing_id

    # Verify: move_to_uploaded called exactly once (during the successful second run)
    assert mock_storage.move_to_uploaded.call_count == 1

    await mm.close()
