"""Agent logs mixin for MemoryManager."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from apps.backend.core._memory._base import _json_dumps, _json_loads

logger = logging.getLogger("agentpexi.memory")


class AgentLogsMixin:
    # ------------------------------------------------------------------
    # Agent logs
    # ------------------------------------------------------------------

    async def log_agent_task(
        self,
        agent_name: str,
        task_id: str,
        status: str = "running",
        input_data: Any = None,
        output_data: Any = None,
        tokens: int = 0,
        cost: float = 0.0,
    ) -> None:
        await self._db.execute(
            """INSERT INTO agent_logs
               (agent_name, task_id, status, input_data, output_data, tokens_used, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                agent_name,
                task_id,
                status,
                _json_dumps(input_data),
                _json_dumps(output_data),
                tokens,
                cost,
            ),
        )
        await self._db.commit()

    async def finalize_agent_task(
        self,
        task_id: str,
        status: str = "completed",
        output_data: Any = None,
        tokens_used: int = 0,
        cost_usd: float = 0.0,
        total_llm_calls: int = 0,
        total_tool_calls: int = 0,
        total_steps: int = 0,
        total_cost_usd: float = 0.0,
    ) -> None:
        await self._db.execute(
            """UPDATE agent_logs SET
               status = ?, output_data = ?, tokens_used = ?, cost_usd = ?,
               total_llm_calls = ?, total_tool_calls = ?, total_steps = ?,
               total_cost_usd = ?, updated_at = datetime('now')
               WHERE task_id = ?""",
            (
                status,
                _json_dumps(output_data),
                tokens_used,
                cost_usd,
                total_llm_calls,
                total_tool_calls,
                total_steps,
                total_cost_usd,
                task_id,
            ),
        )
        await self._db.commit()

    async def get_task_by_id(self, task_id: str) -> dict | None:
        cursor = await self._db.execute(
            "SELECT * FROM agent_logs WHERE task_id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["input_data"] = _json_loads(d.get("input_data"))
        d["output_data"] = _json_loads(d.get("output_data"))
        return d

    async def get_last_failed_task(self, agent_name: str | None = None) -> dict | None:
        if agent_name:
            cursor = await self._db.execute(
                """SELECT * FROM agent_logs
                   WHERE status = 'failed' AND agent_name = ?
                   AND status != 'input_required'
                   ORDER BY updated_at DESC LIMIT 1""",
                (agent_name,),
            )
        else:
            cursor = await self._db.execute(
                """SELECT * FROM agent_logs
                   WHERE status = 'failed' AND status != 'input_required'
                   ORDER BY updated_at DESC LIMIT 1"""
            )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["input_data"] = _json_loads(d.get("input_data"))
        d["output_data"] = _json_loads(d.get("output_data"))
        return d

    # ------------------------------------------------------------------
    # Error log
    # ------------------------------------------------------------------

    async def log_error(
        self,
        agent_name: str,
        error_type: str,
        message: str,
        task_id: str | None = None,
    ) -> None:
        await self._db.execute(
            "INSERT INTO error_log (agent_name, error_type, message, task_id) VALUES (?, ?, ?, ?)",
            (agent_name, error_type, message, task_id),
        )
        await self._db.commit()

    async def get_agent_error_count(self, agent_name: str, hours: int = 1) -> int:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM error_log WHERE agent_name = ? AND timestamp >= ?",
            (agent_name, since),
        )
        row = await cursor.fetchone()
        return row[0]

    # ------------------------------------------------------------------
    # Observability — agent_steps, llm_calls, tool_calls
    # ------------------------------------------------------------------

    async def log_step(
        self,
        task_id: str,
        agent_name: str,
        step_number: int,
        step_type: str,
        description: str | None,
        input_data: Any = None,
        output_data: Any = None,
        duration_ms: int = 0,
    ) -> int:
        try:
            cursor = await self._db.execute(
                """INSERT INTO agent_steps
                   (task_id, agent_name, step_number, step_type, description,
                    input_data, output_data, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    agent_name,
                    step_number,
                    step_type,
                    description,
                    _json_dumps(input_data),
                    _json_dumps(output_data),
                    duration_ms,
                ),
            )
            await self._db.commit()
            return cursor.lastrowid
        except Exception:
            await self._db.rollback()
            raise

    async def log_llm_call(
        self,
        task_id: str,
        step_id: int | None,
        agent_name: str,
        model: str,
        system_prompt: str | None,
        messages: Any,
        response: str | None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
        provider: str = "anthropic",
    ) -> int:
        """Logga una chiamata LLM nel DB.

        Args:
            provider: 'anthropic' o 'ollama'. Default 'anthropic' per backward compat.
        """
        try:
            cursor = await self._db.execute(
                """INSERT INTO llm_calls
                   (task_id, step_id, agent_name, model, provider, system_prompt, messages,
                    response, input_tokens, output_tokens, cache_read_tokens,
                    cache_write_tokens, cost_usd, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    step_id,
                    agent_name,
                    model,
                    provider,
                    system_prompt,
                    _json_dumps(messages),
                    response,
                    input_tokens,
                    output_tokens,
                    cache_read_tokens,
                    cache_write_tokens,
                    cost_usd,
                    duration_ms,
                ),
            )
            await self._db.commit()
            return cursor.lastrowid
        except Exception:
            await self._db.rollback()
            raise

    async def log_tool_call(
        self,
        task_id: str,
        step_id: int | None,
        agent_name: str,
        tool_name: str,
        action: str,
        input_params: Any = None,
        output_result: Any = None,
        status: str = "success",
        duration_ms: int = 0,
        cost_usd: float | None = None,
    ) -> int:
        try:
            cursor = await self._db.execute(
                """INSERT INTO tool_calls
                   (task_id, step_id, agent_name, tool_name, action,
                    input_params, output_result, status, duration_ms, cost_usd)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    step_id,
                    agent_name,
                    tool_name,
                    action,
                    _json_dumps(input_params),
                    _json_dumps(output_result),
                    status,
                    duration_ms,
                    cost_usd,
                ),
            )
            await self._db.commit()
            return cursor.lastrowid
        except Exception:
            await self._db.rollback()
            raise

    async def get_task_timeline(self, task_id: str) -> list[dict]:
        """Restituisce tutti gli step + llm_calls + tool_calls per un task, ordinati per timestamp."""
        results: list[dict] = []

        # Escludi step_type 'tool_call' e 'llm_call': i dati sono già nelle
        # tabelle dedicate (tool_calls, llm_calls) con info più ricche.
        cursor = await self._db.execute(
            "SELECT * FROM agent_steps WHERE task_id = ? AND step_type NOT IN ('tool_call', 'llm_call')",
            (task_id,),
        )
        for row in await cursor.fetchall():
            d = dict(row)
            d["type"] = "agent_step"
            d["input_data"] = _json_loads(d.get("input_data"))
            d["output_data"] = _json_loads(d.get("output_data"))
            results.append(d)

        cursor = await self._db.execute(
            "SELECT * FROM llm_calls WHERE task_id = ?",
            (task_id,),
        )
        for row in await cursor.fetchall():
            d = dict(row)
            d["type"] = "llm_call"
            d["messages"] = _json_loads(d.get("messages"))
            results.append(d)

        cursor = await self._db.execute(
            "SELECT * FROM tool_calls WHERE task_id = ?",
            (task_id,),
        )
        for row in await cursor.fetchall():
            d = dict(row)
            d["type"] = "tool_call"
            d["input_params"] = _json_loads(d.get("input_params"))
            d["output_result"] = _json_loads(d.get("output_result"))
            results.append(d)

        results.sort(key=lambda x: x.get("timestamp", ""))
        return results
