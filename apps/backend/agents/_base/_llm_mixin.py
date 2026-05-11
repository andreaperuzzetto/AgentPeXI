"""AgentBase — LLM call mixin (Anthropic + Haiku routing, retry logic)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import anthropic
import openai

from apps.backend.core.config import MODEL_HAIKU, settings
from apps.backend.core.type_aliases import LLMMessage

if TYPE_CHECKING:
    from apps.backend.agents._base._protocols import AgentCoreProtocol

# ── Ollama module-level singleton (CNC-024) ───────────────────────────────────

_OLLAMA_CLIENT: openai.AsyncOpenAI | None = None
_OLLAMA_CLIENT_LOCK = asyncio.Lock()


async def _get_ollama_client() -> openai.AsyncOpenAI:
    """Return the module-level Ollama-compatible client, creating it on first use."""
    global _OLLAMA_CLIENT
    async with _OLLAMA_CLIENT_LOCK:
        if _OLLAMA_CLIENT is None:
            _OLLAMA_CLIENT = openai.AsyncOpenAI(
                base_url=settings.OLLAMA_BASE_URL,
                api_key="ollama",
            )
    return _OLLAMA_CLIENT


class _LlmMixin:
    """Mixin: LLM calls via Anthropic (Sonnet/Haiku) with retry."""

    async def _call_llm(
        self: AgentCoreProtocol,
        messages: list[LLMMessage],
        system_prompt: str | None = None,
        model_override: str | None = None,
        max_tokens: int = 4096,
        domain_name: str = "etsy_store",
    ) -> str:
        """Chiama LLM con routing automatico: Haiku per dominio personal, Sonnet altrimenti.

        Args:
            domain_name: Nome del dominio attivo (es. 'personal', 'etsy_store').
                         Se 'personal' → Haiku (economico, affidabile).
                         Altrimenti → Anthropic Sonnet (comportamento invariato).
        """
        # Mock mode — ritorna stub immediato, zero costi LLM
        if getattr(self.memory, "mock_mode", False):
            stub = json.dumps({
                "mock": True,
                "viability": 0.75,
                "final_score": 0.75,
                "confidence": "medium",
                "niche": "mock_niche",
                "product_type": "digital_print",
                "keywords": ["mock keyword"],
                "rationale": "[MOCK MODE] Risposta simulata — nessuna chiamata LLM reale.",
                "differentiation": "Mock differentiation",
                "target_audience": "Mock audience",
                "pricing": {"suggested_eur": 3.50},
                "color_schemes": ["#FFFFFF", "#000000"],
                "thumbnail_style": "mock style",
                "why_winner": "Mock winner selection",
                "score": 0.75,
            })
            return stub

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

        cost_usd = self._estimate_cost(model, input_tokens, output_tokens, cache_read, cache_write)

        response_text = response.content[0].text if response.content else ""

        step_id = await self._log_step(
            step_type="llm_call",
            description=f"LLM {model} ({input_tokens}+{output_tokens} tok)",
            input_data={"system_prompt": system_prompt, "messages": messages},
            output_data={"response": response_text[:500]},
            duration_ms=duration_ms,
        )

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

        async with self._counters_lock:
            self._llm_call_count += 1
            self._total_cost += cost_usd
            self._total_tokens += input_tokens + output_tokens

        return response_text

    async def _call_llm_ollama(
        self: AgentCoreProtocol,
        messages: list[LLMMessage] | None = None,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        *,
        system: str | None = None,
        user: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """Wrapper compatibile con la vecchia interfaccia Ollama — ora usa Claude Haiku.

        Mantiene la stessa firma per non modificare remind, research_personal,
        summarize e qualsiasi altro agente che la chiama. Internamente passa
        su Anthropic Haiku, che è affidabile, veloce ed economico (~€0,001/call).

        Supporta due convenzioni di chiamata:
          1. messages/system_prompt (stile _call_llm)
          2. system/user/temperature (stile caveman, usato dagli agenti Personal)
        """
        # Risolve i parametri convenience (system/user) in messages/system_prompt
        effective_system = system_prompt or system
        effective_messages: list[LLMMessage] = (
            messages if messages is not None
            else ([{"role": "user", "content": user}] if user else [])
        )

        t0 = time.monotonic()
        model = MODEL_HAIKU

        response = await self._llm_with_retry(
            model=model,
            messages=effective_messages,
            system_prompt=effective_system,
            max_tokens=max_tokens,
        )

        duration_ms = int((time.monotonic() - t0) * 1000)
        usage = response.usage
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost_usd = self._estimate_cost(model, input_tokens, output_tokens, cache_read, cache_write)

        response_text = response.content[0].text if response.content else ""

        step_id = await self._log_step(
            step_type="llm_call",
            description=f"Haiku {model} ({input_tokens}+{output_tokens} tok)",
            input_data={"system_prompt": effective_system, "messages": effective_messages},
            output_data={"response": response_text[:500]},
            duration_ms=duration_ms,
        )

        await self.memory.log_llm_call(
            task_id=self._task_id,
            step_id=step_id,
            agent_name=self.name,
            model=model,
            system_prompt=effective_system,
            messages=effective_messages,
            response=response_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            provider="haiku",
        )

        await self._broadcast({
            "type": "llm_call",
            "agent": self.name,
            "task_id": self._task_id,
            "step_id": step_id,
            "model": model,
            "provider": "haiku",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
        })

        async with self._counters_lock:
            self._llm_call_count += 1
            self._total_cost += cost_usd
            self._total_tokens += input_tokens + output_tokens

        return response_text

    async def _llm_with_retry(
        self: AgentCoreProtocol,
        model: str,
        messages: list[LLMMessage],
        system_prompt: str | None,
        max_tokens: int,
        max_retries: int = 3,
    ) -> anthropic.types.Message:
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
                return await self.client.messages.create(**kwargs)  # type: ignore[arg-type]
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

