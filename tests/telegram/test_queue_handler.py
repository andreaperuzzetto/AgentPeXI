"""Tests for telegram/handlers/autopilot.py — approval and bundle callbacks."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.backend.telegram.handlers.autopilot import (
    handle_approval_callback,
    handle_bundle_callback,
    cmd_approve,
    cmd_skip,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_callback_query(data: str, user_id: int = 100):
    query = AsyncMock()
    query.data = data
    query.from_user = MagicMock()
    query.from_user.id = user_id
    query.message = AsyncMock()
    return query


def _make_update(callback_query=None, message=None):
    update = MagicMock()
    update.callback_query = callback_query
    update.message = AsyncMock() if message is None else message
    return update


def _make_deps(loop=None, memory=None):
    deps = MagicMock()
    deps.autopilot_loop = loop or AsyncMock()
    deps.memory = memory
    return deps


def _make_context(args=None):
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


# ---------------------------------------------------------------------------
# handle_approval_callback — approve action
# ---------------------------------------------------------------------------

async def test_approve_callback_calls_register_approval(monkeypatch):
    """approve:<id> callback registers 'approved' on the autopilot loop."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    loop = AsyncMock()
    deps = _make_deps(loop=loop)
    query = _make_callback_query("approve:42")
    update = _make_update(callback_query=query)

    await handle_approval_callback(deps, update, _make_context())

    loop.register_approval.assert_called_once_with(42, "approved")


# ---------------------------------------------------------------------------
# handle_approval_callback — skip action
# ---------------------------------------------------------------------------

async def test_skip_callback_calls_register_approval(monkeypatch):
    """skip:<id> callback registers 'skipped_user' on the autopilot loop."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    loop = AsyncMock()
    deps = _make_deps(loop=loop)
    query = _make_callback_query("skip:7")
    update = _make_update(callback_query=query)

    await handle_approval_callback(deps, update, _make_context())

    loop.register_approval.assert_called_once_with(7, "skipped_user")


# ---------------------------------------------------------------------------
# handle_approval_callback — unknown action
# ---------------------------------------------------------------------------

async def test_unknown_action_no_register_no_exception(monkeypatch):
    """Unknown callback action (not approve/skip) silently returns — no crash."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    loop = AsyncMock()
    deps = _make_deps(loop=loop)
    query = _make_callback_query("something:99")
    update = _make_update(callback_query=query)

    await handle_approval_callback(deps, update, _make_context())

    loop.register_approval.assert_not_called()


# ---------------------------------------------------------------------------
# handle_approval_callback — unauthorized user
# ---------------------------------------------------------------------------

async def test_unauthorized_user_denied(monkeypatch):
    """Unauthorized user gets 'Non autorizzato.' answer, loop not touched."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: False,
    )
    loop = AsyncMock()
    deps = _make_deps(loop=loop)
    query = _make_callback_query("approve:1", user_id=999)
    update = _make_update(callback_query=query)

    await handle_approval_callback(deps, update, _make_context())

    query.answer.assert_called_once_with("Non autorizzato.")
    loop.register_approval.assert_not_called()


# ---------------------------------------------------------------------------
# handle_approval_callback — invalid item_id (non-integer)
# ---------------------------------------------------------------------------

async def test_invalid_item_id_no_exception(monkeypatch):
    """Non-integer item_id in callback data → silently returns, no crash."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    loop = AsyncMock()
    deps = _make_deps(loop=loop)
    query = _make_callback_query("approve:not-a-number")
    update = _make_update(callback_query=query)

    await handle_approval_callback(deps, update, _make_context())

    loop.register_approval.assert_not_called()


# ---------------------------------------------------------------------------
# handle_approval_callback — no colon in data (malformed)
# ---------------------------------------------------------------------------

async def test_malformed_callback_data_no_exception(monkeypatch):
    """Callback data without ':' separator silently returns."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    loop = AsyncMock()
    deps = _make_deps(loop=loop)
    query = _make_callback_query("nocohereseparator")
    update = _make_update(callback_query=query)

    await handle_approval_callback(deps, update, _make_context())

    loop.register_approval.assert_not_called()


# ---------------------------------------------------------------------------
# handle_approval_callback — same id called twice (no duplicate detection)
# ---------------------------------------------------------------------------

async def test_same_callback_twice_second_is_deduped(monkeypatch):
    """Duplicate-callback detection (_processed_approvals) blocks second call."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    import apps.backend.telegram.handlers.autopilot as _mod
    _mod._processed_approvals.clear()  # reset module-level set for test isolation

    loop = AsyncMock()
    deps = _make_deps(loop=loop)
    query = _make_callback_query("approve:10")
    update = _make_update(callback_query=query)

    await handle_approval_callback(deps, update, _make_context())
    await handle_approval_callback(deps, update, _make_context())

    # Only the first call reaches register_approval; the second is blocked by deduplication
    loop.register_approval.assert_called_once_with(10, "approved")


# ---------------------------------------------------------------------------
# handle_bundle_callback — approve
# ---------------------------------------------------------------------------

async def test_bundle_approve_stores_approved_insight(monkeypatch):
    """bundle_approve:<cluster_id> stores 'approved' insight in memory."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    memory = AsyncMock()
    deps = _make_deps(memory=memory)
    # Regex requires exactly 12 lowercase hex chars
    query = _make_callback_query("bundle_approve:abcdef012345")
    update = _make_update(callback_query=query)

    await handle_bundle_callback(deps, update, _make_context())

    memory.store_insight.assert_called_once()
    call_kwargs = memory.store_insight.call_args[1]
    assert call_kwargs["metadata"]["status"] == "approved"
    assert "abcdef012345" in call_kwargs["metadata"]["cluster_id"]


# ---------------------------------------------------------------------------
# cmd_approve / cmd_skip helpers
# ---------------------------------------------------------------------------

async def test_cmd_approve_registers_approval(monkeypatch):
    """cmd_approve with valid item_id calls loop.register_approval(id, 'approved')."""
    loop = AsyncMock()
    deps = _make_deps(loop=loop)
    update = _make_update()
    ctx = _make_context(args=["5"])

    await cmd_approve(deps, update, ctx)

    loop.register_approval.assert_called_once_with(5, "approved")


async def test_cmd_skip_registers_skipped_user(monkeypatch):
    """cmd_skip with valid item_id calls loop.register_approval(id, 'skipped_user')."""
    loop = AsyncMock()
    deps = _make_deps(loop=loop)
    update = _make_update()
    ctx = _make_context(args=["8"])

    await cmd_skip(deps, update, ctx)

    loop.register_approval.assert_called_once_with(8, "skipped_user")
