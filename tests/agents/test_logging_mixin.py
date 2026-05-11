# MOCK CONTRACT — _LoggingMixin
#
# _LoggingMixin._log_step(step_type, description, input_data, output_data, duration_ms) → int
#   Acquires self._counters_lock (real asyncio.Lock), increments _step_counter,
#   calls self.memory.log_step(**kwargs) → AsyncMock(return_value=int),
#   then calls self._broadcast(event dict) → uses real _broadcast which calls _ws_broadcast.
#
# _LoggingMixin._broadcast(event) → None
#   If self._ws_broadcast is not None: await self._ws_broadcast(event), swallows exceptions.
#
# _LoggingMixin.execute(task) → AgentResult
#   Resets counters, calls memory.log_agent_task, _broadcast(started), run(task).
#   On success: finalize_agent_task("completed"), _broadcast(completed), return result.
#   On failure: log_error, finalize_agent_task("failed"), _broadcast(error), return FAILED result.
#
# _LoggingMixin._format_rel_time(dt) → str   [static]
# _LoggingMixin._task_description(task) → str [static]
# _LoggingMixin._estimate_cost(model, in, out, cache_read, cache_write) → float [static]
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import asyncio
from datetime import datetime as real_datetime
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.backend.agents._base._logging_mixin import _LoggingMixin
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus


# ─────────────────────────────────────────────────────────────────────────────
# Factory helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_agent(ws_broadcast=None, extra_attrs: dict | None = None):
    """Compose a minimal _LoggingMixin instance with all required protocol state."""

    class _Agent(_LoggingMixin):
        pass

    agent = _Agent()
    agent.name = "test_logger"
    agent._task_id = "task-001"
    agent._step_counter = 0
    agent._llm_call_count = 0
    agent._tool_call_count = 0
    agent._total_cost = 0.0
    agent._total_tokens = 0
    agent._counters_lock = asyncio.Lock()   # real lock — atomicity must be verifiable
    agent._ws_broadcast = ws_broadcast

    agent.memory = MagicMock()
    agent.memory.log_step = AsyncMock(return_value=99)
    agent.memory.log_agent_task = AsyncMock()
    agent.memory.log_error = AsyncMock()
    agent.memory.finalize_agent_task = AsyncMock()

    if extra_attrs:
        for k, v in extra_attrs.items():
            setattr(agent, k, v)

    return agent


def _make_task(input_data: dict | None = None, task_id: str = "task-abc12345") -> AgentTask:
    return AgentTask(agent_name="test_agent", input_data=input_data or {}, task_id=task_id)


def _success_result(task_id: str = "task-abc12345") -> AgentResult:
    return AgentResult(
        task_id=task_id,
        agent_name="test_logger",
        status=TaskStatus.COMPLETED,
        output_data={"ok": True},
    )


# ─────────────────────────────────────────────────────────────────────────────
# _log_step  (lines 29-52)
# ─────────────────────────────────────────────────────────────────────────────

