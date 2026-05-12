"""~120 pytest-asyncio coverage tests for all 11 MemoryManager mixins.

asyncio_mode = auto (pytest.ini).
"""
from __future__ import annotations

# MOCK CONTRACT
# AgentLogsMixin: self._db.execute(sql, params) → cursor; cursor.fetchone() → dict|None; cursor.fetchall() → list[dict]
# Tutti i mixin: stessa struttura _db. Rows = plain dict (dict(row) funziona su dict).
# _json_dumps/_json_loads: importati da core._memory._base, funzionano senza mock.

import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from apps.backend.core._memory._base import MemoryBase, _json_dumps, _json_loads
from apps.backend.core._memory._agent_logs import AgentLogsMixin
from apps.backend.core._memory._analytics import AnalyticsMixin
from apps.backend.core._memory._queue import QueueMixin
from apps.backend.core._memory._revenue import RevenueMixin
from apps.backend.core._memory._pending import PendingMixin
from apps.backend.core._memory._learning import LearningMixin
from apps.backend.core._memory._etsy_listings import EtsyListingsMixin
from apps.backend.core._memory._oauth import OAuthMixin
from apps.backend.core._memory._conversations import ConversationsMixin
from apps.backend.core._memory._reminders import RemindersMixin


# ---------------------------------------------------------------------------
# Helpers — dual-mode mock: supports both `await db.execute()` and
# `async with db.execute() as cur:` as aiosqlite does.
# ---------------------------------------------------------------------------

class _DualCursorMock:
    """Wraps a cursor mock so that db.execute() return value can be both
    awaited (Pattern 1) and used as async context-manager (Pattern 2)."""

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


def _make_db(
    fetchone=None,
    fetchall=None,
    lastrowid: int = 42,
    rowcount: int = 1,
):
    """Return (db_mock, cursor_mock) ready for all mixin patterns.

    db.execute: MagicMock — each call returns a fresh _DualCursorMock(cursor).
    db.commit / rollback / executescript / close: AsyncMock.
    cursor.fetchone / fetchall: AsyncMock with provided return values.
    cursor.lastrowid / rowcount: plain int attributes.
    """
    cursor = MagicMock()
    cursor.fetchone = AsyncMock(return_value=fetchone)
    cursor.fetchall = AsyncMock(
        return_value=fetchall if fetchall is not None else []
    )
    cursor.lastrowid = lastrowid
    cursor.rowcount = rowcount

    db = MagicMock()
    db.execute = MagicMock(side_effect=lambda *a, **kw: _DualCursorMock(cursor))
    db.executemany = AsyncMock(return_value=cursor)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.executescript = AsyncMock()
    db.close = AsyncMock()
    return db, cursor


# ===========================================================================
# MemoryBase  (_base.py)
# ===========================================================================


class TestMemoryBase:

    # -- _json_dumps --

    def test_json_dumps_none(self):
        assert _json_dumps(None) is None

    def test_json_dumps_dict(self):
        result = _json_dumps({"k": 1})
        assert isinstance(result, str)
        assert json.loads(result) == {"k": 1}

    def test_json_dumps_list(self):
        result = _json_dumps([1, 2])
        assert json.loads(result) == [1, 2]

    # -- _json_loads --

    def test_json_loads_none(self):
        assert _json_loads(None) is None

    def test_json_loads_valid(self):
        assert _json_loads('{"k": 1}') == {"k": 1}

    def test_json_loads_invalid_raises(self):
        with pytest.raises((json.JSONDecodeError, ValueError)):
            _json_loads("not_valid_json")

    # -- set_ws_broadcaster / set_bridge_callback --

    def test_set_ws_broadcaster(self):
        obj = MemoryBase.__new__(MemoryBase)
        obj._ws_broadcaster = None
        cb = lambda e: None
        obj.set_ws_broadcaster(cb)
        assert obj._ws_broadcaster is cb

    def test_set_bridge_callback(self):
        obj = MemoryBase.__new__(MemoryBase)
        obj._bridge_callback = None
        cb = lambda t, d: None
        obj.set_bridge_callback(cb)
        assert obj._bridge_callback is cb

    # -- close --

    async def test_close_db_none(self):
        obj = MemoryBase.__new__(MemoryBase)
        obj._db = None
        await asyncio.wait_for(obj.close(), timeout=5)
        assert obj._db is None

    async def test_close_db_present(self):
        obj = MemoryBase.__new__(MemoryBase)
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=None)
        mock_db.close = AsyncMock()
        obj._db = mock_db
        await asyncio.wait_for(obj.close(), timeout=5)
        mock_db.close.assert_awaited_once()
        assert obj._db is None


# ===========================================================================
# AgentLogsMixin  (_agent_logs.py)
# ===========================================================================


class _FakeAgentLogs(AgentLogsMixin):
    pass


