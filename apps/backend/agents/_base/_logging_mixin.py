"""AgentBase — logging, broadcasting and task lifecycle mixin."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from apps.backend.core.config import settings
from apps.backend.core.models import AgentResult, TaskStatus

if TYPE_CHECKING:
    from apps.backend.core.models import AgentTask


class _LoggingMixin:
    """Mixin: step logging, WebSocket broadcasting, task lifecycle, static helpers."""

    async def _log_step(
        self,
        step_type: str,
        description: str | None,
        input_data: Any = None,
        output_data: Any = None,
        duration_ms: int = 0,
    ) -> int:
        self._step_counter += 1  # type: ignore[attr-defined]
        step_id = await self.memory.log_step(  # type: ignore[attr-defined]
            task_id=self._task_id,  # type: ignore[attr-defined]
            agent_name=self.name,  # type: ignore[attr-defined]
            step_number=self._step_counter,  # type: ignore[attr-defined]
            step_type=step_type,
            description=description,
            input_data=input_data,
            output_data=output_data,
            duration_ms=duration_ms,
        )
        await self._broadcast({  # type: ignore[attr-defined]
            "type": "agent_step",
            "agent": self.name,  # type: ignore[attr-defined]
            "task_id": self._task_id,  # type: ignore[attr-defined]
            "step_id": step_id,
            "step_number": self._step_counter,  # type: ignore[attr-defined]
            "step_type": step_type,
            "description": description,
            "duration_ms": duration_ms,
        })
        return step_id

    async def _broadcast(self, event: dict) -> None:
        """Invia evento WebSocket se broadcaster disponibile."""
        if self._ws_broadcast is not None:  # type: ignore[attr-defined]
            try:
                await self._ws_broadcast(event)  # type: ignore[attr-defined]
            except Exception:
                pass  # Non bloccare l'agente per errori WS

    async def execute(self, task: AgentTask) -> AgentResult:
        """Wrapper che gestisce logging, contatori e finalizzazione."""
        self._task_id = task.task_id  # type: ignore[attr-defined]
        self._step_counter = 0  # type: ignore[attr-defined]
        self._llm_call_count = 0  # type: ignore[attr-defined]
        self._tool_call_count = 0  # type: ignore[attr-defined]
        self._total_cost = 0.0  # type: ignore[attr-defined]
        self._total_tokens = 0  # type: ignore[attr-defined]

        t0 = time.monotonic()

        await self.memory.log_agent_task(  # type: ignore[attr-defined]
            agent_name=self.name,  # type: ignore[attr-defined]
            task_id=task.task_id,
            status="running",
            input_data=task.input_data,
        )
        _desc = self._task_description(task)
        await self._broadcast({  # type: ignore[attr-defined]
            "type": "agent_started",
            "agent": self.name,  # type: ignore[attr-defined]
            "task_id": task.task_id,
            "description": _desc,
        })

        try:
            result = await self.run(task)  # type: ignore[attr-defined]
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            await self.memory.log_error(  # type: ignore[attr-defined]
                self.name, type(exc).__name__, str(exc), task_id=task.task_id  # type: ignore[attr-defined]
            )
            await self.memory.finalize_agent_task(  # type: ignore[attr-defined]
                task_id=task.task_id,
                status="failed",
                output_data={"error": str(exc)},
                tokens_used=self._total_tokens,  # type: ignore[attr-defined]
                cost_usd=self._total_cost,  # type: ignore[attr-defined]
                total_llm_calls=self._llm_call_count,  # type: ignore[attr-defined]
                total_tool_calls=self._tool_call_count,  # type: ignore[attr-defined]
                total_steps=self._step_counter,  # type: ignore[attr-defined]
                total_cost_usd=self._total_cost,  # type: ignore[attr-defined]
            )
            await self._broadcast({  # type: ignore[attr-defined]
                "type": "agent_error",
                "agent": self.name,  # type: ignore[attr-defined]
                "task_id": task.task_id,
                "error": str(exc),
            })
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,  # type: ignore[attr-defined]
                status=TaskStatus.FAILED,
                output_data={"error": str(exc)},
                tokens_used=self._total_tokens,  # type: ignore[attr-defined]
                cost_usd=self._total_cost,  # type: ignore[attr-defined]
                duration_ms=duration_ms,
            )

        duration_ms = int((time.monotonic() - t0) * 1000)

        await self.memory.finalize_agent_task(  # type: ignore[attr-defined]
            task_id=task.task_id,
            status="completed",
            output_data=result.output_data,
            tokens_used=self._total_tokens,  # type: ignore[attr-defined]
            cost_usd=self._total_cost,  # type: ignore[attr-defined]
            total_llm_calls=self._llm_call_count,  # type: ignore[attr-defined]
            total_tool_calls=self._tool_call_count,  # type: ignore[attr-defined]
            total_steps=self._step_counter,  # type: ignore[attr-defined]
            total_cost_usd=self._total_cost,  # type: ignore[attr-defined]
        )
        await self._broadcast({  # type: ignore[attr-defined]
            "type": "agent_completed",
            "agent": self.name,  # type: ignore[attr-defined]
            "task_id": task.task_id,
        })

        result.duration_ms = duration_ms
        result.tokens_used = self._total_tokens  # type: ignore[attr-defined]
        result.cost_usd = self._total_cost  # type: ignore[attr-defined]
        return result

    @staticmethod
    def _format_rel_time(dt: datetime) -> str:
        """Restituisce una stringa italiana che descrive il momento del trigger rispetto ad ora.

        Esempi:
          'tra pochi secondi'  'tra un minuto'   'tra 8 minuti'
          'tra un'ora'         'tra 2 ore e 30 minuti'
          'oggi alle 20:30'    'domani alle 9:00'
          'venerdì alle 15:00' 'il 25/04 alle 10:00'
        """
        now = datetime.now()
        total_seconds = (dt - now).total_seconds()

        if total_seconds < 0:
            return dt.strftime("il %d/%m alle %H:%M")
        if total_seconds < 60:
            return "tra pochi secondi"

        minutes = int(total_seconds // 60)
        hours   = int(total_seconds // 3600)

        if minutes < 60:
            return "tra un minuto" if minutes == 1 else f"tra {minutes} minuti"

        if hours < 24:
            remaining_minutes = int((total_seconds % 3600) // 60)
            if remaining_minutes == 0:
                return "tra un'ora" if hours == 1 else f"tra {hours} ore"
            if hours == 1:
                return f"tra un'ora e {remaining_minutes} minuti"
            return f"tra {hours} ore e {remaining_minutes} minuti"

        today       = now.date()
        target_date = dt.date()
        time_str    = dt.strftime("%H:%M")

        if target_date == today:
            return f"oggi alle {time_str}"
        if target_date == today + timedelta(days=1):
            return f"domani alle {time_str}"

        days_ahead = (target_date - today).days
        if days_ahead < 7:
            _DAYS_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
            return f"{_DAYS_IT[dt.weekday()]} alle {time_str}"
        return f"il {dt.strftime('%d/%m')} alle {time_str}"

    @staticmethod
    def _task_description(task: AgentTask) -> str:
        """Costruisce una descrizione leggibile dal task input (max 80 char)."""
        d = task.input_data
        if not d:
            return f"task {task.task_id[:8]}"
        for key in ("query", "niches", "description", "action", "symbol", "message"):
            val = d.get(key)
            if val:
                if isinstance(val, list):
                    val = ", ".join(str(v) for v in val[:3])
                txt = f"{key}: {val}"
                return txt[:80]
        first_val = next(iter(d.values()), "")
        return str(first_val)[:80]

    @staticmethod
    def _estimate_cost(
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> float:
        """Stima costo USD basata su pricing Anthropic (valori configurabili in settings)."""
        if "sonnet" in model:
            cost = (
                input_tokens * settings.LLM_SONNET_INPUT_PRICE
                + output_tokens * settings.LLM_SONNET_OUTPUT_PRICE
            ) / 1_000_000
            cost += (
                cache_read * settings.LLM_SONNET_CACHE_READ_PRICE
                + cache_write * settings.LLM_SONNET_CACHE_WRITE_PRICE
            ) / 1_000_000
        elif "haiku" in model:
            cost = (
                input_tokens * settings.LLM_HAIKU_INPUT_PRICE
                + output_tokens * settings.LLM_HAIKU_OUTPUT_PRICE
            ) / 1_000_000
            cost += (
                cache_read * settings.LLM_HAIKU_CACHE_READ_PRICE
                + cache_write * settings.LLM_HAIKU_CACHE_WRITE_PRICE
            ) / 1_000_000
        else:
            cost = (
                input_tokens * settings.LLM_SONNET_INPUT_PRICE
                + output_tokens * settings.LLM_SONNET_OUTPUT_PRICE
            ) / 1_000_000
        return round(cost, 6)
