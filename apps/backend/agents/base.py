"""AgentBase — thin assembler (backwards-compat façade).

Implementation is split across sub-modules in ``apps/backend/agents/_base/``:

* ``_llm_mixin.py``     — ``_LlmMixin``     (LLM calls, retry)
* ``_tools_mixin.py``   — ``_ToolsMixin``   (_call_tool, spawn_subagent)
* ``_logging_mixin.py`` — ``_LoggingMixin`` (execute, _log_step, _broadcast, static helpers)
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, ClassVar, Coroutine

import anthropic

from apps.backend.agents._base._llm_mixin import _LlmMixin
from apps.backend.agents._base._logging_mixin import _LoggingMixin
from apps.backend.agents._base._tools_mixin import _ToolsMixin

if TYPE_CHECKING:
    from apps.backend.core.memory import MemoryManager
    from apps.backend.core.models import AgentCard, AgentResult, AgentTask


class AgentBase(_LoggingMixin, _ToolsMixin, _LlmMixin, ABC):
    """Base comune per Research, Design, Publisher, Analytics, CustomerService, Finance."""

    card: ClassVar[AgentCard]  # ogni subclass DEVE definire questa ClassVar

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
        self._counters_lock: asyncio.Lock = asyncio.Lock()

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