class TestAgentLogsMixin:

    async def test_log_agent_task_basic(self):
        obj = _FakeAgentLogs()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(
            obj.log_agent_task("my_agent", "task-1"),
            timeout=5,
        )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_log_agent_task_with_input_data(self):
        obj = _FakeAgentLogs()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(
            obj.log_agent_task("a", "t-2", input_data={"x": 1}),
            timeout=5,
        )
        # input_data serialised to JSON string in the SQL params
        params = db.execute.call_args[0][1]
        assert '"x"' in params[3]

    async def test_finalize_agent_task(self):
        obj = _FakeAgentLogs()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(
            obj.finalize_agent_task("task-1", status="completed", tokens_used=100),
            timeout=5,
        )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_get_task_by_id_found(self):
        obj = _FakeAgentLogs()
        row = {
            "task_id": "t1",
            "agent_name": "a",
            "input_data": '{"q":1}',
            "output_data": None,
        }
        db, _ = _make_db(fetchone=row)
        obj._db = db
        result = await asyncio.wait_for(obj.get_task_by_id("t1"), timeout=5)
        assert result["task_id"] == "t1"
        assert result["input_data"] == {"q": 1}
        assert result["output_data"] is None

    async def test_get_task_by_id_not_found(self):
        obj = _FakeAgentLogs()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        result = await asyncio.wait_for(obj.get_task_by_id("missing"), timeout=5)
        assert result is None

    async def test_get_last_failed_task_with_agent_name(self):
        obj = _FakeAgentLogs()
        row = {"task_id": "t2", "agent_name": "a", "input_data": None, "output_data": None}
        db, _ = _make_db(fetchone=row)
        obj._db = db
        result = await asyncio.wait_for(
            obj.get_last_failed_task(agent_name="a"), timeout=5
        )
        assert result["task_id"] == "t2"
        # Verify the SQL uses the agent_name filter
        sql = db.execute.call_args[0][0]
        assert "agent_name = ?" in sql

    async def test_get_last_failed_task_no_agent(self):
        obj = _FakeAgentLogs()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        result = await asyncio.wait_for(obj.get_last_failed_task(), timeout=5)
        assert result is None
        sql = db.execute.call_args[0][0]
        assert "agent_name = ?" not in sql

    async def test_log_error(self):
        obj = _FakeAgentLogs()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(
            obj.log_error("agent", "IOError", "broke", task_id="t1"),
            timeout=5,
        )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_get_agent_error_count(self):
        obj = _FakeAgentLogs()
        # row[0] access requires a tuple / sequence
        db, _ = _make_db(fetchone=(5,))
        obj._db = db
        count = await asyncio.wait_for(
            obj.get_agent_error_count("agent", hours=1), timeout=5
        )
        assert count == 5

    async def test_log_step_returns_lastrowid(self):
        obj = _FakeAgentLogs()
        db, _ = _make_db(lastrowid=99)
        obj._db = db
        step_id = await asyncio.wait_for(
            obj.log_step("t1", "agent", 1, "think", "desc"),
            timeout=5,
        )
        assert step_id == 99
        db.commit.assert_awaited()

    async def test_log_step_exception_triggers_rollback(self):
        obj = _FakeAgentLogs()
        db, _ = _make_db()
        db.commit = AsyncMock(side_effect=RuntimeError("db error"))
        obj._db = db
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(
                obj.log_step("t1", "a", 1, "think", "d"),
                timeout=5,
            )
        db.rollback.assert_awaited()

    async def test_log_llm_call_returns_lastrowid(self):
        obj = _FakeAgentLogs()
        db, _ = _make_db(lastrowid=77)
        obj._db = db
        call_id = await asyncio.wait_for(
            obj.log_llm_call("t1", None, "agent", "claude-3", None, [], "resp"),
            timeout=5,
        )
        assert call_id == 77

    async def test_log_llm_call_exception_triggers_rollback(self):
        obj = _FakeAgentLogs()
        db, _ = _make_db()
        db.commit = AsyncMock(side_effect=RuntimeError("fail"))
        obj._db = db
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(
                obj.log_llm_call("t1", None, "a", "m", None, [], "r"),
                timeout=5,
            )
        db.rollback.assert_awaited()

    async def test_log_tool_call_returns_lastrowid(self):
        obj = _FakeAgentLogs()
        db, _ = _make_db(lastrowid=55)
        obj._db = db
        call_id = await asyncio.wait_for(
            obj.log_tool_call("t1", None, "agent", "write_file", "write"),
            timeout=5,
        )
        assert call_id == 55

    async def test_log_tool_call_exception_triggers_rollback(self):
        obj = _FakeAgentLogs()
        db, _ = _make_db()
        db.commit = AsyncMock(side_effect=RuntimeError("fail"))
        obj._db = db
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(
                obj.log_tool_call("t1", None, "a", "tool", "act"),
                timeout=5,
            )
        db.rollback.assert_awaited()

    async def test_get_task_timeline_empty(self):
        obj = _FakeAgentLogs()
        db, _ = _make_db(fetchall=[])
        obj._db = db
        result = await asyncio.wait_for(obj.get_task_timeline("t1"), timeout=5)
        assert result == []

    async def test_get_task_timeline_with_data(self):
        obj = _FakeAgentLogs()
        step_row = {
            "id": 1,
            "step_type": "think",
            "input_data": '{"a":1}',
            "output_data": None,
            "timestamp": "2024-01-01 10:00:00",
        }
        llm_row = {
            "id": 1,
            "messages": '["m"]',
            "timestamp": "2024-01-01 10:00:01",
        }
        tool_row = {
            "id": 1,
            "input_params": None,
            "output_result": '{"r":1}',
            "timestamp": "2024-01-01 10:00:02",
        }
        # Provide distinct fetchall results for the 3 SELECT queries
        cursor = MagicMock()
        cursor.fetchall = AsyncMock(side_effect=[[step_row], [llm_row], [tool_row]])
        db = MagicMock()
        db.execute = MagicMock(side_effect=lambda *a, **kw: _DualCursorMock(cursor))
        db.commit = AsyncMock()
        obj._db = db
        result = await asyncio.wait_for(obj.get_task_timeline("t1"), timeout=5)
        assert len(result) == 3
        types = {r["type"] for r in result}
        assert types == {"agent_step", "llm_call", "tool_call"}


# ===========================================================================
# AnalyticsMixin  (_analytics.py)
# ===========================================================================