class TestLogStep:

    @pytest.mark.asyncio
    async def test_increments_step_counter(self):
        agent = _make_agent()
        await asyncio.wait_for(agent._log_step("llm", "first"), timeout=5)
        assert agent._step_counter == 1

    @pytest.mark.asyncio
    async def test_multiple_calls_accumulate_counter(self):
        agent = _make_agent()
        for _ in range(3):
            await asyncio.wait_for(agent._log_step("tool", "x"), timeout=5)
        assert agent._step_counter == 3

    @pytest.mark.asyncio
    async def test_calls_memory_log_step_with_all_kwargs(self):
        agent = _make_agent()
        await asyncio.wait_for(
            agent._log_step(
                "tool", "desc",
                input_data={"k": "v"},
                output_data={"r": 1},
                duration_ms=42,
            ),
            timeout=5,
        )
        agent.memory.log_step.assert_called_once_with(
            task_id="task-001",
            agent_name="test_logger",
            step_number=1,
            step_type="tool",
            description="desc",
            input_data={"k": "v"},
            output_data={"r": 1},
            duration_ms=42,
        )

    @pytest.mark.asyncio
    async def test_returns_step_id_from_memory(self):
        agent = _make_agent()
        agent.memory.log_step = AsyncMock(return_value=42)
        result = await asyncio.wait_for(agent._log_step("llm", "x"), timeout=5)
        assert result == 42

    @pytest.mark.asyncio
    async def test_broadcasts_agent_step_event_when_ws_set(self):
        ws = AsyncMock()
        agent = _make_agent(ws_broadcast=ws)
        await asyncio.wait_for(agent._log_step("llm", "step desc", duration_ms=10), timeout=5)
        ws.assert_called_once()
        evt = ws.call_args[0][0]
        assert evt["type"] == "agent_step"
        assert evt["agent"] == "test_logger"
        assert evt["task_id"] == "task-001"
        assert evt["step_type"] == "llm"
        assert evt["description"] == "step desc"
        assert evt["duration_ms"] == 10

    @pytest.mark.asyncio
    async def test_broadcast_includes_step_id_and_number(self):
        ws = AsyncMock()
        agent = _make_agent(ws_broadcast=ws)
        agent.memory.log_step = AsyncMock(return_value=77)
        await asyncio.wait_for(agent._log_step("llm", "x"), timeout=5)
        evt = ws.call_args[0][0]
        assert evt["step_id"] == 77
        assert evt["step_number"] == 1

    @pytest.mark.asyncio
    async def test_no_error_when_ws_is_none(self):
        agent = _make_agent(ws_broadcast=None)
        step_id = await asyncio.wait_for(agent._log_step("llm", "x"), timeout=5)
        assert step_id == 99

    @pytest.mark.asyncio
    async def test_concurrent_calls_increment_atomically(self):
        """Concurrent _log_step calls must produce unique, gapless step numbers."""
        agent = _make_agent()
        n = 20
        await asyncio.wait_for(
            asyncio.gather(*[agent._log_step("llm", f"step {i}") for i in range(n)]),
            timeout=10,
        )
        assert agent._step_counter == n
        step_numbers = [c[1]["step_number"] for c in agent.memory.log_step.call_args_list]
        assert sorted(step_numbers) == list(range(1, n + 1))


# ─────────────────────────────────────────────────────────────────────────────
# _broadcast  (lines 56-60)
# ─────────────────────────────────────────────────────────────────────────────

class TestBroadcast:

    @pytest.mark.asyncio
    async def test_noop_when_ws_broadcast_is_none(self):
        agent = _make_agent(ws_broadcast=None)
        await asyncio.wait_for(agent._broadcast({"type": "test"}), timeout=5)

    @pytest.mark.asyncio
    async def test_calls_ws_broadcast_with_event(self):
        ws = AsyncMock()
        agent = _make_agent(ws_broadcast=ws)
        evt = {"type": "ping", "data": 123}
        await asyncio.wait_for(agent._broadcast(evt), timeout=5)
        ws.assert_called_once_with(evt)

    @pytest.mark.asyncio
    async def test_ws_exception_is_silently_swallowed(self):
        ws = AsyncMock(side_effect=RuntimeError("ws down"))
        agent = _make_agent(ws_broadcast=ws)
        # Must not propagate
        await asyncio.wait_for(agent._broadcast({"type": "x"}), timeout=5)
        ws.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# execute — success path  (lines 64-143)
# ─────────────────────────────────────────────────────────────────────────────

