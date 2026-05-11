"""Coverage tests for _AnalyticsDiagnosticsMixin.

Target: apps/backend/agents/_analytics/diagnostics_mixin.py
Goal:   >= 75% coverage

Methods under test:
  - poll_listing_performance()          APScheduler job — called directly
  - run_ladder_diagnostic_all()         iterates all published listings
  - run_ladder_diagnostic_by_id(id)     ladder classification per listing
  - _trigger_remediation(item, …)       remediation actions
  - _remediation_attempted_recently()   cooldown guard (sync)
  - _log_remediation_attempt()          cooldown log (sync)
"""
from __future__ import annotations

import asyncio
import time as _time
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from apps.backend.agents._analytics.constants import (
    CONV_MIN,
    CTR_MIN,
    MIN_DAYS_LIVE,
    REMEDIATION_COOLDOWN_HOURS,
    VIEWS_MIN_7DAYS,
)
from apps.backend.agents._analytics.diagnostics_mixin import _AnalyticsDiagnosticsMixin


# ---------------------------------------------------------------------------
# Concrete agent class assembling the mixin
# ---------------------------------------------------------------------------

class FakeDiagnosticsAgent(_AnalyticsDiagnosticsMixin):
    """Minimal concrete agent that wires all mixin dependencies."""

    def __init__(self):
        self.memory = AsyncMock()
        self.etsy_api = AsyncMock()
        self._production_queue = AsyncMock()
        self._learning_loop = AsyncMock()
        self._notify_telegram = AsyncMock()
        self._remediation_log: dict = {}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_item(**kwargs):
    """Build a MagicMock that looks like a ProductionQueueItem."""
    item = MagicMock()
    item.id = kwargs.get("id", 1)
    item.etsy_listing_id = kwargs.get("etsy_listing_id", "L123")
    item.niche = kwargs.get("niche", "wedding")
    item.product_type = kwargs.get("product_type", "print")
    item.published_at = kwargs.get("published_at", _time.time() - 8 * 86400)
    item.keywords = kwargs.get("keywords", ["key1"])
    item.listing_title = kwargs.get("listing_title", "My Listing Title")
    return item


def _make_db_mock(row=None):
    """Return (db_mock, cursor_mock) — fetchone returns *row*."""
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=row)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=cursor)
    db.commit = AsyncMock()
    return db, cursor


def _make_ok_row(**kwargs):
    """Build a row dict that passes all ladder thresholds → level=ok."""
    return {
        "views":           kwargs.get("views",           VIEWS_MIN_7DAYS + 5),
        "clicks":          kwargs.get("clicks",          20),
        "orders":          kwargs.get("orders",          2),
        "ctr":             kwargs.get("ctr",             CTR_MIN + 0.01),
        "conversion_rate": kwargs.get("conversion_rate", CONV_MIN + 0.01),
        "days_live":       kwargs.get("days_live",       MIN_DAYS_LIVE + 1),
        "template":        kwargs.get("template",        "tmpl_a"),
        "color_scheme":    kwargs.get("color_scheme",    "blue"),
    }


# ===========================================================================
# _remediation_attempted_recently / _log_remediation_attempt  (sync unit)
# ===========================================================================

class TestRemediationLog:

    def setup_method(self):
        self.agent = FakeDiagnosticsAgent()

    def test_no_entry_returns_false(self):
        assert self.agent._remediation_attempted_recently(99, "rewrite_seo") is False

    def test_recent_entry_returns_true(self):
        item_id, action = 1, "rewrite_seo"
        self.agent._remediation_log[item_id] = {action: _time.time() - 3600}   # 1h ago
        assert self.agent._remediation_attempted_recently(item_id, action) is True

    def test_old_entry_returns_false(self):
        item_id, action = 2, "regen_thumbnail"
        # Older than REMEDIATION_COOLDOWN_HOURS → cooldown expired
        self.agent._remediation_log[item_id] = {
            action: _time.time() - (REMEDIATION_COOLDOWN_HOURS + 1) * 3600
        }
        assert self.agent._remediation_attempted_recently(item_id, action) is False

    def test_log_creates_new_item_entry(self):
        self.agent._log_remediation_attempt(5, "rewrite_seo")
        assert 5 in self.agent._remediation_log
        assert "rewrite_seo" in self.agent._remediation_log[5]
        assert self.agent._remediation_log[5]["rewrite_seo"] == pytest.approx(
            _time.time(), abs=2
        )

    def test_log_updates_existing_timestamp(self):
        self.agent._remediation_log[5] = {"rewrite_seo": 0.0}
        self.agent._log_remediation_attempt(5, "rewrite_seo")
        assert self.agent._remediation_log[5]["rewrite_seo"] == pytest.approx(
            _time.time(), abs=2
        )

    def test_log_adds_new_action_to_existing_item(self):
        self.agent._remediation_log[5] = {"rewrite_seo": _time.time()}
        self.agent._log_remediation_attempt(5, "regen_thumbnail")
        assert "regen_thumbnail" in self.agent._remediation_log[5]

    def test_different_actions_tracked_independently(self):
        self.agent._log_remediation_attempt(7, "rewrite_seo")
        self.agent._log_remediation_attempt(7, "regen_thumbnail")
        assert "rewrite_seo" in self.agent._remediation_log[7]
        assert "regen_thumbnail" in self.agent._remediation_log[7]


