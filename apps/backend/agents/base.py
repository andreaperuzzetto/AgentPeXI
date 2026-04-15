"""AgentBase — classe astratta per tutti gli agenti AgentPeXI."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import asdict
from typing import Any, Callable, Coroutine

import anthropic

from apps.backend.core.config import MODEL_SONNET
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus


class AgentBase(ABC):
    """Base comune per Research, Design, Publisher, Analytics, CustomerService, Finance."""

    def __init__(
        self,
        name: str,
        model: str,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.client = anthropic_client
        self.memory = memory
        self._ws_broadcast = ws_broadcaster

        # Contatori interni per task corrente (reset a ogni run)
        self._task_id: str = ""
        self._step_counter: int = 0
        self._llm_call_count: int = 0
        self._tool_call_count: int = 0
        self._total_cost: float = 0.0
        self._total_tokens: int = 0

    # ------------------------------------------------------------------
    # Metodo astratto — ogni agente lo implementa
    # ------------------------------------------------------------------

    @abstractmethod
    async def run(self, task: AgentTask) -> AgentResult:
        """Esegue il task e restituisce il risultato."""
        ...

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def execute(self, task: AgentTask) -> AgentResult:
        """Wrapper che gestisce logging, contatori e finalizzazione."""
        self._task_id = task.task_id
        self._step_counter = 0
        self._llm_call_count = 0
        self._tool_call_count = 0
        self._total_cost = 0.0
        self._total_tokens = 0

        t0 = time.monotonic()

        # Logga avvio task
        await self.memory.log_agent_task(
            agent_name=self.name,
            task_id=task.task_id,
            status="running",
            input_data=task.input_data,
        )
        # Deriva una descrizione leggibile dall'input del task
        _desc = self._task_description(task)
        await self._broadcast({
            "type": "agent_started",
            "agent": self.name,
            "task_id": task.task_id,
            "description": _desc,
        })

        try:
            result = await self.run(task)
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            await self.memory.log_error(
                self.name, type(exc).__name__, str(exc), task_id=task.task_id
            )
            await self.memory.finalize_agent_task(
                task_id=task.task_id,
                status="failed",
                output_data={"error": str(exc)},
                tokens_used=self._total_tokens,
                cost_usd=self._total_cost,
                total_llm_calls=self._llm_call_count,
                total_tool_calls=self._tool_call_count,
                total_steps=self._step_counter,
                total_cost_usd=self._total_cost,
            )
            await self._broadcast({
                "type": "agent_error",
                "agent": self.name,
                "task_id": task.task_id,
                "error": str(exc),
            })
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": str(exc)},
                tokens_used=self._total_tokens,
                cost_usd=self._total_cost,
                duration_ms=duration_ms,
            )

        duration_ms = int((time.monotonic() - t0) * 1000)

        # Finalizza task completato
        await self.memory.finalize_agent_task(
            task_id=task.task_id,
            status="completed",
            output_data=result.output_data,
            tokens_used=self._total_tokens,
            cost_usd=self._total_cost,
            total_llm_calls=self._llm_call_count,
            total_tool_calls=self._tool_call_count,
            total_steps=self._step_counter,
            total_cost_usd=self._total_cost,
        )
        await self._broadcast({
            "type": "agent_completed",
            "agent": self.name,
            "task_id": task.task_id,
        })

        result.duration_ms = duration_ms
        result.tokens_used = self._total_tokens
        result.cost_usd = self._total_cost
        return result

    # ------------------------------------------------------------------
    # _log_step — registra ogni passo in agent_steps + WebSocket
    # ------------------------------------------------------------------

    async def _log_step(
        self,
        step_type: str,
        description: str | None,
        input_data: Any = None,
        output_data: Any = None,
        duration_ms: int = 0,
    ) -> int:
        self._step_counter += 1
        step_id = await self.memory.log_step(
            task_id=self._task_id,
            agent_name=self.name,
            step_number=self._step_counter,
            step_type=step_type,
            description=description,
            input_data=input_data,
            output_data=output_data,
            duration_ms=duration_ms,
        )
        await self._broadcast({
            "type": "agent_step",
            "agent": self.name,
            "task_id": self._task_id,
            "step_id": step_id,
            "step_number": self._step_counter,
            "step_type": step_type,
            "description": description,
            "duration_ms": duration_ms,
        })
        return step_id

    # ------------------------------------------------------------------
    # _call_llm — wrapper Anthropic con retry, logging, WebSocket
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        messages: list[dict],
        system_prompt: str | None = None,
        model_override: str | None = None,
        max_tokens: int = 4096,
    ) -> str:
        model = model_override or self.model
        t0 = time.monotonic()

        # Retry su rate limit (429) e overload (529)
        response = await self._llm_with_retry(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
        )

        duration_ms = int((time.monotonic() - t0) * 1000)
        usage = response.usage

        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

        # Calcolo costo approssimativo
        cost_usd = self._estimate_cost(model, input_tokens, output_tokens, cache_read, cache_write)

        response_text = response.content[0].text if response.content else ""

        # Log step
        step_id = await self._log_step(
            step_type="llm_call",
            description=f"LLM {model} ({input_tokens}+{output_tokens} tok)",
            input_data={"system_prompt": system_prompt, "messages": messages},
            output_data={"response": response_text[:500]},
            duration_ms=duration_ms,
        )

        # Log dettagliato in llm_calls
        await self.memory.log_llm_call(
            task_id=self._task_id,
            step_id=step_id,
            agent_name=self.name,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            response=response_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
        )

        # WebSocket event
        await self._broadcast({
            "type": "llm_call",
            "agent": self.name,
            "task_id": self._task_id,
            "step_id": step_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
        })

        # Aggiorna contatori
        self._llm_call_count += 1
        self._total_cost += cost_usd
        self._total_tokens += input_tokens + output_tokens

        return response_text

    async def _llm_with_retry(
        self,
        model: str,
        messages: list[dict],
        system_prompt: str | None,
        max_tokens: int,
        max_retries: int = 3,
    ) -> Any:
        """Chiama Anthropic con retry esponenziale su 429/529."""
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                return await self.client.messages.create(**kwargs)
            except anthropic.RateLimitError as exc:
                last_exc = exc
                wait = 2 ** attempt
                await asyncio.sleep(wait)
            except anthropic.APIStatusError as exc:
                if exc.status_code == 529:  # overloaded
                    last_exc = exc
                    wait = 2 ** attempt
                    await asyncio.sleep(wait)
                else:
                    raise
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # _call_tool — wrapper generico per qualsiasi tool esterno
    # ------------------------------------------------------------------

    async def _call_tool(
        self,
        tool_name: str,
        action: str,
        input_params: dict | None,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
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

            # Log step
            step_id = await self._log_step(
                step_type="tool_call",
                description=f"{tool_name}.{action} [{status}]",
                input_data=input_params,
                output_data=output_for_log,
                duration_ms=duration_ms,
            )

            # Log dettagliato in tool_calls
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

            # WebSocket event
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
            })

            self._tool_call_count += 1

        return result

    # ------------------------------------------------------------------
    # spawn_subagent — delega sub-task a nuova istanza dello stesso agente
    # ------------------------------------------------------------------

    async def spawn_subagent(self, task: AgentTask) -> AgentResult:
        t0 = time.monotonic()

        # Log step subagent_spawn
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

        # Crea nuova istanza dello stesso tipo di agente
        sub_agent = self.__class__(
            name=self.name,
            model=self.model,
            anthropic_client=self.client,
            memory=self.memory,
            ws_broadcaster=self._ws_broadcast,
        )
        result = await sub_agent.execute(task)

        duration_ms = int((time.monotonic() - t0) * 1000)

        # Aggiorna lo step con l'output del sub-agent
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

        # Aggrega costi del sub-agent
        self._total_cost += result.cost_usd
        self._total_tokens += result.tokens_used

        return result

    # ------------------------------------------------------------------
    # Helpers privati
    # ------------------------------------------------------------------

    async def _broadcast(self, event: dict) -> None:
        """Invia evento WebSocket se broadcaster disponibile."""
        if self._ws_broadcast is not None:
            try:
                await self._ws_broadcast(event)
            except Exception:
                pass  # Non bloccare l'agente per errori WS

    @staticmethod
    def _task_description(task: AgentTask) -> str:
        """Costruisce una descrizione leggibile dal task input (max 80 char)."""
        d = task.input_data
        if not d:
            return f"task {task.task_id[:8]}"
        # Prova campi comuni in ordine di priorità
        for key in ("query", "niches", "description", "action", "symbol", "message"):
            val = d.get(key)
            if val:
                if isinstance(val, list):
                    val = ", ".join(str(v) for v in val[:3])
                txt = f"{key}: {val}"
                return txt[:80]
        # Fallback: primo valore qualsiasi
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
        """Stima costo USD basata su pricing Anthropic (aprile 2025)."""
        if "sonnet" in model:
            # Sonnet 4: $3/M input, $15/M output
            cost = (input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000
            # Cache: read 90% sconto, write 25% sovrapprezzo
            cost += (cache_read * 0.3 + cache_write * 3.75) / 1_000_000
        elif "haiku" in model:
            # Haiku: $0.80/M input, $4/M output
            cost = (input_tokens * 0.80 + output_tokens * 4.0) / 1_000_000
            cost += (cache_read * 0.08 + cache_write * 1.0) / 1_000_000
        else:
            # Fallback generico
            cost = (input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000
        return round(cost, 6)
