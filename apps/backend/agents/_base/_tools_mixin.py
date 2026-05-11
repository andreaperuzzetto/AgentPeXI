"""AgentBase — tool & sub-agent call mixin."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from dataclasses import asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, TypeVar

if TYPE_CHECKING:
    from apps.backend.agents._base._protocols import AgentCoreProtocol
    from apps.backend.core.models import AgentResult, AgentTask

_R = TypeVar("_R")


class _ToolsMixin:
    """Mixin: external tool calls and sub-agent spawning."""

    async def _call_tool(
        self: AgentCoreProtocol,
        tool_name: str,
        action: str,
        input_params: dict[str, Any] | None,
        fn: Callable[..., Awaitable[_R] | _R],
        *args: Any,
        **kwargs: Any,
    ) -> _R:
        t0 = time.monotonic()
        status = "success"
        result: Any = None
        cost_usd: float | None = None

        try:
            if asyncio.iscoroutinefunction(fn):
                result = await fn(*args, **kwargs)
            else:
                result = fn(*args, **kwargs)
        except Exception as exc:
            status = "error"
            result = {"error": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            duration_ms = int((time.monotonic() - t0) * 1000)

            # Serializza output per log (tronca se troppo grande)
            output_for_log = result
            if isinstance(result, (dict, list)):
                output_for_log = result
            elif result is not None:
                output_for_log = str(result)[:2000]

            step_id = await self._log_step(
                step_type="tool_call",
                description=f"{tool_name}.{action} [{status}]",
                input_data=input_params,
                output_data=output_for_log,
                duration_ms=duration_ms,
            )

            await self.memory.log_tool_call(
                task_id=self._task_id,
                step_id=step_id,
                agent_name=self.name,
                tool_name=tool_name,
                action=action,
                input_params=input_params,
                output_result=output_for_log,
                status=status,
                duration_ms=duration_ms,
                cost_usd=cost_usd,
            )

            await self._broadcast({
                "type": "tool_call",
                "agent": self.name,
                "task_id": self._task_id,
                "step_id": step_id,
                "tool": tool_name,
                "action": action,
                "status": status,
                "duration_ms": duration_ms,
                "cost_usd": cost_usd,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            async with self._counters_lock:
                self._tool_call_count += 1

        return result  # type: ignore[return-value]

    async def spawn_subagent(self: AgentCoreProtocol, task: AgentTask) -> AgentResult:
        t0 = time.monotonic()

        step_id = await self._log_step(
            step_type="subagent_spawn",
            description=f"Spawn sub-agent {self.name} → task {task.task_id}",
            input_data=asdict(task),
        )

        await self._broadcast({
            "type": "subagent_spawn",
            "parent_agent": self.name,
            "task_id": self._task_id,
            "sub_task_id": task.task_id,
            "description": f"Sub-task per {self.name}",
        })

        sub_agent = self.__class__(
            anthropic_client=self.client,
            memory=self.memory,
            ws_broadcaster=self._ws_broadcast,
            **self._extra_init_kwargs(),
        )
        result = await sub_agent.execute(task)

        duration_ms = int((time.monotonic() - t0) * 1000)

        await self.memory.log_step(
            task_id=self._task_id,
            agent_name=self.name,
            step_number=self._step_counter,
            step_type="subagent_spawn",
            description=f"Sub-agent {self.name} completato ({result.status.value})",
            input_data=asdict(task),
            output_data=result.output_data,
            duration_ms=duration_ms,
        )

        async with self._counters_lock:
            self._total_cost += result.cost_usd
            self._total_tokens += result.tokens_used

        return result