class TestExecuteSuccess:

    def _agent_with_run(self, task_id="task-abc12345", ws_broadcast=None):
        agent = _make_agent(ws_broadcast=ws_broadcast)
        agent.run = AsyncMock(return_value=_success_result(task_id))
        return agent

    @pytest.mark.asyncio
    async def test_resets_counters_on_start(self):
        agent = self._agent_with_run()
        agent._step_counter = 7
        await asyncio.wait_for(agent.execute(_make_task({"query": "hi"})), timeout=5)
        assert agent._llm_call_count == 0
        assert agent._tool_call_count == 0
        assert agent._total_cost == 0.0
        assert agent._total_tokens == 0

    @pytest.mark.asyncio
    async def test_sets_task_id_from_task(self):
        agent = self._agent_with_run(task_id="custom-task-id")
        task = _make_task(task_id="custom-task-id")
        await asyncio.wait_for(agent.execute(task), timeout=5)
        assert agent._task_id == "custom-task-id"

    @pytest.mark.asyncio
    async def test_logs_agent_task_as_running(self):
        agent = self._agent_with_run()
        task = _make_task({"query": "test"})
        await asyncio.wait_for(agent.execute(task), timeout=5)
        agent.memory.log_agent_task.assert_called_once_with(
            agent_name="test_logger",
            task_id=task.task_id,
            status="running",
            input_data=task.input_data,
        )

    @pytest.mark.asyncio
    async def test_broadcasts_agent_started(self):
        ws = AsyncMock()
        agent = self._agent_with_run(ws_broadcast=ws)
        await asyncio.wait_for(agent.execute(_make_task({"query": "hello"})), timeout=5)
        types = [c[0][0]["type"] for c in ws.call_args_list]
        assert "agent_started" in types

    @pytest.mark.asyncio
    async def test_broadcasts_agent_completed(self):
        ws = AsyncMock()
        agent = self._agent_with_run(ws_broadcast=ws)
        await asyncio.wait_for(agent.execute(_make_task()), timeout=5)
        types = [c[0][0]["type"] for c in ws.call_args_list]
        assert "agent_completed" in types

    @pytest.mark.asyncio
    async def test_agent_started_event_has_description(self):
        ws = AsyncMock()
        agent = self._agent_with_run(ws_broadcast=ws)
        await asyncio.wait_for(agent.execute(_make_task({"query": "find me"})), timeout=5)
        started_calls = [c[0][0] for c in ws.call_args_list if c[0][0]["type"] == "agent_started"]
        assert started_calls
        assert "description" in started_calls[0]

    @pytest.mark.asyncio
    async def test_finalizes_task_as_completed(self):
        agent = self._agent_with_run()
        task = _make_task()
        await asyncio.wait_for(agent.execute(task), timeout=5)
        agent.memory.finalize_agent_task.assert_called_once()
        kw = agent.memory.finalize_agent_task.call_args[1]
        assert kw["status"] == "completed"
        assert kw["task_id"] == task.task_id

    @pytest.mark.asyncio
    async def test_finalize_includes_counter_fields(self):
        agent = self._agent_with_run()
        await asyncio.wait_for(agent.execute(_make_task()), timeout=5)
        kw = agent.memory.finalize_agent_task.call_args[1]
        for field in ("tokens_used", "cost_usd", "total_llm_calls",
                      "total_tool_calls", "total_steps", "total_cost_usd"):
            assert field in kw

    @pytest.mark.asyncio
    async def test_returns_completed_result(self):
        agent = self._agent_with_run()
        result = await asyncio.wait_for(agent.execute(_make_task()), timeout=5)
        assert result.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_result_duration_ms_is_non_negative(self):
        agent = self._agent_with_run()
        result = await asyncio.wait_for(agent.execute(_make_task()), timeout=5)
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_result_tokens_and_cost_from_agent_state(self):
        agent = self._agent_with_run()
        result = await asyncio.wait_for(agent.execute(_make_task()), timeout=5)
        assert result.tokens_used == 0
        assert result.cost_usd == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# execute — failure path  (lines 89-119)
# ─────────────────────────────────────────────────────────────────────────────