# ===========================================================================
# run_ladder_diagnostic_by_id
# ===========================================================================

class TestRunLadderDiagnosticById:

    def setup_method(self):
        self.agent = FakeDiagnosticsAgent()

    async def test_no_production_queue_returns_error(self):
        self.agent._production_queue = None
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        assert "error" in result
        assert "production_queue" in result["error"]

    async def test_item_not_found_returns_error(self):
        self.agent._production_queue.get_item = AsyncMock(return_value=None)
        db, _ = _make_db_mock()
        self.agent.memory.get_db = AsyncMock(return_value=db)
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(42), timeout=5
        )
        assert "error" in result
        assert "42" in result["error"]

    async def test_too_new_when_no_row(self):
        item = _make_item(id=1)
        self.agent._production_queue.get_item = AsyncMock(return_value=item)
        db, _ = _make_db_mock(row=None)
        self.agent.memory.get_db = AsyncMock(return_value=db)
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        assert result["level"] == "too_new"
        assert result["action"] is None

    async def test_too_new_when_days_live_below_min(self):
        item = _make_item(id=1)
        self.agent._production_queue.get_item = AsyncMock(return_value=item)
        row = _make_ok_row(days_live=MIN_DAYS_LIVE - 1)
        db, _ = _make_db_mock(row=row)
        self.agent.memory.get_db = AsyncMock(return_value=db)
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        assert result["level"] == "too_new"

    async def test_exactly_min_days_live_not_too_new(self):
        """days_live == MIN_DAYS_LIVE passes the threshold check (not < MIN_DAYS_LIVE)."""
        item = _make_item(id=1)
        self.agent._production_queue.get_item = AsyncMock(return_value=item)
        row = _make_ok_row(days_live=MIN_DAYS_LIVE)
        db, _ = _make_db_mock(row=row)
        self.agent.memory.get_db = AsyncMock(return_value=db)
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        assert result["level"] != "too_new"

    async def test_views_low_boundary(self):
        """views < VIEWS_MIN_7DAYS → level=views_low."""
        item = _make_item(id=1)
        self.agent._production_queue.get_item = AsyncMock(return_value=item)
        row = _make_ok_row(views=VIEWS_MIN_7DAYS - 1)
        db, _ = _make_db_mock(row=row)
        self.agent.memory.get_db = AsyncMock(return_value=db)
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        assert result["level"] == "views_low"
        assert result["action"] == "rewrite_seo"

    async def test_views_ok_boundary(self):
        """views >= VIEWS_MIN_7DAYS moves past views check."""
        item = _make_item(id=1)
        self.agent._production_queue.get_item = AsyncMock(return_value=item)
        row = _make_ok_row(views=VIEWS_MIN_7DAYS)
        db, _ = _make_db_mock(row=row)
        self.agent.memory.get_db = AsyncMock(return_value=db)
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        assert result["level"] != "views_low"

    async def test_ctr_low_boundary(self):
        """ctr < CTR_MIN (and views ok) → level=ctr_low."""
        item = _make_item(id=1)
        self.agent._production_queue.get_item = AsyncMock(return_value=item)
        row = _make_ok_row(views=VIEWS_MIN_7DAYS + 5, ctr=CTR_MIN - 0.001)
        db, _ = _make_db_mock(row=row)
        self.agent.memory.get_db = AsyncMock(return_value=db)
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        assert result["level"] == "ctr_low"
        assert result["action"] == "regen_thumbnail"

    async def test_conv_low_with_enough_clicks(self):
        """conversion_rate < CONV_MIN and clicks >= 10 → level=conv_low."""
        item = _make_item(id=1)
        self.agent._production_queue.get_item = AsyncMock(return_value=item)
        row = _make_ok_row(
            views=VIEWS_MIN_7DAYS + 5,
            ctr=CTR_MIN + 0.01,
            conversion_rate=CONV_MIN - 0.001,
            clicks=15,
        )
        db, _ = _make_db_mock(row=row)
        self.agent.memory.get_db = AsyncMock(return_value=db)
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        assert result["level"] == "conv_low"
        assert result["action"] == "update_listing"

    async def test_conv_low_insufficient_clicks_yields_ok(self):
        """conversion_rate < CONV_MIN but clicks < 10 → level=ok (not enough data)."""
        item = _make_item(id=1)
        self.agent._production_queue.get_item = AsyncMock(return_value=item)
        row = _make_ok_row(
            views=VIEWS_MIN_7DAYS + 5,
            ctr=CTR_MIN + 0.01,
            conversion_rate=CONV_MIN - 0.001,
            clicks=5,   # < 10
        )
        db, _ = _make_db_mock(row=row)
        self.agent.memory.get_db = AsyncMock(return_value=db)
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        assert result["level"] == "ok"
        assert result["action"] is None

    async def test_all_ok(self):
        item = _make_item(id=1)
        self.agent._production_queue.get_item = AsyncMock(return_value=item)
        db, _ = _make_db_mock(row=_make_ok_row())
        self.agent.memory.get_db = AsyncMock(return_value=db)
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        assert result["level"] == "ok"
        assert result["action"] is None

    async def test_result_contains_expected_keys(self):
        item = _make_item(id=1)
        self.agent._production_queue.get_item = AsyncMock(return_value=item)
        db, _ = _make_db_mock(row=_make_ok_row())
        self.agent.memory.get_db = AsyncMock(return_value=db)
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        for key in ("item_id", "niche", "level", "action", "views", "ctr", "conv", "days_live"):
            assert key in result

    async def test_remediation_skipped_when_cooldown_active(self):
        item = _make_item(id=1)
        self.agent._production_queue.get_item = AsyncMock(return_value=item)
        row = _make_ok_row(views=VIEWS_MIN_7DAYS - 1)
        db, _ = _make_db_mock(row=row)
        self.agent.memory.get_db = AsyncMock(return_value=db)
        # Simulate recent attempt
        self.agent._remediation_log[1] = {"rewrite_seo": _time.time() - 3600}
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        assert result["level"] == "views_low"
        self.agent._notify_telegram.assert_not_called()

    async def test_remediation_triggered_when_no_cooldown(self):
        item = _make_item(id=1)
        self.agent._production_queue.get_item = AsyncMock(return_value=item)
        self.agent._production_queue.create_item = AsyncMock(return_value=99)
        row = _make_ok_row(views=VIEWS_MIN_7DAYS - 1)
        db, _ = _make_db_mock(row=row)
        self.agent.memory.get_db = AsyncMock(return_value=db)
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        assert result["level"] == "views_low"
        self.agent._notify_telegram.assert_awaited_once()

    async def test_db_update_and_commit_called(self):
        item = _make_item(id=1)
        self.agent._production_queue.get_item = AsyncMock(return_value=item)
        db, _ = _make_db_mock(row=_make_ok_row())
        self.agent.memory.get_db = AsyncMock(return_value=db)
        await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_by_id(1), timeout=5
        )
        # execute called for SELECT + UPDATE; commit called once
        assert db.execute.call_count >= 2
        db.commit.assert_awaited_once()