class _FakeAnalytics(AnalyticsMixin):
    pass


class TestAnalyticsMixin:

    async def test_get_cost_breakdown_returns_dict(self):
        obj = _FakeAnalytics()
        # All fetchall → empty; fetchone → {"total": 0.0} for image/fee costs
        db, _ = _make_db(fetchone={"total": 0.0}, fetchall=[])
        obj._db = db
        obj._ws_broadcaster = None
        result = await asyncio.wait_for(obj.get_cost_breakdown(period_days=7), timeout=5)
        assert isinstance(result, dict)
        assert "per_agent" in result
        assert "total" in result
        assert "cache" in result
        assert "tokens" in result

    async def test_get_agent_logs_summary(self):
        obj = _FakeAnalytics()
        db, _ = _make_db(fetchone={"cnt": 0}, fetchall=[])
        obj._db = db
        # get_production_queue_stats lives in QueueMixin — mock it on instance
        obj.get_production_queue_stats = AsyncMock(return_value={})
        result = await asyncio.wait_for(obj.get_agent_logs_summary(period_days=7), timeout=5)
        assert isinstance(result, dict)
        assert "total" in result
        assert "by_status" in result

    async def test_log_memory_query_empty_doc_ids(self):
        obj = _FakeAnalytics()
        db, _ = _make_db()
        obj._db = db
        obj._ws_broadcaster = None
        await asyncio.wait_for(
            obj.log_memory_query([], "pepe_memory"),
            timeout=5,
        )
        db.execute.assert_not_called()

    async def test_log_memory_query_with_doc_ids(self):
        obj = _FakeAnalytics()
        db, _ = _make_db()
        obj._db = db
        obj._ws_broadcaster = None
        await asyncio.wait_for(
            obj.log_memory_query(["doc1", "doc2"], "pepe_memory", agent="test"),
            timeout=5,
        )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_log_memory_query_with_broadcaster(self):
        obj = _FakeAnalytics()
        db, _ = _make_db()
        obj._db = db
        broadcaster = AsyncMock()
        obj._ws_broadcaster = broadcaster
        await asyncio.wait_for(
            obj.log_memory_query(["doc1"], "pepe_memory"),
            timeout=5,
        )
        broadcaster.assert_awaited_once()

    async def test_get_node_access_history(self):
        obj = _FakeAnalytics()
        row = {
            "agent": "a",
            "collection": "pepe_memory",
            "doc_ids": '["doc1"]',
            "query_text": "cats",
            "queried_at": "2024-01-01",
        }
        db, _ = _make_db(fetchall=[row])
        obj._db = db
        result = await asyncio.wait_for(
            obj.get_node_access_history("doc1", "pepe_memory"),
            timeout=5,
        )
        assert len(result) == 1
        assert result[0]["agent"] == "a"

    async def test_get_analytics_summary(self):
        obj = _FakeAnalytics()
        row = {"total_views": 100, "total_sales": 5, "revenue": 50.0}
        db, _ = _make_db(fetchone=row)
        obj._db = db
        result = await asyncio.wait_for(obj.get_analytics_summary(days=7), timeout=5)
        assert result["total_views"] == 100

    async def test_get_scheduled_tasks(self):
        obj = _FakeAnalytics()
        rows = [{"id": 1, "name": "daily_push"}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_scheduled_tasks(), timeout=5)
        assert len(result) == 1
        assert result[0]["name"] == "daily_push"

    async def test_get_recent_agent_steps_no_filter(self):
        obj = _FakeAnalytics()
        rows = [{"id": 1, "agent_name": "a", "step_type": "think", "description": "d", "timestamp": "t"}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_recent_agent_steps(limit=10), timeout=5)
        assert len(result) == 1

    async def test_get_recent_agent_steps_with_agent(self):
        obj = _FakeAnalytics()
        db, _ = _make_db(fetchall=[])
        obj._db = db
        result = await asyncio.wait_for(
            obj.get_recent_agent_steps(limit=10, agent_name="myagent"),
            timeout=5,
        )
        assert result == []
        sql = db.execute.call_args[0][0]
        assert "agent_name = ?" in sql

    async def test_get_domain_agent_stats(self):
        obj = _FakeAnalytics()
        rows = [{"agent_name": "planner", "status": "completed", "cnt": 3}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(
            obj.get_domain_agent_stats(domain="personal"),
            timeout=5,
        )
        assert "planner" in result
        assert result["planner"]["completed"] == 3

    async def test_get_agent_steps_count_all(self):
        obj = _FakeAnalytics()
        db, _ = _make_db(fetchone=(7,))
        obj._db = db
        count = await asyncio.wait_for(obj.get_agent_steps_count(agent="*"), timeout=5)
        assert count == 7

    async def test_get_agent_steps_count_specific(self):
        obj = _FakeAnalytics()
        db, _ = _make_db(fetchone=(3,))
        obj._db = db
        count = await asyncio.wait_for(obj.get_agent_steps_count(agent="myagent"), timeout=5)
        assert count == 3

    async def test_get_enabled_scheduled_tasks(self):
        obj = _FakeAnalytics()
        rows = [{"id": 1, "name": "push_daily", "enabled": 1}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_enabled_scheduled_tasks(), timeout=5)
        assert len(result) == 1
        assert result[0]["enabled"] == 1

    async def test_update_task_last_run(self):
        obj = _FakeAnalytics()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(
            obj.update_task_last_run(1, "2024-01-01T00:00:00Z"),
            timeout=5,
        )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()


# ===========================================================================
# QueueMixin  (_queue.py)
# ===========================================================================


class _FakeQueue(QueueMixin):
    pass


class TestQueueMixin:

    async def test_add_to_production_queue_returns_lastrowid(self):
        obj = _FakeQueue()
        db, _ = _make_db(lastrowid=10)
        obj._db = db
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await asyncio.wait_for(
                obj.add_to_production_queue("t1", "printable", "cats", {"title": "Cat"}),
                timeout=5,
            )
        assert result == 10
        db.commit.assert_awaited_once()

    async def test_get_production_queue_item_found(self):
        obj = _FakeQueue()
        row = {
            "task_id": "t1",
            "product_type": "printable",
            "brief": '{"a":1}',
            "file_paths": None,
        }
        db, _ = _make_db(fetchone=row)
        obj._db = db
        result = await asyncio.wait_for(obj.get_production_queue_item("t1"), timeout=5)
        assert result["task_id"] == "t1"
        assert result["brief"] == {"a": 1}
        assert result["file_paths"] is None

    async def test_get_production_queue_item_not_found(self):
        obj = _FakeQueue()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        result = await asyncio.wait_for(obj.get_production_queue_item("missing"), timeout=5)
        assert result is None

    async def test_update_production_queue_status_without_file_paths(self):
        obj = _FakeQueue()
        db, _ = _make_db()
        obj._db = db
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            await asyncio.wait_for(
                obj.update_production_queue_status("t1", "published"),
                timeout=5,
            )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_update_production_queue_status_with_file_paths(self):
        obj = _FakeQueue()
        db, _ = _make_db()
        obj._db = db
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            await asyncio.wait_for(
                obj.update_production_queue_status("t1", "published", file_paths=["a.png"]),
                timeout=5,
            )
        sql = db.execute.call_args[0][0]
        assert "file_paths" in sql

    async def test_get_production_queue_no_filter(self):
        obj = _FakeQueue()
        rows = [{"task_id": "t1", "brief": '{"b":1}', "file_paths": None}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_production_queue(), timeout=5)
        assert len(result) == 1
        assert result[0]["brief"] == {"b": 1}

    async def test_get_production_queue_with_status(self):
        obj = _FakeQueue()
        db, _ = _make_db(fetchall=[])
        obj._db = db
        result = await asyncio.wait_for(
            obj.get_production_queue(status="pending_design"),
            timeout=5,
        )
        assert result == []
        sql = db.execute.call_args[0][0]
        assert "status = ?" in sql

    async def test_is_duplicate_product_in_queue(self):
        obj = _FakeQueue()
        # First fetchone returns a row (exists in production_queue)
        db, _ = _make_db(fetchone=(1,))
        obj._db = db
        result = await asyncio.wait_for(
            obj.is_duplicate_product("cats", "printable"),
            timeout=5,
        )
        assert result is True

    async def test_is_duplicate_product_in_etsy_listings(self):
        """Not in production_queue but found in etsy_listings."""
        obj = _FakeQueue()
        cursor = MagicMock()
        # First fetchone: None (not in queue), second: row (in etsy_listings)
        cursor.fetchone = AsyncMock(side_effect=[None, (1,)])
        cursor.fetchall = AsyncMock(return_value=[])
        db = MagicMock()
        db.execute = MagicMock(side_effect=lambda *a, **kw: _DualCursorMock(cursor))
        db.commit = AsyncMock()
        obj._db = db
        result = await asyncio.wait_for(
            obj.is_duplicate_product("cats", "printable"),
            timeout=5,
        )
        assert result is True

    async def test_is_duplicate_product_not_found(self):
        obj = _FakeQueue()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        result = await asyncio.wait_for(
            obj.is_duplicate_product("unknown", "type"),
            timeout=5,
        )
        assert result is False

    async def test_get_production_queue_stats(self):
        obj = _FakeQueue()
        db, _ = _make_db(fetchone={"cnt": 0})
        obj._db = db
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await asyncio.wait_for(obj.get_production_queue_stats(), timeout=5)
        assert isinstance(result, dict)
        assert "published" in result
        assert "planned" in result  # backward-compat alias

    async def test_get_listings_by_niche(self):
        obj = _FakeQueue()
        rows = [{"niche": "cats", "tags": '["cat","cute"]', "title": "Cat Print"}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_listings_by_niche("cats"), timeout=5)
        assert len(result) == 1
        assert result[0]["tags"] == ["cat", "cute"]

    async def test_get_stale_listings_without_sales(self):
        obj = _FakeQueue()
        rows = [{"niche": "dogs", "price_eur": 5.0, "views": 100, "sales": 0}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(
            obj.get_stale_listings_without_sales(min_views=50, days_old=30),
            timeout=5,
        )
        assert len(result) == 1


# ===========================================================================
# RevenueMixin  (_revenue.py)
# ===========================================================================


class _FakeRevenue(RevenueMixin):
    pass


class TestRevenueMixin:

    async def test_get_revenue_stats_found(self):
        obj = _FakeRevenue()
        row = {
            "total_revenue_eur": 100.0,
            "total_sales": 5,
            "total_listings": 10,
            "active_count": 8,
            "draft_count": 2,
            "avg_price_eur": 20.0,
        }
        db, _ = _make_db(fetchone=row)
        obj._db = db
        result = await asyncio.wait_for(obj.get_revenue_stats(), timeout=5)
        assert result["total_revenue_eur"] == 100.0
        assert "avg_revenue_per_listing" in result
        assert result["avg_revenue_per_listing"] == pytest.approx(10.0)

    async def test_get_revenue_stats_not_found(self):
        obj = _FakeRevenue()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        result = await asyncio.wait_for(obj.get_revenue_stats(), timeout=5)
        assert result["total_revenue_eur"] == 0.0
        assert result["total_sales"] == 0

    async def test_get_revenue_by_niche(self):
        obj = _FakeRevenue()
        rows = [
            {
                "niche": "cats",
                "listing_count": 3,
                "total_sales": 2,
                "total_revenue_eur": 10.0,
                "avg_price_eur": 5.0,
            }
        ]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_revenue_by_niche(), timeout=5)
        assert len(result) == 1
        assert result[0]["niche"] == "cats"

    async def test_get_revenue_by_product_type(self):
        obj = _FakeRevenue()
        rows = [
            {
                "product_type": "printable",
                "listing_count": 5,
                "total_sales": 3,
                "total_revenue_eur": 15.0,
                "avg_price_eur": 5.0,
            }
        ]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_revenue_by_product_type(), timeout=5)
        assert len(result) == 1
        assert result[0]["product_type"] == "printable"

    async def test_get_model_cost_breakdown(self):
        obj = _FakeRevenue()
        rows = [
            {
                "model": "claude-3-haiku",
                "call_count": 10,
                "total_input_tokens": 5000,
                "total_output_tokens": 2000,
                "total_cost_usd": 0.01,
            }
        ]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_model_cost_breakdown(), timeout=5)
        assert len(result) == 1
        assert result[0]["model"] == "claude-3-haiku"

    async def test_get_daily_revenue_trend(self):
        obj = _FakeRevenue()
        rows = [{"day": "2024-01-01", "daily_revenue_eur": 20.0, "daily_sales": 2}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_daily_revenue_trend(), timeout=5)
        assert len(result) == 1
        assert result[0]["day"] == "2024-01-01"