class TestExecuteFailure:

    @pytest.mark.asyncio
    async def test_returns_failed_result_on_exception(self):
        agent = _make_agent()
        agent.run = AsyncMock(side_effect=ValueError("boom"))
        result = await asyncio.wait_for(agent.execute(_make_task({"query": "fail"})), timeout=5)
        assert result.status == TaskStatus.FAILED
        assert "boom" in result.output_data["error"]

    @pytest.mark.asyncio
    async def test_logs_error_with_agent_name_and_exc_type(self):
        agent = _make_agent()
        agent.run = AsyncMock(side_effect=RuntimeError("crash"))
        await asyncio.wait_for(agent.execute(_make_task()), timeout=5)
        agent.memory.log_error.assert_called_once()
        positional = agent.memory.log_error.call_args[0]
        assert positional[0] == "test_logger"
        assert "RuntimeError" in positional[1]

    @pytest.mark.asyncio
    async def test_finalizes_task_as_failed(self):
        agent = _make_agent()
        agent.run = AsyncMock(side_effect=KeyError("missing"))
        task = _make_task()
        await asyncio.wait_for(agent.execute(task), timeout=5)
        kw = agent.memory.finalize_agent_task.call_args[1]
        assert kw["status"] == "failed"
        assert kw["task_id"] == task.task_id

    @pytest.mark.asyncio
    async def test_broadcasts_agent_error(self):
        ws = AsyncMock()
        agent = _make_agent(ws_broadcast=ws)
        agent.run = AsyncMock(side_effect=KeyError("missing"))
        await asyncio.wait_for(agent.execute(_make_task()), timeout=5)
        types = [c[0][0]["type"] for c in ws.call_args_list]
        assert "agent_error" in types

    @pytest.mark.asyncio
    async def test_error_event_contains_error_message(self):
        ws = AsyncMock()
        agent = _make_agent(ws_broadcast=ws)
        agent.run = AsyncMock(side_effect=ValueError("detailed error"))
        await asyncio.wait_for(agent.execute(_make_task()), timeout=5)
        error_evts = [c[0][0] for c in ws.call_args_list if c[0][0]["type"] == "agent_error"]
        assert error_evts
        assert "detailed error" in error_evts[0]["error"]

    @pytest.mark.asyncio
    async def test_failure_result_has_non_negative_duration(self):
        agent = _make_agent()
        agent.run = AsyncMock(side_effect=TypeError("bad type"))
        result = await asyncio.wait_for(agent.execute(_make_task()), timeout=5)
        assert result.duration_ms >= 0


# ─────────────────────────────────────────────────────────────────────────────
# _format_rel_time (static)  — lines 158–190, gap at 174 and 177-190
# ─────────────────────────────────────────────────────────────────────────────

_FIXED_NOW = real_datetime(2024, 1, 15, 12, 0, 0)   # Mon 2024-01-15 12:00:00