# ===========================================================================
# run_ladder_diagnostic_all
# ===========================================================================

class TestRunLadderDiagnosticAll:

    def setup_method(self):
        self.agent = FakeDiagnosticsAgent()

    async def test_no_production_queue_returns_empty_list(self):
        self.agent._production_queue = None
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_all(), timeout=5
        )
        assert result == []

    async def test_calls_diagnostic_for_each_published_item(self):
        items = [_make_item(id=i) for i in range(3)]
        self.agent._production_queue.get_recent = AsyncMock(return_value=items)
        self.agent.run_ladder_diagnostic_by_id = AsyncMock(return_value={"level": "ok"})
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_all(), timeout=5
        )
        assert len(result) == 3
        assert self.agent.run_ladder_diagnostic_by_id.call_count == 3

    async def test_empty_published_list_returns_empty(self):
        self.agent._production_queue.get_recent = AsyncMock(return_value=[])
        self.agent.run_ladder_diagnostic_by_id = AsyncMock(return_value={"level": "ok"})
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_all(), timeout=5
        )
        assert result == []

    async def test_aggregates_results_from_each_id(self):
        items = [_make_item(id=i) for i in range(2)]
        self.agent._production_queue.get_recent = AsyncMock(return_value=items)
        self.agent.run_ladder_diagnostic_by_id = AsyncMock(
            side_effect=[{"level": "views_low"}, {"level": "ok"}]
        )
        result = await asyncio.wait_for(
            self.agent.run_ladder_diagnostic_all(), timeout=5
        )
        assert result[0]["level"] == "views_low"
        assert result[1]["level"] == "ok"