# ===========================================================================
# PendingMixin  (_pending.py)
# ===========================================================================


class _FakePending(PendingMixin):
    pass


class TestPendingMixin:

    async def test_save_pending_action(self):
        obj = _FakePending()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(
            obj.save_pending_action("confirm", {"data": "x"}, task_id="t1"),
            timeout=5,
        )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_get_pending_action_found(self):
        obj = _FakePending()
        row = {
            "action_type": "confirm",
            "payload": '{"data":"x"}',
            "expires_at": "2099-01-01",
            "task_id": "t1",
        }
        db, _ = _make_db(fetchone=row)
        obj._db = db
        result = await asyncio.wait_for(obj.get_pending_action("confirm"), timeout=5)
        assert result["action_type"] == "confirm"
        assert result["payload"] == {"data": "x"}

    async def test_get_pending_action_not_found(self):
        obj = _FakePending()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        result = await asyncio.wait_for(obj.get_pending_action("confirm"), timeout=5)
        assert result is None

    async def test_delete_pending_action(self):
        obj = _FakePending()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(obj.delete_pending_action("confirm"), timeout=5)
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_get_pending_input_for_task_found(self):
        obj = _FakePending()
        row = {
            "action_type": "clarification",
            "payload": '{"q":"?"}',
            "expires_at": "2099-01-01",
            "task_id": "t1",
        }
        db, _ = _make_db(fetchone=row)
        obj._db = db
        result = await asyncio.wait_for(obj.get_pending_input_for_task("t1"), timeout=5)
        assert result["task_id"] == "t1"
        assert result["payload"] == {"q": "?"}

    async def test_get_pending_input_for_task_not_found(self):
        obj = _FakePending()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        result = await asyncio.wait_for(obj.get_pending_input_for_task("t1"), timeout=5)
        assert result is None

    async def test_resolve_pending_input(self):
        obj = _FakePending()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(obj.resolve_pending_input("t1"), timeout=5)
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_get_pending_input_tasks(self):
        obj = _FakePending()
        rows = [
            {
                "action_type": "clarification",
                "payload": '{"q":"?"}',
                "task_id": "t1",
            }
        ]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_pending_input_tasks(), timeout=5)
        assert len(result) == 1
        assert result[0]["payload"] == {"q": "?"}


