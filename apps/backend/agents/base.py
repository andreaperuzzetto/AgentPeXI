"""AgentBase — classe astratta per tutti gli agenti AgentPeXI."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable, Coroutine

import anthropic
import openai

from apps.backend.core.config import MODEL_SONNET, settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus


class AgentBase(ABC):
    """Base comune per Research, Design, Publisher, Analytics, CustomerService, Finance."""

    # Client Ollama condiviso a livello di classe — creato al primo utilizzo.
    # AsyncOpenAI è thread-safe e connection-pool-aware: non va istanziato ad ogni chiamata.
    _ollama_client: openai.AsyncOpenAI | None = None

    @classmethod
    def _get_ollama_client(cls) -> openai.AsyncOpenAI:
        if cls._ollama_client is None:
            cls._ollama_client = openai.AsyncOpenAI(
                base_url=settings.OLLAMA_BASE_URL,
                api_key="ollama",  # Ollama non richiede API key reale
            )
        return cls._ollama_client
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

    def _extra_init_kwargs(self) -> dict:
        """Kwargs aggiuntivi passati al costruttore in spawn_subagent.

        Sovrascrivi nelle sottoclassi con parametri obbligatori extra
        (es. storage, etsy_api) per evitare TypeError alla creazione.
        """
        return {}

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
        domain_name: str = "etsy_store",
    ) -> str:
        """Chiama LLM con routing automatico: Ollama per dominio personal, Anthropic altrimenti.

        Args:
            domain_name: Nome del dominio attivo (es. 'personal', 'etsy_store').
                         Se 'personal' → Ollama locale (costo zero, privacy totale).
                         Altrimenti → Anthropic Claude (comportamento invariato).
        """
        use_ollama = domain_name == "personal"

        if use_ollama:
            return await self._call_llm_ollama(
                messages=messages,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
            )

        # --- Path Anthropic (comportamento originale) ---
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
            provider="anthropic",
        )

        # WebSocket event
        await self._broadcast({
            "type": "llm_call",
            "agent": self.name,
            "task_id": self._task_id,
            "step_id": step_id,
            "model": model,
            "provider": "anthropic",
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

    async def _call_llm_ollama(
        self,
        messages: list[dict] | None = None,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        *,
        system: str | None = None,
        user: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """Chiama Ollama via API compatibile OpenAI. Costo zero, privacy totale.

        Supporta due convenzioni di chiamata:
          1. messages/system_prompt (stile _call_llm)
          2. system/user/temperature (stile caveman, usato dagli agenti Personal)

        Ollama deve essere avviato con OLLAMA_KEEP_ALIVE=-1 per tenere il modello
        permanentemente in RAM (configurato in .env e nel plist launchd).
        """
        # Risolve i parametri convenience (system/user) in messages/system_prompt
        effective_system = system_prompt or system
        if messages is None:
            effective_messages: list[dict] = (
                [{"role": "user", "content": user}] if user else []
            )
        else:
            effective_messages = messages

        t0 = time.monotonic()
        model = settings.OLLAMA_MODEL

        # Costruisci messages list per OpenAI SDK (system come primo messaggio)
        ollama_messages: list[dict] = []
        if effective_system:
            ollama_messages.append({"role": "system", "content": effective_system})
        ollama_messages.extend(effective_messages)

        # Aggiorna le variabili usate nel log a fine metodo
        system_prompt = effective_system
        messages = effective_messages

        ollama_client = self._get_ollama_client()

        create_kwargs: dict = {
            "model": model,
            "messages": ollama_messages,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            create_kwargs["temperature"] = temperature

        try:
            response = await ollama_client.chat.completions.create(**create_kwargs)
        except Exception as exc:
            raise RuntimeError(f"Ollama non disponibile ({model}): {exc}") from exc

        duration_ms = int((time.monotonic() - t0) * 1000)

        response_text = response.choices[0].message.content or "" if response.choices else ""
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        cost_usd = 0.0  # Ollama locale = €0

        # Log step
        step_id = await self._log_step(
            step_type="llm_call",
            description=f"Ollama {model} ({input_tokens}+{output_tokens} tok)",
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
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            provider="ollama",
        )

        # WebSocket event
        await self._broadcast({
            "type": "llm_call",
            "agent": self.name,
            "task_id": self._task_id,
            "step_id": step_id,
            "model": model,
            "provider": "ollama",
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
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

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
                "timestamp": datetime.utcnow().isoformat(),
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
            anthropic_client=self.client,
            memory=self.memory,
            ws_broadcaster=self._ws_broadcast,
            **self._extra_init_kwargs(),
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
            # Fallback: usa prezzi Sonnet
            cost = (
                input_tokens * settings.LLM_SONNET_INPUT_PRICE
                + output_tokens * settings.LLM_SONNET_OUTPUT_PRICE
            ) / 1_000_000
        return round(cost, 6)
