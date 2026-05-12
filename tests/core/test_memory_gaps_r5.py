"""~40 pytest-asyncio tests covering uncovered lines in _reminders.py and _analytics.py.

Gaps targeted:
  _reminders.py  : reschedule_recurring (lines 142-198) — entire method
  _analytics.py  : lines 92-111, 157-158, 201-204, 219, 252-277, 300-302, 315-316, 350-352
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.backend.core._memory._analytics import AnalyticsMixin
from apps.backend.core._memory._reminders import RemindersMixin


# ---------------------------------------------------------------------------
# Mock helpers — same _DualCursorMock contract as test_memory_coverage.py
# ---------------------------------------------------------------------------

class _DualCursorMock:
    """Supports both `cursor = await db.execute(...)` and `async with db.execute(...) as cur:`."""

    def __init__(self, cursor: MagicMock) -> None:
        self._cursor = cursor

    def __await__(self):
        async def _c():
            return self._cursor
        return _c().__await__()

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *args):
        return False


def _make_cursor(fetchone=None, fetchall=None) -> MagicMock:
    c = MagicMock()
    c.fetchone = AsyncMock(return_value=fetchone)
    c.fetchall = AsyncMock(return_value=fetchall if fetchall is not None else [])
    c.lastrowid = 1
    return c


def _make_db(fetchone=None, fetchall=None):
    """Single fixed-cursor db mock (both await and async-with patterns)."""
    cur = _make_cursor(fetchone=fetchone, fetchall=fetchall)
    db = MagicMock()
    db.execute = MagicMock(side_effect=lambda *a, **kw: _DualCursorMock(cur))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db, cur


def _make_seq_db(*specs):
    """Sequenced db mock — each spec is a dict(fetchone=, fetchall=) or {"raise": True}."""
    db = MagicMock()
    db.commit = AsyncMock()
    idx = [0]

    def side_effect(*args, **kwargs):
        i = idx[0] % max(len(specs), 1)
        idx[0] += 1
        s = specs[i]
        if isinstance(s, dict) and s.get("raise"):
            raise Exception("simulated DB error")
        fn = s.get("fetchone") if isinstance(s, dict) else None
        fa = s.get("fetchall", []) if isinstance(s, dict) else []
        return _DualCursorMock(_make_cursor(fetchone=fn, fetchall=fa))

    db.execute = MagicMock(side_effect=side_effect)
    return db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class FakeReminders(RemindersMixin):
    pass


class FakeAnalytics(AnalyticsMixin):
    pass


# ---------------------------------------------------------------------------
# _reminders.py — reschedule_recurring (lines 142-198)
# ---------------------------------------------------------------------------

class TestRescheduleRecurring:

    async def test_row_none_no_update(self):
        """row=None → early return, no UPDATE, no commit."""
        obj = FakeReminders()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        assert db.execute.call_count == 1
        db.commit.assert_not_awaited()

    async def test_recurring_rule_none_no_update(self):
        """recurring_rule=None → early return, no UPDATE."""
        obj = FakeReminders()
        db, _ = _make_db(fetchone={"trigger_at": "2026-05-11T10:00:00", "recurring_rule": None})
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        assert db.execute.call_count == 1
        db.commit.assert_not_awaited()

    async def test_trigger_at_malformed_no_crash(self):
        """Malformed trigger_at → ValueError → return silently."""
        obj = FakeReminders()
        db, _ = _make_db(fetchone={"trigger_at": "not-a-date", "recurring_rule": "daily"})
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        assert db.execute.call_count == 1
        db.commit.assert_not_awaited()

    async def test_daily_increments_one_day(self):
        """rule='daily' → next_dt = current + 1 day."""
        obj = FakeReminders()
        db, _ = _make_db(fetchone={"trigger_at": "2026-05-11T10:00:00", "recurring_rule": "daily"})
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        assert db.execute.call_count == 2
        params = db.execute.call_args_list[1][0][1]
        assert params[0] == "2026-05-12T10:00:00"

    async def test_daily_update_correct_isoformat(self):
        """rule='daily', trigger='2026-05-10T10:00:00' → UPDATE param '2026-05-11T10:00:00'."""
        obj = FakeReminders()
        db, _ = _make_db(fetchone={"trigger_at": "2026-05-10T10:00:00", "recurring_rule": "daily"})
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(42), timeout=5)
        params = db.execute.call_args_list[1][0][1]
        assert params[0] == "2026-05-11T10:00:00"
        assert params[1] == 42

    async def test_daily_commit_called(self):
        """commit is awaited once after the UPDATE."""
        obj = FakeReminders()
        db, _ = _make_db(fetchone={"trigger_at": "2026-05-10T10:00:00", "recurring_rule": "daily"})
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        db.commit.assert_awaited_once()

    async def test_weekdays_from_monday_gives_tuesday(self):
        """rule='weekdays', current=Monday(0) → Tuesday(1)."""
        obj = FakeReminders()
        db, _ = _make_db(fetchone={"trigger_at": "2026-05-11T10:00:00", "recurring_rule": "weekdays"})
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        params = db.execute.call_args_list[1][0][1]
        assert params[0] == "2026-05-12T10:00:00"

    async def test_weekdays_from_friday_skips_weekend(self):
        """rule='weekdays', current=Friday(4) → skip Sat+Sun → Monday(0)."""
        obj = FakeReminders()
        db, _ = _make_db(fetchone={"trigger_at": "2026-05-08T10:00:00", "recurring_rule": "weekdays"})
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        params = db.execute.call_args_list[1][0][1]
        assert params[0] == "2026-05-11T10:00:00"

    async def test_weekdays_from_thursday_gives_friday(self):
        """rule='weekdays', current=Thursday(3) → Friday(4), no skip."""
        obj = FakeReminders()
        db, _ = _make_db(fetchone={"trigger_at": "2026-05-14T10:00:00", "recurring_rule": "weekdays"})
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        params = db.execute.call_args_list[1][0][1]
        assert params[0] == "2026-05-15T10:00:00"

    async def test_weekly_mon_wed_from_monday_gives_wednesday(self):
        """rule='weekly:MON,WED', current=Monday → next candidate = Wednesday."""
        obj = FakeReminders()
        db, _ = _make_db(
            fetchone={"trigger_at": "2026-05-11T10:00:00", "recurring_rule": "weekly:MON,WED"}
        )
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        params = db.execute.call_args_list[1][0][1]
        assert params[0] == "2026-05-13T10:00:00"

    async def test_weekly_fri_from_friday_gives_next_friday(self):
        """rule='weekly:FRI', current=Friday → next occurrence is next Friday."""
        obj = FakeReminders()
        db, _ = _make_db(
            fetchone={"trigger_at": "2026-05-08T10:00:00", "recurring_rule": "weekly:FRI"}
        )
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        params = db.execute.call_args_list[1][0][1]
        assert params[0] == "2026-05-15T10:00:00"

    async def test_weekly_mon_from_wednesday_gives_next_monday(self):
        """rule='weekly:MON', current=Wednesday → next Monday (5 days ahead)."""
        obj = FakeReminders()
        db, _ = _make_db(
            fetchone={"trigger_at": "2026-05-13T10:00:00", "recurring_rule": "weekly:MON"}
        )
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        params = db.execute.call_args_list[1][0][1]
        assert params[0] == "2026-05-18T10:00:00"

    async def test_weekly_tue_thu_from_tuesday_gives_thursday(self):
        """rule='weekly:TUE,THU', current=Tuesday → Thursday."""
        obj = FakeReminders()
        db, _ = _make_db(
            fetchone={"trigger_at": "2026-05-12T10:00:00", "recurring_rule": "weekly:TUE,THU"}
        )
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        params = db.execute.call_args_list[1][0][1]
        assert params[0] == "2026-05-14T10:00:00"

    async def test_weekly_empty_days_no_update(self):
        """rule='weekly:' (empty days) → target_days=[] → no UPDATE."""
        obj = FakeReminders()
        db, _ = _make_db(
            fetchone={"trigger_at": "2026-05-11T10:00:00", "recurring_rule": "weekly:"}
        )
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        assert db.execute.call_count == 1
        db.commit.assert_not_awaited()

    async def test_monthly_15_from_april_gives_may_15(self):
        """rule='monthly:15', current=Apr 15 → May 15."""
        obj = FakeReminders()
        db, _ = _make_db(
            fetchone={"trigger_at": "2026-04-15T10:00:00", "recurring_rule": "monthly:15"}
        )
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        params = db.execute.call_args_list[1][0][1]
        assert params[0] == "2026-05-15T10:00:00"

    async def test_monthly_31_from_january_gives_feb_28(self):
        """rule='monthly:31', current=Jan 31 → Feb 28 (2026 not leap)."""
        obj = FakeReminders()
        db, _ = _make_db(
            fetchone={"trigger_at": "2026-01-31T10:00:00", "recurring_rule": "monthly:31"}
        )
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        params = db.execute.call_args_list[1][0][1]
        assert params[0] == "2026-02-28T10:00:00"

    async def test_monthly_1_from_december_gives_jan_next_year(self):
        """rule='monthly:1', current=Dec 15 → Jan 1 next year."""
        obj = FakeReminders()
        db, _ = _make_db(
            fetchone={"trigger_at": "2026-12-15T10:00:00", "recurring_rule": "monthly:1"}
        )
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        params = db.execute.call_args_list[1][0][1]
        assert params[0] == "2027-01-01T10:00:00"

    async def test_monthly_bad_day_no_update(self):
        """rule='monthly:bad' → ValueError inside try → pass → no UPDATE."""
        obj = FakeReminders()
        db, _ = _make_db(
            fetchone={"trigger_at": "2026-05-11T10:00:00", "recurring_rule": "monthly:bad"}
        )
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        assert db.execute.call_count == 1
        db.commit.assert_not_awaited()

    async def test_unknown_rule_no_update(self):
        """Unrecognised rule → next_dt stays None → no UPDATE, no commit."""
        obj = FakeReminders()
        db, _ = _make_db(
            fetchone={"trigger_at": "2026-05-11T10:00:00", "recurring_rule": "unknown_rule"}
        )
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(1), timeout=5)
        assert db.execute.call_count == 1
        db.commit.assert_not_awaited()

    async def test_update_args_contain_reminder_id(self):
        """UPDATE second param must be the reminder_id passed to the method."""
        obj = FakeReminders()
        db, _ = _make_db(
            fetchone={"trigger_at": "2026-05-11T10:00:00", "recurring_rule": "daily"}
        )
        obj._db = db
        await asyncio.wait_for(obj.reschedule_recurring(99), timeout=5)
        params = db.execute.call_args_list[1][0][1]
        assert params[1] == 99


# ---------------------------------------------------------------------------
# _analytics.py — uncovered lines
# ---------------------------------------------------------------------------

# Helper: build the 8-call sequence for get_cost_breakdown
_EMPTY_CALLS = {"fetchall": []}
_ZERO_COST   = {"fetchone": {"total": 0.0}}

def _cost_breakdown_seq(cache_rows):
    """Returns a _make_seq_db configured for get_cost_breakdown.

    cache_rows: list of dicts for the 5th execute call (cache savings query).
    """
    return _make_seq_db(
        _EMPTY_CALLS,              # 1. per_agent
        _EMPTY_CALLS,              # 2. per_tool
        _EMPTY_CALLS,              # 3. per_day
        _EMPTY_CALLS,              # 4. tokens_per_day
        {"fetchall": cache_rows},  # 5. cache savings
        _ZERO_COST,                # 6. image_cost
        _ZERO_COST,                # 7. fee_cost
        _ZERO_COST,                # 8. pinterest_cost
    )


class TestAnalyticsMixinGaps:

    # ------------------------------------------------------------------
    # get_cost_breakdown — cache savings loop (lines 92-111)
    # ------------------------------------------------------------------

    async def test_get_cost_breakdown_haiku_model_savings(self):
        """Haiku model → uses LLM_HAIKU pricing, computes savings_usd correctly."""
        obj = FakeAnalytics()
        obj._db = _cost_breakdown_seq([
            {"model": "claude-haiku-3", "total_cache_read": 1_000_000,
             "total_cache_write": 0, "total_input": 0, "total_output": 0},
        ])
        result = await asyncio.wait_for(obj.get_cost_breakdown(period_days=7), timeout=5)
        from apps.backend.core.config import settings
        expected = round(1_000_000 * (settings.LLM_HAIKU_INPUT_PRICE - settings.LLM_HAIKU_CACHE_READ_PRICE) / 1_000_000, 6)
        assert result["cache"]["savings_usd"] == expected

    async def test_get_cost_breakdown_sonnet_model_savings(self):
        """Non-haiku model → uses LLM_SONNET pricing."""
        obj = FakeAnalytics()
        obj._db = _cost_breakdown_seq([
            {"model": "claude-sonnet-3-5", "total_cache_read": 1_000_000,
             "total_cache_write": 0, "total_input": 0, "total_output": 0},
        ])
        result = await asyncio.wait_for(obj.get_cost_breakdown(period_days=7), timeout=5)
        from apps.backend.core.config import settings
        expected = round(1_000_000 * (settings.LLM_SONNET_INPUT_PRICE - settings.LLM_SONNET_CACHE_READ_PRICE) / 1_000_000, 6)
        assert result["cache"]["savings_usd"] == expected

    async def test_get_cost_breakdown_efficiency_pct_positive(self):
        """denominator > 0 → efficiency_pct = cache_read / (cache_read + input) * 100."""
        obj = FakeAnalytics()
        obj._db = _cost_breakdown_seq([
            {"model": "sonnet", "total_cache_read": 1000,
             "total_cache_write": 0, "total_input": 1000, "total_output": 0},
        ])
        result = await asyncio.wait_for(obj.get_cost_breakdown(), timeout=5)
        assert result["cache"]["efficiency_pct"] == 50.0

    async def test_get_cost_breakdown_efficiency_pct_zero_no_tokens(self):
        """cache_read=0, input=0 → denominator=0 → efficiency_pct=0.0."""
        obj = FakeAnalytics()
        obj._db = _cost_breakdown_seq([
            {"model": "sonnet", "total_cache_read": 0,
             "total_cache_write": 0, "total_input": 0, "total_output": 0},
        ])
        result = await asyncio.wait_for(obj.get_cost_breakdown(), timeout=5)
        assert result["cache"]["efficiency_pct"] == 0.0

    async def test_get_cost_breakdown_total_output_accumulates(self):
        """total_output accumulates across multiple model rows."""
        obj = FakeAnalytics()
        obj._db = _cost_breakdown_seq([
            {"model": "haiku",  "total_cache_read": 0, "total_cache_write": 0,
             "total_input": 0, "total_output": 100},
            {"model": "sonnet", "total_cache_read": 0, "total_cache_write": 0,
             "total_input": 0, "total_output": 200},
        ])
        result = await asyncio.wait_for(obj.get_cost_breakdown(), timeout=5)
        assert result["tokens"]["output"] == 300

    async def test_get_cost_breakdown_pinterest_exception_gives_zero(self):
        """Pinterest query raises → pinterest_cost_today = 0.0 (lines 157-158)."""
        obj = FakeAnalytics()
        obj._db = _make_seq_db(
            _EMPTY_CALLS,    # 1. per_agent
            _EMPTY_CALLS,    # 2. per_tool
            _EMPTY_CALLS,    # 3. per_day
            _EMPTY_CALLS,    # 4. tokens_per_day
            _EMPTY_CALLS,    # 5. cache savings
            _ZERO_COST,      # 6. image_cost
            _ZERO_COST,      # 7. fee_cost
            {"raise": True}, # 8. pinterest_cost → exception
        )
        result = await asyncio.wait_for(obj.get_cost_breakdown(), timeout=5)
        assert result["pinterest_cost_today"] == 0.0

    # ------------------------------------------------------------------
    # get_chroma_stats (lines 252-277)
    # ------------------------------------------------------------------

    async def test_get_chroma_stats_no_collection(self):
        """_chroma_collection=None → {"available": False, "count": 0, "by_collection": {}}."""
        obj = FakeAnalytics()
        obj._chroma_collection = None
        result = await asyncio.wait_for(obj.get_chroma_stats(), timeout=5)
        assert result == {"available": False, "count": 0, "by_collection": {}}

    async def test_get_chroma_stats_with_main_collection_only(self):
        """Only pepe_memory set → count = asyncio.to_thread result; others = 0."""
        obj = FakeAnalytics()
        obj._chroma_collection = MagicMock()
        obj._screen_memory_collection = None
        obj._personal_memory_collection = None
        obj._shared_memory_collection = None
        with patch("asyncio.to_thread", AsyncMock(return_value=42)):
            result = await asyncio.wait_for(obj.get_chroma_stats(), timeout=5)
        assert result["available"] is True
        assert result["count"] == 42
        assert result["by_collection"]["pepe_memory"] == 42
        assert result["by_collection"]["screen_memory"] == 0
        assert result["by_collection"]["personal_memory"] == 0
        assert result["by_collection"]["shared_memory"] == 0

    async def test_get_chroma_stats_all_collections(self):
        """All 4 collections present → total = sum of all counts."""
        obj = FakeAnalytics()
        obj._chroma_collection = MagicMock()
        obj._screen_memory_collection = MagicMock()
        obj._personal_memory_collection = MagicMock()
        obj._shared_memory_collection = MagicMock()
        with patch("asyncio.to_thread", AsyncMock(return_value=10)):
            result = await asyncio.wait_for(obj.get_chroma_stats(), timeout=5)
        assert result["available"] is True
        assert result["count"] == 40
        assert all(v == 10 for v in result["by_collection"].values())

    async def test_get_chroma_stats_collection_count_raises(self):
        """asyncio.to_thread raises → by_collection[name] = 0 (inner except)."""
        obj = FakeAnalytics()
        obj._chroma_collection = MagicMock()
        obj._screen_memory_collection = None
        obj._personal_memory_collection = None
        obj._shared_memory_collection = None
        with patch("asyncio.to_thread", AsyncMock(side_effect=Exception("count failed"))):
            result = await asyncio.wait_for(obj.get_chroma_stats(), timeout=5)
        assert result["available"] is True
        assert result["by_collection"]["pepe_memory"] == 0
        assert result["count"] == 0

    # ------------------------------------------------------------------
    # log_memory_query — exception paths (lines 300-302, 315-316)
    # ------------------------------------------------------------------

    async def test_log_memory_query_db_exception_silent(self):
        """db.execute raises → warning logged, no crash, commit NOT called (line 300-302)."""
        obj = FakeAnalytics()
        db = MagicMock()
        db.execute = MagicMock(side_effect=Exception("db error"))
        db.commit = AsyncMock()
        obj._db = db
        obj._ws_broadcaster = None
        await asyncio.wait_for(
            obj.log_memory_query(["doc1"], "pepe_memory"),
            timeout=5,
        )
        db.commit.assert_not_awaited()

    async def test_log_memory_query_ws_broadcast_exception_silent(self):
        """ws_broadcaster raises → warning logged, no re-raise (lines 315-316)."""
        obj = FakeAnalytics()
        db, _ = _make_db()
        obj._db = db
        obj._ws_broadcaster = AsyncMock(side_effect=Exception("ws error"))
        await asyncio.wait_for(
            obj.log_memory_query(["doc1"], "pepe_memory"),
            timeout=5,
        )
        db.commit.assert_awaited_once()

    # ------------------------------------------------------------------
    # get_node_access_history — exception path + filter (lines 350-352)
    # ------------------------------------------------------------------

    async def test_get_node_access_history_exception_returns_empty(self):
        """db.execute raises → except → return [] (lines 350-352)."""
        obj = FakeAnalytics()
        db = MagicMock()
        db.execute = MagicMock(side_effect=Exception("db error"))
        obj._db = db
        result = await asyncio.wait_for(
            obj.get_node_access_history("doc1", "pepe_memory"),
            timeout=5,
        )
        assert result == []

    async def test_get_node_access_history_doc_not_in_parsed_ids_filtered(self):
        """Row whose doc_ids JSON doesn't contain doc_id → excluded from output."""
        obj = FakeAnalytics()
        row = {
            "agent": "a",
            "collection": "pepe_memory",
            "doc_ids": '["doc2", "doc3"]',
            "query_text": "cats",
            "queried_at": "2026-01-01",
        }
        db, _ = _make_db(fetchall=[row])
        obj._db = db
        result = await asyncio.wait_for(
            obj.get_node_access_history("doc1", "pepe_memory"),
            timeout=5,
        )
        assert result == []

    # ------------------------------------------------------------------
    # get_agent_logs_summary — per_day and per_agent loops (lines 201-204, 219)
    # ------------------------------------------------------------------

    async def test_get_agent_logs_summary_per_day_populated(self):
        """Non-empty per_day rows → dict keyed by day with status counts (lines 201-204)."""
        obj = FakeAnalytics()
        obj._db = _make_seq_db(
            {"fetchall": [{"status": "completed", "cnt": 5}]},       # by_status
            {"fetchall": [                                             # per_day
                {"day": "2026-05-11", "status": "completed", "cnt": 3},
                {"day": "2026-05-11", "status": "failed",    "cnt": 1},
                {"day": "2026-05-12", "status": "completed", "cnt": 2},
            ]},
            {"fetchall": []},                                          # per_agent
        )
        obj.get_production_queue_stats = AsyncMock(return_value={})
        result = await asyncio.wait_for(obj.get_agent_logs_summary(period_days=7), timeout=5)
        assert result["per_day"]["2026-05-11"]["completed"] == 3
        assert result["per_day"]["2026-05-11"]["failed"] == 1
        assert result["per_day"]["2026-05-12"]["completed"] == 2

    async def test_get_agent_logs_summary_per_agent_populated(self):
        """Non-empty per_agent rows → dict keyed by agent_name (line 219)."""
        obj = FakeAnalytics()
        obj._db = _make_seq_db(
            {"fetchall": [{"status": "completed", "cnt": 5}]},   # by_status
            {"fetchall": []},                                      # per_day
            {"fetchall": [                                         # per_agent
                {"agent_name": "planner", "total": 5, "completed": 5, "failed": 0, "cost": 1.5},
            ]},
        )
        obj.get_production_queue_stats = AsyncMock(return_value={})
        result = await asyncio.wait_for(obj.get_agent_logs_summary(period_days=7), timeout=5)
        assert "planner" in result["per_agent"]
        assert result["per_agent"]["planner"]["total"] == 5
        assert result["per_agent"]["planner"]["cost"] == 1.5

    # ------------------------------------------------------------------
    # get_domain_agent_stats — unknown status branch
    # ------------------------------------------------------------------

    async def test_get_domain_agent_stats_unknown_status_mapped_to_running(self):
        """Status not in {completed, failed, running} → key defaults to 'running'."""
        obj = FakeAnalytics()
        rows = [{"agent_name": "worker", "status": "cancelled", "cnt": 2}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(
            obj.get_domain_agent_stats(domain="personal"),
            timeout=5,
        )
        assert result["worker"]["running"] == 2

    async def test_get_domain_agent_stats_multiple_agents(self):
        """Multiple agents in result → each gets its own entry initialised to 0."""
        obj = FakeAnalytics()
        rows = [
            {"agent_name": "agent_a", "status": "completed", "cnt": 3},
            {"agent_name": "agent_b", "status": "failed",    "cnt": 1},
        ]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(
            obj.get_domain_agent_stats(domain="etsy"),
            timeout=5,
        )
        assert result["agent_a"]["completed"] == 3
        assert result["agent_a"]["failed"] == 0
        assert result["agent_b"]["failed"] == 1
        assert result["agent_b"]["completed"] == 0