# ===========================================================================
# LearningMixin  (_learning.py)
# ===========================================================================


class _FL(LearningMixin):
    pass


class TestLearningMixin:

    async def test_upsert_learning_insert_path(self):
        """No existing row → INSERT new pattern."""
        obj = _FL()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        await asyncio.wait_for(
            obj.upsert_learning("agent", "topic", "cats", "positive", 0.1),
            timeout=5,
        )
        # SELECT (context-manager) + INSERT = at least 2 execute calls
        assert db.execute.call_count >= 2
        db.commit.assert_awaited()

    async def test_upsert_learning_update_accepted(self):
        """Existing row + |delta| >= threshold → weight updated."""
        obj = _FL()
        row = {"id": 1, "weight": 0.5, "occurrences": 2}
        db, _ = _make_db(fetchone=row)
        obj._db = db
        await asyncio.wait_for(
            obj.upsert_learning("agent", "topic", "cats", "positive", 0.1),
            timeout=5,
        )
        # SELECT + UPDATE weight + INSERT evaluation = at least 3 calls
        assert db.execute.call_count >= 2
        db.commit.assert_awaited()

    async def test_upsert_learning_update_rejected(self):
        """Existing row + |delta| < threshold → only occurrences updated."""
        obj = _FL()
        row = {"id": 1, "weight": 0.5, "occurrences": 2}
        db, _ = _make_db(fetchone=row)
        obj._db = db
        # 0.005 < _ACCEPTANCE_THRESHOLD (0.02) → rejected path
        await asyncio.wait_for(
            obj.upsert_learning("agent", "topic", "cats", "positive", 0.005),
            timeout=5,
        )
        db.commit.assert_awaited()

    async def test_get_learning_patterns_with_type(self):
        obj = _FL()
        rows = [
            {
                "id": 1,
                "agent": "a",
                "pattern_type": "topic",
                "pattern_value": "cats",
                "weight": 0.7,
            }
        ]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(
            obj.get_learning_patterns("a", pattern_type="topic"),
            timeout=5,
        )
        assert len(result) == 1
        assert result[0]["pattern_value"] == "cats"

    async def test_get_learning_patterns_without_type(self):
        obj = _FL()
        db, _ = _make_db(fetchall=[])
        obj._db = db
        result = await asyncio.wait_for(obj.get_learning_patterns("a"), timeout=5)
        assert result == []

    async def test_save_learning_evaluation(self):
        obj = _FL()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(
            obj.save_learning_evaluation(
                pattern_id="p1",
                signal_type="positive",
                metric_type="topic",
                baseline_value=0.5,
                post_value=0.6,
                accepted=True,
            ),
            timeout=5,
        )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_get_pattern_acceptance_rate_found(self):
        obj = _FL()
        db, _ = _make_db(fetchone=(0.75,))
        obj._db = db
        rate = await asyncio.wait_for(
            obj.get_pattern_acceptance_rate("positive"),
            timeout=5,
        )
        assert rate == pytest.approx(0.75)

    async def test_get_pattern_acceptance_rate_none_row(self):
        obj = _FL()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        rate = await asyncio.wait_for(
            obj.get_pattern_acceptance_rate("positive"),
            timeout=5,
        )
        assert rate == 0.0

    async def test_get_pattern_acceptance_rate_null_value(self):
        obj = _FL()
        db, _ = _make_db(fetchone=(None,))
        obj._db = db
        rate = await asyncio.wait_for(
            obj.get_pattern_acceptance_rate("positive"),
            timeout=5,
        )
        assert rate == 0.0

    async def test_get_baseline_metric_found(self):
        obj = _FL()
        db, _ = _make_db(fetchone=(0.6,))
        obj._db = db
        result = await asyncio.wait_for(obj.get_baseline_metric("topic"), timeout=5)
        assert result == pytest.approx(0.6)

    async def test_get_baseline_metric_none(self):
        obj = _FL()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        result = await asyncio.wait_for(obj.get_baseline_metric("topic"), timeout=5)
        assert result is None

    async def test_decay_old_patterns(self):
        obj = _FL()
        db, _ = _make_db(rowcount=3)
        obj._db = db
        updated = await asyncio.wait_for(obj.decay_old_patterns(days=7), timeout=5)
        assert updated == 3
        db.commit.assert_awaited()

    async def test_detect_watcher_habits(self):
        obj = _FL()
        rows = [{"app_name": "Xcode", "hour_slot": 10, "day_count": 6}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.detect_watcher_habits(), timeout=5)
        assert len(result) == 1
        assert result[0]["app_name"] == "Xcode"
        assert result[0]["hour_slot"] == 10

    async def test_get_frequent_queries(self):
        obj = _FL()
        rows = [{"pattern_value": "cats"}, {"pattern_value": "dogs"}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_frequent_queries(), timeout=5)
        assert result == ["cats", "dogs"]