# ===========================================================================
# _trigger_remediation
# ===========================================================================

class TestTriggerRemediation:

    def setup_method(self):
        self.agent = FakeDiagnosticsAgent()
        self.agent._production_queue.create_item = AsyncMock(return_value=77)

    # --- rewrite_seo ---

    async def test_rewrite_seo_happy_path(self):
        item = _make_item()
        row = _make_ok_row(views=10, days_live=10)
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "views_low", "rewrite_seo", row),
            timeout=5,
        )
        self.agent._learning_loop.flag_for_seo_revision.assert_awaited_once_with(
            item.niche, item.product_type
        )
        self.agent._production_queue.create_item.assert_awaited_once()
        self.agent._notify_telegram.assert_awaited_once()
        msg = self.agent._notify_telegram.call_args[0][0]
        assert "#ladder" in msg
        assert "#views_low" in msg

    async def test_rewrite_seo_no_learning_loop(self):
        self.agent._learning_loop = None
        item = _make_item()
        row = _make_ok_row(views=10, days_live=10)
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "views_low", "rewrite_seo", row),
            timeout=5,
        )
        # Should still enqueue and notify
        self.agent._production_queue.create_item.assert_awaited_once()
        self.agent._notify_telegram.assert_awaited_once()

    async def test_rewrite_seo_flag_raises_continues(self):
        self.agent._learning_loop.flag_for_seo_revision = AsyncMock(
            side_effect=RuntimeError("ll down")
        )
        item = _make_item()
        row = _make_ok_row(views=10, days_live=10)
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "views_low", "rewrite_seo", row),
            timeout=5,
        )
        # Exception caught — still notifies
        self.agent._notify_telegram.assert_awaited_once()

    async def test_rewrite_seo_create_item_raises_continues(self):
        self.agent._production_queue.create_item = AsyncMock(
            side_effect=RuntimeError("queue err")
        )
        item = _make_item()
        row = _make_ok_row(views=10, days_live=10)
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "views_low", "rewrite_seo", row),
            timeout=5,
        )
        self.agent._notify_telegram.assert_awaited_once()

    async def test_rewrite_seo_no_production_queue(self):
        self.agent._production_queue = None
        item = _make_item()
        row = _make_ok_row(views=10, days_live=10)
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "views_low", "rewrite_seo", row),
            timeout=5,
        )
        self.agent._notify_telegram.assert_awaited_once()

    # --- regen_thumbnail ---

    async def test_regen_thumbnail_happy_path(self):
        item = _make_item()
        row = _make_ok_row(ctr=0.005, template="t1", color_scheme="red")
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "ctr_low", "regen_thumbnail", row),
            timeout=5,
        )
        self.agent._learning_loop.flag_low_ctr.assert_awaited_once_with(
            item.niche, item.product_type, "t1", "red"
        )
        self.agent._production_queue.create_item.assert_awaited_once()
        self.agent._notify_telegram.assert_awaited_once()
        msg = self.agent._notify_telegram.call_args[0][0]
        assert "#ctr_low" in msg

    async def test_regen_thumbnail_no_learning_loop(self):
        self.agent._learning_loop = None
        item = _make_item()
        row = _make_ok_row(ctr=0.005)
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "ctr_low", "regen_thumbnail", row),
            timeout=5,
        )
        self.agent._notify_telegram.assert_awaited_once()

    async def test_regen_thumbnail_flag_raises_continues(self):
        self.agent._learning_loop.flag_low_ctr = AsyncMock(
            side_effect=RuntimeError("flag err")
        )
        item = _make_item()
        row = _make_ok_row(ctr=0.005)
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "ctr_low", "regen_thumbnail", row),
            timeout=5,
        )
        self.agent._notify_telegram.assert_awaited_once()

    async def test_regen_thumbnail_create_item_raises_continues(self):
        self.agent._production_queue.create_item = AsyncMock(
            side_effect=RuntimeError("q err")
        )
        item = _make_item()
        row = _make_ok_row(ctr=0.005)
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "ctr_low", "regen_thumbnail", row),
            timeout=5,
        )
        self.agent._notify_telegram.assert_awaited_once()

    async def test_regen_thumbnail_no_production_queue(self):
        self.agent._production_queue = None
        item = _make_item()
        row = _make_ok_row(ctr=0.005)
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "ctr_low", "regen_thumbnail", row),
            timeout=5,
        )
        self.agent._notify_telegram.assert_awaited_once()

    async def test_regen_thumbnail_empty_template_and_color(self):
        """row template/color_scheme falsy → treated as empty string."""
        item = _make_item()
        row = _make_ok_row(ctr=0.005, template="", color_scheme="")
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "ctr_low", "regen_thumbnail", row),
            timeout=5,
        )
        self.agent._notify_telegram.assert_awaited_once()

    # --- update_listing ---

    async def test_update_listing_notifies_only(self):
        item = _make_item()
        row = _make_ok_row(conversion_rate=0.001, clicks=20)
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "conv_low", "update_listing", row),
            timeout=5,
        )
        self.agent._notify_telegram.assert_awaited_once()
        msg = self.agent._notify_telegram.call_args[0][0]
        assert "#conv_low" in msg
        # No learning_loop or queue calls for update_listing
        self.agent._learning_loop.flag_for_seo_revision.assert_not_awaited()
        self.agent._learning_loop.flag_low_ctr.assert_not_awaited()
        self.agent._production_queue.create_item.assert_not_awaited()

    # --- unknown action ---

    async def test_unknown_action_returns_early_no_notify(self):
        item = _make_item()
        row = _make_ok_row()
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "ok", "unknown_action", row),
            timeout=5,
        )
        self.agent._notify_telegram.assert_not_awaited()

    # --- title fallback ---

    async def test_listing_title_fallback_to_niche(self):
        """When listing_title is falsy, the notification uses item.niche."""
        item = _make_item(listing_title=None, niche="xmas-gifts-2024")
        row = _make_ok_row(views=5, days_live=10)
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "views_low", "rewrite_seo", row),
            timeout=5,
        )
        msg = self.agent._notify_telegram.call_args[0][0]
        assert "xmas-gifts-2024" in msg

    async def test_long_title_truncated_to_60_chars(self):
        long_title = "A" * 80
        item = _make_item(listing_title=long_title)
        row = _make_ok_row(views=5, days_live=10)
        await asyncio.wait_for(
            self.agent._trigger_remediation(item, "views_low", "rewrite_seo", row),
            timeout=5,
        )
        msg = self.agent._notify_telegram.call_args[0][0]
        # Title capped at 60 chars: check no more than 60 A's appear contiguously
        assert "A" * 61 not in msg


