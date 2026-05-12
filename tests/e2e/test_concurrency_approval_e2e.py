"""tests/e2e/test_concurrency_approval_e2e.py

E2E tests for the approval deduplication mechanism in autopilot.py (CNC-027).

The module-level _processed_approvals (OrderedDict) and _approval_cb_lock
(asyncio.Lock) guard against double-tap / re-delivered Telegram callback
queries calling register_approval more than once for the same cb_id.

Tests:
  CA1 — Two concurrent approval signals for the same cb_id → exactly 1 downstream call
  CA2 — Concurrent signals for different cb_ids → both reach downstream (2 calls)
  CA3 — _approval_cb_lock released after processing (subsequent cb_id not blocked)
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import apps.backend.telegram.handlers.autopilot as _mod
from apps.backend.telegram.handlers.autopilot import handle_approval_callback


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_query(cb_id: str, data: str, user_id: int = 100) -> AsyncMock:
    """Build a mock Telegram CallbackQuery with an explicit string cb_id."""
    query = AsyncMock()
    query.id = cb_id          # string — mirrors real Telegram callback_query.id
    query.data = data
    query.from_user = MagicMock()
    query.from_user.id = user_id
    query.message = AsyncMock()
    return query


def _make_update(query: AsyncMock) -> MagicMock:
    update = MagicMock()
    update.callback_query = query
    return update


def _make_deps(loop_mock: AsyncMock) -> MagicMock:
    deps = MagicMock()
    deps.autopilot_loop = loop_mock
    return deps


def _make_context() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# CA1 — Two concurrent approval signals for same cb_id → deduplicated
# ---------------------------------------------------------------------------

async def test_ca1_concurrent_same_cb_id_deduped(monkeypatch):
    """Two concurrent handle_approval_callback calls sharing the same cb_id
    must result in exactly one downstream register_approval call.

    The second signal must be silently discarded (no exception raised).
    Verifies CNC-027 dedup under realistic concurrency.
    """
    monkeypatch.setattr(_mod, "is_authorized", lambda uid: True)
    _mod._processed_approvals.clear()

    loop = AsyncMock()
    deps = _make_deps(loop)
    ctx = _make_context()

    # Two separate Update objects — same logical callback id
    query1 = _make_query(cb_id="cb-ca1-dup", data="approve:100")
    query2 = _make_query(cb_id="cb-ca1-dup", data="approve:100")
    update1 = _make_update(query1)
    update2 = _make_update(query2)

    await asyncio.wait_for(
        asyncio.gather(
            handle_approval_callback(deps, update1, ctx),
            handle_approval_callback(deps, update2, ctx),
            return_exceptions=True,
        ),
        timeout=5,
    )

    # Exactly one downstream call — the duplicate was discarded
    loop.register_approval.assert_called_once_with(100, "approved")


# ---------------------------------------------------------------------------
# CA2 — Concurrent signals for different cb_ids → both processed
# ---------------------------------------------------------------------------

async def test_ca2_concurrent_different_cb_ids_both_processed(monkeypatch):
    """Two concurrent calls with *different* cb_ids must both reach
    register_approval. This distinguishes correct per-id deduplication from
    a broken lock that serialises and drops legitimate concurrent traffic.
    """
    monkeypatch.setattr(_mod, "is_authorized", lambda uid: True)
    _mod._processed_approvals.clear()

    loop = AsyncMock()
    deps = _make_deps(loop)
    ctx = _make_context()

    query_a = _make_query(cb_id="cb-ca2-alpha", data="approve:200")
    query_b = _make_query(cb_id="cb-ca2-beta",  data="approve:201")
    update_a = _make_update(query_a)
    update_b = _make_update(query_b)

    await asyncio.wait_for(
        asyncio.gather(
            handle_approval_callback(deps, update_a, ctx),
            handle_approval_callback(deps, update_b, ctx),
            return_exceptions=True,
        ),
        timeout=5,
    )

    # Both unique cb_ids must have produced a downstream call
    assert loop.register_approval.call_count == 2
    actual = {c.args[:2] for c in loop.register_approval.call_args_list}
    assert actual == {(200, "approved"), (201, "approved")}


# ---------------------------------------------------------------------------
# CA3 — _approval_cb_lock released after processing (no deadlock)
# ---------------------------------------------------------------------------

async def test_ca3_lock_released_after_processing(monkeypatch):
    """After completing a call for cb_id 'X', the _approval_cb_lock must be
    free. A subsequent call for a *different* cb_id 'Y' must not block.

    asyncio.wait_for with a 2-second timeout detects any accidental
    lock retention after the handler returns.
    """
    monkeypatch.setattr(_mod, "is_authorized", lambda uid: True)
    _mod._processed_approvals.clear()

    loop = AsyncMock()
    deps = _make_deps(loop)
    ctx = _make_context()

    # First call — process cb_id X to completion
    query_x = _make_query(cb_id="cb-ca3-X", data="approve:300")
    update_x = _make_update(query_x)
    await handle_approval_callback(deps, update_x, ctx)

    # Second call — different cb_id Y; must not block (lock already released)
    query_y = _make_query(cb_id="cb-ca3-Y", data="approve:301")
    update_y = _make_update(query_y)
    await asyncio.wait_for(
        handle_approval_callback(deps, update_y, ctx),
        timeout=2,
    )

    # Both calls reached register_approval
    assert loop.register_approval.call_count == 2
    actual = {c.args[:2] for c in loop.register_approval.call_args_list}
    assert (300, "approved") in actual
    assert (301, "approved") in actual