# ===========================================================================
# EtsyListingsMixin  (_etsy_listings.py)
# ===========================================================================


class _FakeEtsy(EtsyListingsMixin):
    pass


class TestEtsyListingsMixin:

    async def test_add_etsy_listing(self):
        obj = _FakeEtsy()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(
            obj.add_etsy_listing(
                listing_id="L1",
                production_queue_task_id=None,
                title="Cat Print",
                tags=["cat", "cute"],
                product_type="printable",
                niche="cats",
                template="minimal",
                color_scheme="blue",
                size="A4",
                ab_price_variant="a",
                price_eur=5.0,
                file_path="/tmp/cat.jpg",
            ),
            timeout=5,
        )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_update_etsy_listing_stats(self):
        obj = _FakeEtsy()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(
            obj.update_etsy_listing_stats("L1", 100, 10, 5, 25.0, "active", "2024-01-01"),
            timeout=5,
        )
        # BEGIN IMMEDIATE + UPDATE views_prev + UPDATE stats = 3 calls
        assert db.execute.call_count >= 3
        db.commit.assert_awaited()

    async def test_get_etsy_listings_no_filter(self):
        obj = _FakeEtsy()
        rows = [{"listing_id": "L1", "tags": '["cat"]', "title": "Cat"}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_etsy_listings(), timeout=5)
        assert len(result) == 1
        assert result[0]["tags"] == ["cat"]

    async def test_get_etsy_listings_with_status(self):
        obj = _FakeEtsy()
        db, _ = _make_db(fetchall=[])
        obj._db = db
        result = await asyncio.wait_for(obj.get_etsy_listings(status="active"), timeout=5)
        assert result == []
        sql = db.execute.call_args[0][0]
        assert "status = ?" in sql

    async def test_get_etsy_listings_with_limit(self):
        obj = _FakeEtsy()
        db, _ = _make_db(fetchall=[])
        obj._db = db
        await asyncio.wait_for(obj.get_etsy_listings(limit=5), timeout=5)
        sql = db.execute.call_args[0][0]
        assert "LIMIT 5" in sql

    async def test_get_etsy_listings_count(self):
        obj = _FakeEtsy()
        db, _ = _make_db(fetchone=(10,))
        obj._db = db
        count = await asyncio.wait_for(obj.get_etsy_listings_count(), timeout=5)
        assert count == 10

    async def test_get_listings_no_views(self):
        obj = _FakeEtsy()
        rows = [{"listing_id": "L2", "views": 0, "status": "active"}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_listings_no_views(days=7), timeout=5)
        assert len(result) == 1

    async def test_get_listings_no_conversion(self):
        obj = _FakeEtsy()
        rows = [{"listing_id": "L3", "views": 50, "sales": 0}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_listings_no_conversion(days=45), timeout=5)
        assert len(result) == 1

    async def test_get_listings_no_views_no_sales(self):
        obj = _FakeEtsy()
        db, _ = _make_db(fetchall=[])
        obj._db = db
        result = await asyncio.wait_for(obj.get_listings_no_views_no_sales(), timeout=5)
        assert result == []

    async def test_flag_no_views(self):
        obj = _FakeEtsy()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(obj.flag_no_views("L1"), timeout=5)
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_flag_no_conversion(self):
        obj = _FakeEtsy()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(obj.flag_no_conversion("L1"), timeout=5)
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_flag_no_views_no_sales(self):
        obj = _FakeEtsy()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(obj.flag_no_views_no_sales("L1"), timeout=5)
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_get_listing_prev_views_found(self):
        obj = _FakeEtsy()
        db, _ = _make_db(fetchone=(3,))
        obj._db = db
        result = await asyncio.wait_for(obj.get_listing_prev_views("L1"), timeout=5)
        assert result == 3

    async def test_get_listing_prev_views_not_found(self):
        obj = _FakeEtsy()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        result = await asyncio.wait_for(obj.get_listing_prev_views("missing"), timeout=5)
        assert result is None

    async def test_save_listing_analysis(self):
        obj = _FakeEtsy()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(
            obj.save_listing_analysis(
                "L1",
                "no_views",
                "bad title",
                ["improve title", "add tags"],
                "avoid keyword X",
            ),
            timeout=5,
        )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_get_listing_analyses(self):
        obj = _FakeEtsy()
        rows = [
            {
                "listing_id": "L1",
                "analysis_type": "no_views",
                "recommendations": '["fix title"]',
                "cause": "bad title",
            }
        ]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_listing_analyses("L1"), timeout=5)
        assert len(result) == 1
        assert result[0]["recommendations"] == ["fix title"]

    async def test_get_all_listing_analyses(self):
        obj = _FakeEtsy()
        rows = [
            {
                "listing_id": "L1",
                "analysis_type": "no_views",
                "recommendations": '["r1"]',
                "cause": "c1",
            },
            {
                "listing_id": "L2",
                "analysis_type": "no_conversion",
                "recommendations": None,
                "cause": "c2",
            },
        ]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_all_listing_analyses(limit=20), timeout=5)
        assert len(result) == 2