class TestFormatRelTime:
    """All tests patch datetime.now for full determinism (no wall-clock jitter)."""

    def _dt(self, **kwargs) -> real_datetime:
        """Return _FIXED_NOW + timedelta(**kwargs)."""
        return _FIXED_NOW + timedelta(**kwargs)

    # ── Non-date-block branches ───────────────────────────────────────────────

    @patch("apps.backend.agents._base._logging_mixin.datetime")
    def test_past_returns_date_string(self, mock_dt):
        mock_dt.now.return_value = _FIXED_NOW
        dt = self._dt(hours=-2)   # 2h in the past → total_seconds = -7200
        result = _LoggingMixin._format_rel_time(dt)
        assert result == "il 15/01 alle 10:00"

    @patch("apps.backend.agents._base._logging_mixin.datetime")
    def test_under_60_seconds_returns_tra_pochi_secondi(self, mock_dt):
        mock_dt.now.return_value = _FIXED_NOW
        assert _LoggingMixin._format_rel_time(self._dt(seconds=30)) == "tra pochi secondi"

    @patch("apps.backend.agents._base._logging_mixin.datetime")
    def test_one_minute(self, mock_dt):
        # 90s → minutes = 1
        mock_dt.now.return_value = _FIXED_NOW
        assert _LoggingMixin._format_rel_time(self._dt(seconds=90)) == "tra un minuto"

    @patch("apps.backend.agents._base._logging_mixin.datetime")
    def test_multiple_minutes(self, mock_dt):
        # 600s → minutes = 10
        mock_dt.now.return_value = _FIXED_NOW
        assert _LoggingMixin._format_rel_time(self._dt(seconds=600)) == "tra 10 minuti"

    @patch("apps.backend.agents._base._logging_mixin.datetime")
    def test_one_hour_no_remaining_minutes(self, mock_dt):
        # 3600s → hours=1, remaining=0 → "tra un'ora"
        mock_dt.now.return_value = _FIXED_NOW
        assert _LoggingMixin._format_rel_time(self._dt(seconds=3600)) == "tra un'ora"

    @patch("apps.backend.agents._base._logging_mixin.datetime")
    def test_multiple_hours_no_remaining_minutes(self, mock_dt):
        # 7200s → hours=2, remaining=0 → "tra 2 ore"
        mock_dt.now.return_value = _FIXED_NOW
        assert _LoggingMixin._format_rel_time(self._dt(seconds=7200)) == "tra 2 ore"

    @patch("apps.backend.agents._base._logging_mixin.datetime")
    def test_one_hour_with_remaining_minutes_line_174(self, mock_dt):
        # 5400s → hours=1, remaining=30 → covers line 174
        mock_dt.now.return_value = _FIXED_NOW
        assert _LoggingMixin._format_rel_time(self._dt(seconds=5400)) == "tra un'ora e 30 minuti"

    @patch("apps.backend.agents._base._logging_mixin.datetime")
    def test_multiple_hours_with_remaining_minutes(self, mock_dt):
        # 9900s → hours=2, remaining=45
        mock_dt.now.return_value = _FIXED_NOW
        assert _LoggingMixin._format_rel_time(self._dt(seconds=9900)) == "tra 2 ore e 45 minuti"

    # ── Date-block branches ───────────────────────────────────────────────────

    @patch("apps.backend.agents._base._logging_mixin.datetime")
    def test_domani(self, mock_dt):
        mock_dt.now.return_value = real_datetime(2024, 6, 10, 10, 0, 0)
        dt = real_datetime(2024, 6, 11, 15, 30, 0)   # tomorrow at 15:30 → 29.5h
        assert _LoggingMixin._format_rel_time(dt) == "domani alle 15:30"

    @patch("apps.backend.agents._base._logging_mixin.datetime")
    def test_this_week_returns_weekday_name(self, mock_dt):
        mock_dt.now.return_value = real_datetime(2024, 6, 10, 10, 0, 0)   # Monday
        dt = real_datetime(2024, 6, 13, 14, 0, 0)   # Thursday, days_ahead=3
        assert _LoggingMixin._format_rel_time(dt) == "giovedì alle 14:00"

    @patch("apps.backend.agents._base._logging_mixin.datetime")
    def test_more_than_a_week_returns_date(self, mock_dt):
        mock_dt.now.return_value = real_datetime(2024, 6, 10, 10, 0, 0)
        dt = real_datetime(2024, 6, 25, 9, 0, 0)    # 15 days later
        assert _LoggingMixin._format_rel_time(dt) == "il 25/06 alle 09:00"

    @patch("apps.backend.agents._base._logging_mixin.datetime")
    def test_saturday_in_week(self, mock_dt):
        mock_dt.now.return_value = real_datetime(2024, 6, 10, 10, 0, 0)   # Monday
        dt = real_datetime(2024, 6, 15, 8, 0, 0)    # Saturday, days_ahead=5
        assert _LoggingMixin._format_rel_time(dt) == "sabato alle 08:00"


