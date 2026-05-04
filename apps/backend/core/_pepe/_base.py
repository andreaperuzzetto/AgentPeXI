"""Base class for Pepe — init, startup, cost estimation, agent registry."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Literal

import anthropic
import openai

from apps.backend.core.config import MODEL_SONNET, MODEL_HAIKU, settings
from apps.backend.core.domains import DomainContext, PersonalLayer, PERSONAL_LAYER
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentCard, AgentResult, AgentStatus, AgentTask, TaskStatus
from apps.backend.agents.base import AgentBase

logger = logging.getLogger("agentpexi.pepe")


class PepeBase:
    """Base class: __init__, startup, cost estimation, agent registry."""

    def __init__(
        self,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        active_domain: DomainContext | None = None,
    ) -> None:
        self.memory = memory
        self._ws_broadcast = ws_broadcaster
        self.domain = active_domain
        self._business_domain: DomainContext | None = None
        self._personal_layer: PersonalLayer = PERSONAL_LAYER
        self._agent_cards: dict[str, AgentCard] = {}

        # Anthropic client (Etsy domain — Sonnet)
        self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

        # Ollama client (Personal domain — local, zero cost)
        self._local_client = openai.AsyncOpenAI(
            base_url=settings.OLLAMA_BASE_URL,
            api_key="ollama",  # placeholder — Ollama non richiede auth
        )

        # Agent registry: {name: AgentBase instance}
        self._agents: dict[str, AgentBase] = {}
        self._agent_status: dict[str, AgentStatus] = {}

        # Task queue + semaforo parallelismo
        self._queue: asyncio.Queue[AgentTask] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(settings.MAX_PARALLEL_TASKS)

        # Futures per attendere risultati dei task
        self._pending_futures: dict[str, asyncio.Future[AgentResult]] = {}

        # Worker tasks
        self._workers: list[asyncio.Task] = []

        # Callback notifiche Telegram (impostato dal bot module)
        self._telegram_notifier: Callable[[str, bool], Coroutine] | None = None
        self._reminder_notifier: Callable[[str], Coroutine] | None = None

        # Mock mode — attivabile via /mock Telegram
        self.mock_mode: bool = False

        # Urgency system — stato runtime
        self._last_watcher_app: str = ""
        self._urgency_medium_buffer: list[dict] = []
        self._medium_buffer_lock = asyncio.Lock()

    def _has_business_domain(self) -> bool:
        return self._business_domain is not None

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def start(self, num_workers: int = 3) -> None:
        """Avvia i worker della queue."""
        for i in range(num_workers):
            task = asyncio.create_task(self._worker_loop(i), name=f"pepe-worker-{i}")
            self._workers.append(task)
        logger.info("Pepe avviato con %d worker", num_workers)

    async def stop(self) -> None:
        """Ferma i worker gracefully."""
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("Pepe fermato")

    def _fire(self, coro: "Coroutine[Any, Any, Any]", name: str = "") -> asyncio.Task:
        """Schedula una coroutine fire-and-forget con logging delle eccezioni."""
        task = asyncio.create_task(coro, name=name or coro.__qualname__)
        task.add_done_callback(
            lambda t: logger.error("Background task '%s' fallito: %s", task.get_name(), t.exception())
            if not t.cancelled() and t.exception() else None
        )
        return task

    # ------------------------------------------------------------------
    # LLM wrapper tracciato — retry + cost logging + WebSocket
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_cost(
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> float:
        """Stima costo USD (mirror di AgentBase._estimate_cost)."""
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

    # ------------------------------------------------------------------
    # Registrazione agenti
    # ------------------------------------------------------------------

    def register_agent(self, name: str, agent: AgentBase) -> None:
        self._agents[name] = agent
        self._agent_status[name] = AgentStatus.IDLE
        # Indicizza la card per lookup rapido
        if hasattr(agent, 'card'):
            self._agent_cards[name] = agent.card
        logger.info("Agente registrato: %s (layer=%s, llm=%s)",
                    name,
                    getattr(agent, 'card', {}).layer if hasattr(agent, 'card') else 'unknown',
                    getattr(agent, 'card', {}).llm if hasattr(agent, 'card') else 'unknown')

    def get_agent_statuses(self) -> dict[str, str]:
        return {name: status.value for name, status in self._agent_status.items()}

    def resume_agent(self, name: str) -> bool:
        """Riattiva un agente sospeso per troppi errori."""
        if name in self._agent_status and self._agent_status[name] == AgentStatus.ERROR:
            self._agent_status[name] = AgentStatus.IDLE
            logger.info("Agente %s riattivato", name)
            return True
        return False

    def _get_agent_llm(self, agent_name: str) -> Literal['ollama', 'sonnet', 'haiku']:
        card = self._agent_cards.get(agent_name)
        return card.llm if card else 'sonnet'   # fallback sicuro

    def _agent_requires_clarification(self, agent_name: str, input_data: dict) -> list[str]:
        """Ritorna lista di campi mancanti richiesti dall'agente."""
        card = self._agent_cards.get(agent_name)
        if not card or not card.requires_clarification:
            return []
        return [f for f in card.requires_clarification if not input_data.get(f)]

    def _agent_requires_confirmation(self, agent_name: str) -> bool:
        card = self._agent_cards.get(agent_name)
        return card.requires_confirmation if card else False