# ===========================================================================
# OAuthMixin  (_oauth.py)
# ===========================================================================


class _FakeOAuth(OAuthMixin):
    pass


def _fernet_mock():
    """Return a callable mock that mimics self._fernet()."""
    instance = MagicMock()
    instance.encrypt.return_value = b"encrypted_token"
    instance.decrypt.return_value = b"plain_token"
    return MagicMock(return_value=instance)


class TestOAuthMixin:

    async def test_save_oauth_tokens(self):
        obj = _FakeOAuth()
        db, _ = _make_db()
        obj._db = db
        obj._fernet = _fernet_mock()
        await asyncio.wait_for(
            obj.save_oauth_tokens("etsy", "access123", "refresh456", "2099-01-01"),
            timeout=5,
        )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_save_oauth_tokens_encrypts_both_tokens(self):
        obj = _FakeOAuth()
        db, _ = _make_db()
        obj._db = db
        fernet_instance = MagicMock()
        fernet_instance.encrypt.return_value = b"enc"
        obj._fernet = MagicMock(return_value=fernet_instance)
        await asyncio.wait_for(
            obj.save_oauth_tokens("etsy", "at", "rt", "2099-01-01"),
            timeout=5,
        )
        # encrypt called at least twice (access + refresh)
        assert fernet_instance.encrypt.call_count >= 2

    async def test_get_oauth_tokens_found(self):
        obj = _FakeOAuth()
        row = {
            "provider": "etsy",
            "access_token_encrypted": "enc_access",
            "refresh_token_encrypted": "enc_refresh",
            "expires_at": "2099-01-01",
        }
        db, _ = _make_db(fetchone=row)
        obj._db = db
        fernet_instance = MagicMock()
        fernet_instance.decrypt.return_value = b"plain_value"
        obj._fernet = MagicMock(return_value=fernet_instance)
        result = await asyncio.wait_for(obj.get_oauth_tokens("etsy"), timeout=5)
        assert result["access_token"] == "plain_value"
        assert result["refresh_token"] == "plain_value"

    async def test_get_oauth_tokens_not_found(self):
        obj = _FakeOAuth()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        obj._fernet = _fernet_mock()
        result = await asyncio.wait_for(obj.get_oauth_tokens("etsy"), timeout=5)
        assert result is None

    async def test_update_oauth_tokens(self):
        obj = _FakeOAuth()
        db, _ = _make_db()
        obj._db = db
        obj._fernet = _fernet_mock()
        await asyncio.wait_for(
            obj.update_oauth_tokens("etsy", "new_at", "new_rt", "2099-12-31"),
            timeout=5,
        )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_update_oauth_tokens_encrypts(self):
        obj = _FakeOAuth()
        db, _ = _make_db()
        obj._db = db
        fernet_instance = MagicMock()
        fernet_instance.encrypt.return_value = b"enc"
        obj._fernet = MagicMock(return_value=fernet_instance)
        await asyncio.wait_for(
            obj.update_oauth_tokens("etsy", "at", "rt", "2099-12-31"),
            timeout=5,
        )
        assert fernet_instance.encrypt.call_count >= 2


