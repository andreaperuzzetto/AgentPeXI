"""tests/e2e/test_concurrency_publisher_e2e.py — Concurrency safety for PublisherAgent.

Security audit context
----------------------
Three concurrency risks were identified. This module covers:

  CP1 — Double publication: two concurrent Publisher.run() calls on the SAME item
  CP2 — Lock hygiene: sequential publishers on DISTINCT items both publish (no over-blocking)
  CP3 — No-op safety: publisher with 0 publishable items produces no side-effects

Architecture note
-----------------
PublisherAgent has **NO explicit asyncio.Lock**.  Concurrency safety is delegated
entirely to APScheduler ``coalesce=True, max_instances=1`` on the scheduled job.
If those settings are bypassed (second scheduler instance, manual trigger racing a
scheduled run, bug in the scheduler config) nothing in the application code prevents
double publication.

CP1 is written as a **"worst-case race" test**: it simulates the missing lock and
documents the current behaviour (both publishers reach storage.move_to_uploaded).
If a lock is added to the codebase, the CP1 assertion must be updated to ``== 1``.

Execution model
---------------
asyncio_mode = "auto" (pytest-asyncio) — all tests are ``async def``, no explicit
@pytest.mark.asyncio decorator needed.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from apps.backend.agents.publisher import PublisherAgent
from apps.backend.core.models import AgentTask
from tests.e2e.conftest import _make_memory_manager


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _storage_mock() -> MagicMock:
    """Return a fresh storage mock with is_available=True."""
    s = MagicMock()
    s.is_available.return_value = True
    s.move_to_uploaded = MagicMock()
    return s


def _make_publisher(memory: Any, storage: MagicMock) -> PublisherAgent:
    """Instantiate PublisherAgent with mocked external dependencies."""
    etsy_mock = MagicMock()
    etsy_mock.mock_mode = True

    return PublisherAgent(
        anthropic_client=MagicMock(spec=anthropic.AsyncAnthropic),
        memory=memory,
        storage=storage,
        etsy_api=etsy_mock,
        ws_broadcaster=None,
        telegram_broadcaster=None,
        pinterest_agent=None,
    )


def _make_task(file_path: str | Path) -> AgentTask:
    """Return an AgentTask for a single file."""
    return AgentTask(
        agent_name="publisher",
        input_data={
            "file_paths": [str(file_path)],
            "niche": "Test Niche E2E",
            "product_type": "printable_pdf",
            "template": "basic",
            "color_schemes": ["blue"],
            "keywords": ["test", "e2e"],
            "size": "A4",
            "product_tier": "core",
            # pq_task_id intentionally omitted — avoids set_published() which
            # requires "scheduled" state and is out of scope for these tests.
        },
    )


def _tiny_pdf(path: Path) -> Path:
    """Write a minimal fake PDF file and return its path."""
    path.write_bytes(b"%PDF-1.4\n" + b"x" * 200)
    return path


def _fake_publish_single_factory(listing_id_prefix: str = "mock_listing"):
    """Return an async _publish_single replacement.

    Includes ``await asyncio.sleep(0)`` to yield control to the event loop,
    reproducing the interleaving that causes the CP1 race condition.
    """
    call_counter = [0]

    async def _impl(self, file_path, **kwargs):  # noqa: ANN001
        call_counter[0] += 1
        n = call_counter[0]
        await asyncio.sleep(0)  # force a context-switch: lets the second publisher proceed
        return {
            "niche": kwargs.get("niche", "Test Niche E2E"),
            "file_type": kwargs.get("product_type", "printable_pdf"),
            "status": "published",
            "listing_id": f"{listing_id_prefix}_{n}",
            "images_uploaded": 0,
            "seo_validated": True,
            "ab_variant": kwargs.get("ab_variant", "A"),
        }

    return _impl


def _fake_publish_single_no_listing():
    """Return a _publish_single that returns listing_id=None (simulates full failure)."""

    async def _impl(self, file_path, **kwargs):  # noqa: ANN001
        await asyncio.sleep(0)
        return {
            "niche": kwargs.get("niche", "Test Niche E2E"),
            "file_type": kwargs.get("product_type", "printable_pdf"),
            "status": "error",
            "listing_id": None,
            "images_uploaded": 0,
            "seo_validated": False,
            "error": "simulated failure for CP3",
        }

    return _impl


# ---------------------------------------------------------------------------
# CP1 — Two concurrent Publisher.run() → both attempt to publish the SAME item
# ---------------------------------------------------------------------------

async def test_cp1_concurrent_publishers_double_publication(tmp_path):
    """CP1 — worst-case race condition (no lock present in publisher code).

    NOTE: concurrency safety is delegated to APScheduler max_instances=1.
    If that protection is bypassed, BOTH publishers reach storage.move_to_uploaded
    for the same file, causing a duplicate Etsy listing.

    This test DOCUMENTS the vulnerability: it asserts call_count == 2 (current
    behaviour).  When a lock is added, update the assertion to ``== 1``.
    """
    pdf = _tiny_pdf(tmp_path / "product.pdf")
    storage = _storage_mock()

    memory = _make_memory_manager(tmp_path)
    await memory.init()
    try:
        pub1 = _make_publisher(memory, storage)
        pub2 = _make_publisher(memory, storage)

        task = _make_task(pdf)
        fake_publish = _fake_publish_single_factory("cp1_listing")

        with patch.object(PublisherAgent, "_publish_single", fake_publish):
            results = await asyncio.wait_for(
                asyncio.gather(pub1.run(task), pub2.run(task), return_exceptions=True),
                timeout=10,
            )

        # Expect both publishers to complete (no unhandled exceptions)
        for r in results:
            assert not isinstance(r, BaseException), (
                f"Publisher.run() raised unexpectedly: {r}"
            )

        # --- CURRENT BEHAVIOUR (vulnerability documented) ---
        # Both publishers checked is_file() synchronously before the first `await`
        # (memory.get_etsy_listings_count at line ~101 of publisher.py).  With
        # storage mocked (file never actually moved), both saw the file as valid and
        # both proceeded through _publish_single → move_to_uploaded.
        #
        # TODO security: add an asyncio.Lock (or an atomic DB UPDATE WHERE status='approved')
        # before calling _publish_single to prevent this race.
        assert storage.move_to_uploaded.call_count == 2, (
            f"Expected 2 calls (vulnerability documented), got {storage.move_to_uploaded.call_count}. "
            "If this assertion fails with 1, a lock was added — update this test to assert == 1."
        )

        # Both calls target the same file path
        called_paths = {call.args[0] for call in storage.move_to_uploaded.call_args_list}
        assert called_paths == {pdf}, "Both publishers should reference the same file"
    finally:
        await memory.close()


# ---------------------------------------------------------------------------
# CP2 — Two sequential publishers on DISTINCT items both succeed
# ---------------------------------------------------------------------------

async def test_cp2_sequential_publishers_distinct_items(tmp_path):
    """CP2 — verifies that sequential publishing works correctly for distinct items.

    This test distinguishes "correct lock behaviour" from "lock that blocks all work":
    running two Publishers sequentially on different files must produce two published
    items (call_count == 2).  If CP1 is later fixed with a per-file lock, this test
    ensures the lock does not block unrelated items.
    """
    pdf_a = _tiny_pdf(tmp_path / "product_a.pdf")
    pdf_b = _tiny_pdf(tmp_path / "product_b.pdf")
    storage = _storage_mock()

    memory = _make_memory_manager(tmp_path)
    await memory.init()
    try:
        pub = _make_publisher(memory, storage)
        task_a = _make_task(pdf_a)
        task_b = _make_task(pdf_b)

        fake_publish = _fake_publish_single_factory("cp2_listing")

        with patch.object(PublisherAgent, "_publish_single", fake_publish):
            result_a = await asyncio.wait_for(pub.run(task_a), timeout=10)
            result_b = await asyncio.wait_for(pub.run(task_b), timeout=10)

        assert result_a.output_data["listings_created"] == 1, (
            "First publisher should have created 1 listing"
        )
        assert result_b.output_data["listings_created"] == 1, (
            "Second publisher should have created 1 listing"
        )
        assert storage.move_to_uploaded.call_count == 2, (
            f"Expected 2 total move_to_uploaded calls (one per item), got {storage.move_to_uploaded.call_count}"
        )

        # Verify each publisher moved its own file
        moved_paths = {call.args[0] for call in storage.move_to_uploaded.call_args_list}
        assert moved_paths == {pdf_a, pdf_b}
    finally:
        await memory.close()


# ---------------------------------------------------------------------------
# CP3 — Publisher.run() with no publishable items → clean no-op
# ---------------------------------------------------------------------------

async def test_cp3_no_publishable_items_noop(tmp_path):
    """CP3 — publisher called but _publish_single returns listing_id=None for all items.

    Simulates: etsy_api rejects the listing, or a policy guard blocks publication.
    Expected behaviour: no exception, storage.move_to_uploaded never called, DB
    state unchanged (no etsy_listings rows inserted).
    """
    pdf = _tiny_pdf(tmp_path / "rejected.pdf")
    storage = _storage_mock()

    memory = _make_memory_manager(tmp_path)
    await memory.init()
    try:
        pub = _make_publisher(memory, storage)
        task = _make_task(pdf)

        fake_publish = _fake_publish_single_no_listing()

        with patch.object(PublisherAgent, "_publish_single", fake_publish):
            result = await asyncio.wait_for(pub.run(task), timeout=10)

        # No exception should propagate
        assert result is not None

        # storage.move_to_uploaded must NOT be called when listing_id is None
        storage.move_to_uploaded.assert_not_called()

        # listings_created == 0
        assert result.output_data["listings_created"] == 0, (
            f"Expected 0 listings created, got {result.output_data['listings_created']}"
        )

        # DB: no etsy_listings row was inserted
        db = await memory.get_db()
        cur = await db.execute("SELECT COUNT(*) FROM etsy_listings")
        row = await cur.fetchone()
        assert row[0] == 0, f"Expected 0 etsy_listings rows, found {row[0]}"
    finally:
        await memory.close()