# ─────────────────────────────────────────────────────────────────────────────
# _task_description (static)  — lines 193-206, gap at 205-206
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskDescription:

    def _t(self, input_data, task_id: str = "abcd1234-5678"):
        task = MagicMock()
        task.task_id = task_id
        task.input_data = input_data
        return task

    def test_empty_dict_returns_task_id_prefix(self):
        assert _LoggingMixin._task_description(self._t({})) == "task abcd1234"

    def test_none_input_data_returns_task_id_prefix(self):
        assert _LoggingMixin._task_description(self._t(None)) == "task abcd1234"

    def test_query_key(self):
        assert _LoggingMixin._task_description(self._t({"query": "my query"})) == "query: my query"

    def test_niches_key_as_list_joined(self):
        result = _LoggingMixin._task_description(self._t({"niches": ["a", "b", "c"]}))
        assert result == "niches: a, b, c"

    def test_niches_list_truncated_to_3_items(self):
        result = _LoggingMixin._task_description(self._t({"niches": ["a", "b", "c", "d", "e"]}))
        assert result == "niches: a, b, c"

    def test_description_key(self):
        result = _LoggingMixin._task_description(self._t({"description": "some desc"}))
        assert result == "description: some desc"

    def test_action_key(self):
        assert _LoggingMixin._task_description(self._t({"action": "run"})) == "action: run"

    def test_symbol_key(self):
        assert _LoggingMixin._task_description(self._t({"symbol": "AAPL"})) == "symbol: AAPL"

    def test_message_key(self):
        result = _LoggingMixin._task_description(self._t({"message": "hello world"}))
        assert result == "message: hello world"

    def test_fallback_to_first_value_for_unknown_key(self):
        # Covers lines 205-206: no preferred key matches → first value
        result = _LoggingMixin._task_description(self._t({"custom_key": "fallback value"}))
        assert result == "fallback value"

    def test_fallback_truncates_at_80_chars(self):
        long_val = "y" * 200
        result = _LoggingMixin._task_description(self._t({"custom_key": long_val}))
        assert len(result) == 80

    def test_query_truncates_at_80_chars(self):
        result = _LoggingMixin._task_description(self._t({"query": "x" * 200}))
        assert len(result) <= 80


# ─────────────────────────────────────────────────────────────────────────────
# _estimate_cost (static)  — fully covered, verify correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestEstimateCost:

    def test_sonnet_model_positive_cost(self):
        assert _LoggingMixin._estimate_cost("claude-sonnet-4", 1_000_000, 1_000_000) > 0

    def test_haiku_model_positive_cost(self):
        assert _LoggingMixin._estimate_cost("claude-haiku-3", 1_000_000, 1_000_000) > 0

    def test_haiku_cheaper_than_sonnet(self):
        sonnet = _LoggingMixin._estimate_cost("sonnet", 1_000_000, 1_000_000)
        haiku  = _LoggingMixin._estimate_cost("haiku",  1_000_000, 1_000_000)
        assert haiku < sonnet

    def test_unknown_model_uses_sonnet_pricing(self):
        unknown = _LoggingMixin._estimate_cost("gpt-4-turbo", 1_000_000, 1_000_000)
        sonnet  = _LoggingMixin._estimate_cost("sonnet",      1_000_000, 1_000_000)
        assert unknown == pytest.approx(sonnet)

    def test_sonnet_cache_increases_cost(self):
        base       = _LoggingMixin._estimate_cost("sonnet", 100, 100)
        with_cache = _LoggingMixin._estimate_cost("sonnet", 100, 100, cache_read=1_000_000, cache_write=1_000_000)
        assert with_cache > base

    def test_haiku_cache_increases_cost(self):
        base       = _LoggingMixin._estimate_cost("haiku", 100, 100)
        with_cache = _LoggingMixin._estimate_cost("haiku", 100, 100, cache_read=1_000_000, cache_write=1_000_000)
        assert with_cache > base

    def test_zero_tokens_returns_zero(self):
        assert _LoggingMixin._estimate_cost("sonnet", 0, 0) == 0.0

    def test_returns_float_rounded_to_6_decimals(self):
        cost = _LoggingMixin._estimate_cost("sonnet", 123, 456)
        assert isinstance(cost, float)
        assert cost == round(cost, 6)