# ===========================================================================
# poll_listing_performance  (APScheduler job — called directly without trigger)
# ===========================================================================

class TestPollListingPerformance:

    def setup_method(self):
        self.agent = FakeDiagnosticsAgent()

    async def test_no_production_queue_returns_immediately(self):
        self.agent._production_queue = None
        await asyncio.wait_for(
            self.agent.poll_listing_performance(), timeout=5
        )
        self.agent.memory.get_db.assert_not_called()

    async def test_empty_published_list_returns_immediately(self):
        self.agent._production_queue.get_recent = AsyncMock(return_value=[])
        await asyncio.wait_for(
            self.agent.poll_listing_performance(), timeout=5
        )
        self.agent.memory.get_db.assert_not_called()

    async def test_happy_path_inserts_snapshot_and_calls_ladder(self):
        item = _make_item(etsy_listing_id="L42", published_at=_time.time() - 10 * 86400)
        self.agent._production_queue.get_recent = AsyncMock(return_value=[item])
        self.agent.etsy_api.get_listing_stats = AsyncMock(return_value={
            "views": 50, "clicks": 10, "favorites": 5,
            "num_orders": 1, "revenue_eur": 12.0,
        })
        self.agent.memory.get_etsy_listings = AsyncMock(return_value=[
            {"listing_id": "L42", "template": "tmpl_x", "color_scheme": "gold"},
        ])
        db, _ = _make_db_mock()
        self.agent.memory.get_db = AsyncMock(return_value=db)
        self.agent.run_ladder_diagnostic_all = AsyncMock(return_value=[])

        await asyncio.wait_for(self.agent.poll_listing_performance(), timeout=5)

        db.execute.assert_called()
        db.commit.assert_called()
        self.agent.run_ladder_diagnostic_all.assert_awaited_once()

    async def test_matching_etsy_listing_sets_template_and_color(self):
        item = _make_item(etsy_listing_id="L99")
        self.agent._production_queue.get_recent = AsyncMock(return_value=[item])
        self.agent.etsy_api.get_listing_stats = AsyncMock(return_value={
            "views": 5, "clicks": 1, "favorites": 0,
            "num_orders": 0, "revenue_eur": 0.0,
        })
        self.agent.memory.get_etsy_listings = AsyncMock(return_value=[
            {"listing_id": "L99",  "template": "hero", "color_scheme": "navy"},
            {"listing_id": "L100", "template": "other", "color_scheme": "red"},
        ])
        db, _ = _make_db_mock()
        self.agent.memory.get_db = AsyncMock(return_value=db)
        self.agent.run_ladder_diagnostic_all = AsyncMock(return_value=[])

        await asyncio.wait_for(self.agent.poll_listing_performance(), timeout=5)

        # First db.execute call is the INSERT; check it carries hero / navy
        insert_params = db.execute.call_args_list[0][0][1]  # positional param tuple
        assert "hero" in insert_params
        assert "navy" in insert_params

    async def test_item_without_etsy_listing_id_skipped(self):
        item = _make_item(etsy_listing_id=None)
        self.agent._production_queue.get_recent = AsyncMock(return_value=[item])
        db, _ = _make_db_mock()
        self.agent.memory.get_db = AsyncMock(return_value=db)
        self.agent.run_ladder_diagnostic_all = AsyncMock(return_value=[])

        await asyncio.wait_for(self.agent.poll_listing_performance(), timeout=5)

        self.agent.etsy_api.get_listing_stats.assert_not_called()
        # No INSERT happened
        db.execute.assert_not_called()

    async def test_get_listing_stats_exception_item_skipped(self):
        item = _make_item(etsy_listing_id="L77")
        self.agent._production_queue.get_recent = AsyncMock(return_value=[item])
        self.agent.etsy_api.get_listing_stats = AsyncMock(
            side_effect=RuntimeError("Etsy API down")
        )
        db, _ = _make_db_mock()
        self.agent.memory.get_db = AsyncMock(return_value=db)
        self.agent.run_ladder_diagnostic_all = AsyncMock(return_value=[])

        await asyncio.wait_for(self.agent.poll_listing_performance(), timeout=5)

        # No INSERT for the failed item
        db.execute.assert_not_called()

    async def test_get_etsy_listings_exception_still_inserts(self):
        """Exception in get_etsy_listings is caught; INSERT proceeds with empty template."""
        item = _make_item(etsy_listing_id="L55")
        self.agent._production_queue.get_recent = AsyncMock(return_value=[item])
        self.agent.etsy_api.get_listing_stats = AsyncMock(return_value={
            "views": 20, "clicks": 3, "favorites": 1,
            "num_orders": 0, "revenue_eur": 0.0,
        })
        self.agent.memory.get_etsy_listings = AsyncMock(
            side_effect=RuntimeError("memory error")
        )
        db, _ = _make_db_mock()
        self.agent.memory.get_db = AsyncMock(return_value=db)
        self.agent.run_ladder_diagnostic_all = AsyncMock(return_value=[])

        await asyncio.wait_for(self.agent.poll_listing_performance(), timeout=5)

        db.execute.assert_called()

    async def test_published_at_none_sets_days_live_zero(self):
        item = _make_item(etsy_listing_id="L33", published_at=None)
        self.agent._production_queue.get_recent = AsyncMock(return_value=[item])
        self.agent.etsy_api.get_listing_stats = AsyncMock(return_value={
            "views": 0, "clicks": 0, "favorites": 0,
            "num_orders": 0, "revenue_eur": 0.0,
        })
        self.agent.memory.get_etsy_listings = AsyncMock(return_value=[])
        db, _ = _make_db_mock()
        self.agent.memory.get_db = AsyncMock(return_value=db)
        self.agent.run_ladder_diagnostic_all = AsyncMock(return_value=[])

        await asyncio.wait_for(self.agent.poll_listing_performance(), timeout=5)

        # days_live=0 → item still inserted
        insert_params = db.execute.call_args_list[0][0][1]
        assert 0 in insert_params   # days_live param

    async def test_learning_loop_update_called_after_ladder(self):
        item = _make_item(etsy_listing_id="L10")
        self.agent._production_queue.get_recent = AsyncMock(return_value=[item])
        self.agent.etsy_api.get_listing_stats = AsyncMock(return_value={
            "views": 20, "clicks": 3, "favorites": 1,
            "num_orders": 0, "revenue_eur": 0.0,
        })
        self.agent.memory.get_etsy_listings = AsyncMock(return_value=[])
        db, _ = _make_db_mock()
        self.agent.memory.get_db = AsyncMock(return_value=db)
        self.agent.run_ladder_diagnostic_all = AsyncMock(return_value=[])

        await asyncio.wait_for(self.agent.poll_listing_performance(), timeout=5)

        self.agent._learning_loop.update_niche_intelligence.assert_awaited_once()

    async def test_no_learning_loop_no_error(self):
        self.agent._learning_loop = None
        item = _make_item(etsy_listing_id="L10")
        self.agent._production_queue.get_recent = AsyncMock(return_value=[item])
        self.agent.etsy_api.get_listing_stats = AsyncMock(return_value={
            "views": 5, "clicks": 1, "favorites": 0,
            "num_orders": 0, "revenue_eur": 0.0,
        })
        self.agent.memory.get_etsy_listings = AsyncMock(return_value=[])
        db, _ = _make_db_mock()
        self.agent.memory.get_db = AsyncMock(return_value=db)
        self.agent.run_ladder_diagnostic_all = AsyncMock(return_value=[])

        # Must not raise
        await asyncio.wait_for(self.agent.poll_listing_performance(), timeout=5)

    async def test_update_niche_intelligence_exception_does_not_raise(self):
        item = _make_item(etsy_listing_id="L10")
        self.agent._production_queue.get_recent = AsyncMock(return_value=[item])
        self.agent.etsy_api.get_listing_stats = AsyncMock(return_value={
            "views": 20, "clicks": 3, "favorites": 1,
            "num_orders": 0, "revenue_eur": 0.0,
        })
        self.agent.memory.get_etsy_listings = AsyncMock(return_value=[])
        db, _ = _make_db_mock()
        self.agent.memory.get_db = AsyncMock(return_value=db)
        self.agent.run_ladder_diagnostic_all = AsyncMock(return_value=[])
        self.agent._learning_loop.update_niche_intelligence = AsyncMock(
            side_effect=RuntimeError("ll error")
        )

        # Exception caught internally — must not propagate
        await asyncio.wait_for(self.agent.poll_listing_performance(), timeout=5)

    async def test_multiple_items_all_processed(self):
        items = [_make_item(id=i, etsy_listing_id=f"L{i}") for i in range(3)]
        self.agent._production_queue.get_recent = AsyncMock(return_value=items)
        self.agent.etsy_api.get_listing_stats = AsyncMock(return_value={
            "views": 10, "clicks": 1, "favorites": 0,
            "num_orders": 0, "revenue_eur": 0.0,
        })
        self.agent.memory.get_etsy_listings = AsyncMock(return_value=[])
        db, _ = _make_db_mock()
        self.agent.memory.get_db = AsyncMock(return_value=db)
        self.agent.run_ladder_diagnostic_all = AsyncMock(return_value=[])

        await asyncio.wait_for(self.agent.poll_listing_performance(), timeout=5)

        # 3 INSERT calls — one per item
        assert db.execute.call_count == 3
        assert self.agent.etsy_api.get_listing_stats.call_count == 3