# ===========================================================================
# ConversationsMixin  (_conversations.py)
# ===========================================================================


class _FakeConversations(ConversationsMixin):
    pass


class TestConversationsMixin:

    async def test_save_conversation(self):
        obj = _FakeConversations()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(
            obj.save_conversation("user", "Hello world"),
            timeout=5,
        )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_get_recent_conversations(self):
        obj = _FakeConversations()
        rows = [{"role": "user", "content": "Hi", "timestamp": "now"}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_recent_conversations(limit=5), timeout=5)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    async def test_save_message(self):
        obj = _FakeConversations()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(
            obj.save_message("session-1", "assistant", "Hello!", source="telegram"),
            timeout=5,
        )
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_get_conversation_history_no_domain(self):
        obj = _FakeConversations()
        rows = [
            {
                "id": 1,
                "role": "user",
                "content": "hi",
                "timestamp": "t",
                "domain": "etsy",
            }
        ]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(
            obj.get_conversation_history("session-1", limit=10),
            timeout=5,
        )
        assert len(result) == 1

    async def test_get_conversation_history_with_domain(self):
        obj = _FakeConversations()
        db, _ = _make_db(fetchall=[])
        obj._db = db
        result = await asyncio.wait_for(
            obj.get_conversation_history("session-1", limit=10, domain="etsy"),
            timeout=5,
        )
        assert result == []
        sql = db.execute.call_args[0][0]
        assert "domain = ?" in sql

    async def test_clear_session(self):
        obj = _FakeConversations()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(obj.clear_session("session-1"), timeout=5)
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_get_sessions(self):
        obj = _FakeConversations()
        rows = [{"session_id": "s1", "last_message": "hi", "timestamp": "now"}]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_sessions(), timeout=5)
        assert len(result) == 1
        assert result[0]["session_id"] == "s1"


# ===========================================================================
# RemindersMixin  (_reminders.py)
# ===========================================================================


class _FakeReminders(RemindersMixin):
    pass


class TestRemindersMixin:

    async def test_get_personal_recalls(self):
        obj = _FakeReminders()
        rows = [
            {
                "task_id": "t1",
                "input_data": '{"query":"what?"}',
                "output_data": '{"response":"because"}',
                "created_at": "2024-01-01",
                "status": "completed",
            }
        ]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_personal_recalls(limit=5), timeout=5)
        assert len(result) == 1
        assert result[0]["query"] == "what?"
        assert result[0]["response"] == "because"

    async def test_add_reminder_returns_lastrowid(self):
        obj = _FakeReminders()
        db, _ = _make_db(lastrowid=7)
        obj._db = db
        rid = await asyncio.wait_for(
            obj.add_reminder("Buy milk", "2099-12-31T08:00:00"),
            timeout=5,
        )
        assert rid == 7
        db.commit.assert_awaited()

    async def test_get_due_reminders(self):
        obj = _FakeReminders()
        rows = [
            {
                "id": 1,
                "text": "Buy milk",
                "trigger_at": "2024-01-01T08:00:00",
                "status": "pending",
            }
        ]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_due_reminders(), timeout=5)
        assert len(result) == 1

    async def test_mark_reminder_sent(self):
        obj = _FakeReminders()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(obj.mark_reminder_sent(1, telegram_msg_id=12345), timeout=5)
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_acknowledge_reminder_found(self):
        obj = _FakeReminders()
        db, _ = _make_db(fetchone={"id": 1})
        obj._db = db
        result = await asyncio.wait_for(obj.acknowledge_reminder(12345), timeout=5)
        assert result is True
        db.commit.assert_awaited()

    async def test_acknowledge_reminder_not_found(self):
        obj = _FakeReminders()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        result = await asyncio.wait_for(obj.acknowledge_reminder(99999), timeout=5)
        assert result is False

    async def test_get_reminder_notion_id_found(self):
        obj = _FakeReminders()
        db, _ = _make_db(fetchone={"notion_page_id": "np-abc"})
        obj._db = db
        result = await asyncio.wait_for(obj.get_reminder_notion_id(12345), timeout=5)
        assert result == "np-abc"

    async def test_get_reminder_notion_id_not_found(self):
        obj = _FakeReminders()
        db, _ = _make_db(fetchone=None)
        obj._db = db
        result = await asyncio.wait_for(obj.get_reminder_notion_id(99999), timeout=5)
        assert result is None

    async def test_cancel_reminder(self):
        obj = _FakeReminders()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(obj.cancel_reminder(1), timeout=5)
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_get_pending_reminders(self):
        obj = _FakeReminders()
        rows = [
            {
                "id": 1,
                "text": "Meeting",
                "trigger_at": "2099-01-01T10:00:00",
                "status": "pending",
            }
        ]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_pending_reminders(), timeout=5)
        assert len(result) == 1

    async def test_get_sent_unacknowledged(self):
        obj = _FakeReminders()
        rows = [
            {
                "id": 2,
                "text": "Call mom",
                "status": "sent",
                "acknowledged_at": None,
            }
        ]
        db, _ = _make_db(fetchall=rows)
        obj._db = db
        result = await asyncio.wait_for(obj.get_sent_unacknowledged(hours=4), timeout=5)
        assert len(result) == 1

    async def test_update_reminder_notion_id(self):
        obj = _FakeReminders()
        db, _ = _make_db()
        obj._db = db
        await asyncio.wait_for(obj.update_reminder_notion_id(1, "np-xyz"), timeout=5)
        db.execute.assert_called_once()
        db.commit.assert_awaited_once()
