"""C.1 — Telegram bundle approval flow.

Tests:
  - build_bundle_keyboard: constructs InlineKeyboardMarkup with correct callback_data
  - _parse_bundle_callback: parses bundle_approve/bundle_decline callback strings
  - _notify_bundle_pending: async method on _ResearchAnalysisMixin
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from apps.backend.telegram.callbacks import _parse_bundle_callback, build_bundle_keyboard


# ---------------------------------------------------------------------------
# build_bundle_keyboard
# ---------------------------------------------------------------------------

def test_build_bundle_keyboard_contains_approve_and_decline():
    kb = build_bundle_keyboard("abc123def456")
    buttons = kb.inline_keyboard[0]
    callback_data_values = [b.callback_data for b in buttons]
    assert "bundle_approve:abc123def456" in callback_data_values
    assert "bundle_decline:abc123def456" in callback_data_values


def test_build_bundle_keyboard_two_buttons_in_one_row():
    kb = build_bundle_keyboard("abc123def456")
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 2


# ---------------------------------------------------------------------------
# _parse_bundle_callback
# ---------------------------------------------------------------------------

def test_parse_bundle_callback_approve_returns_action_and_cluster_id():
    result = _parse_bundle_callback("bundle_approve:abc123def456")
    assert result == ("approve", "abc123def456")


def test_parse_bundle_callback_decline_returns_action_and_cluster_id():
    result = _parse_bundle_callback("bundle_decline:abc123def456")
    assert result == ("decline", "abc123def456")


def test_parse_bundle_callback_invalid_returns_none():
    assert _parse_bundle_callback("approve:123") is None          # old-style action
    assert _parse_bundle_callback("bundle_approve:not-hex!!") is None  # non-hex cluster_id
    assert _parse_bundle_callback("bundle_approve:tooshort") is None   # < 12 hex chars
    assert _parse_bundle_callback("") is None                          # empty string
    assert _parse_bundle_callback("bundle_approve:") is None           # no cluster_id


# ---------------------------------------------------------------------------
# _notify_bundle_pending  (C.1 — async method on _ResearchAnalysisMixin)
# ---------------------------------------------------------------------------

class _MinimalMixin:
    """Minimal concrete class to test _ResearchAnalysisMixin async methods."""
    _telegram_markup_sender = None

    async def _notify_bundle_pending(self, niche_name: str, bundle: dict, cluster_id: str) -> None:
        from apps.backend.agents._research.analysis_mixin import _ResearchAnalysisMixin
        return await _ResearchAnalysisMixin._notify_bundle_pending(self, niche_name, bundle, cluster_id)


@pytest.mark.asyncio
async def test_notify_bundle_pending_no_op_when_sender_none():
    obj = _MinimalMixin()
    obj._telegram_markup_sender = None
    # Must not raise
    await obj._notify_bundle_pending("party supplies", {"title": "Party Bundle", "price_usd": 9.99}, "abc123def456")


@pytest.mark.asyncio
async def test_notify_bundle_pending_calls_sender_with_text_and_keyboard():
    sender = AsyncMock()
    obj = _MinimalMixin()
    obj._telegram_markup_sender = sender
    bundle = {"title": "Party Bundle", "price_usd": 9.99, "items_included": ["item1", "item2"]}
    await obj._notify_bundle_pending("party supplies", bundle, "abc123def456")
    assert sender.call_count == 1
    text_arg, keyboard_arg = sender.call_args[0]
    assert "party supplies" in text_arg
    assert "Party Bundle" in text_arg
    from telegram import InlineKeyboardMarkup
    assert isinstance(keyboard_arg, InlineKeyboardMarkup)


@pytest.mark.asyncio
async def test_notify_bundle_pending_catches_sender_exception():
    async def failing_sender(text, keyboard):
        raise RuntimeError("Telegram timeout")

    obj = _MinimalMixin()
    obj._telegram_markup_sender = failing_sender
    # Must not raise even if sender throws
    await obj._notify_bundle_pending("party supplies", {"title": "X", "price_usd": 5}, "abc123def456")


@pytest.mark.asyncio
async def test_notify_bundle_pending_text_contains_price():
    sender = AsyncMock()
    obj = _MinimalMixin()
    obj._telegram_markup_sender = sender
    bundle = {"title": "Party Bundle", "price_usd": 12.5, "items_included": []}
    await obj._notify_bundle_pending("party supplies", bundle, "abc123def456")
    text_arg = sender.call_args[0][0]
    assert "12.5" in text_arg or "12,5" in text_arg or "$12" in text_arg
