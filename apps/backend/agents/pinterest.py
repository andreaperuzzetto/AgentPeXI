"""PinterestAgent — thin assembler per la Pinterest Machine.

Eredita:
- _WarmupMixin   — pipeline di warm-up a 5 fasi
- AgentBase      — LLM, tools, logging

AgentCard:
- name="pinterest", layer="business", pipeline_position=4
"""

from __future__ import annotations

import logging
from typing import Any, Callable, ClassVar, Coroutine

import anthropic

from apps.backend.agents._pinterest._generation_mixin import _GenerationMixin
from apps.backend.agents._pinterest._warmup_mixin import _WarmupMixin
from apps.backend.agents._pinterest._delivery_mixin import _DeliveryMixin
from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentCard, AgentResult, AgentTask, TaskStatus

logger = logging.getLogger("agentpexi.pinterest")

__all__ = ["PinterestAgent"]


class PinterestAgent(_WarmupMixin, _GenerationMixin, _DeliveryMixin, AgentBase):
    """Agente Pinterest: warm-up, generazione pin, scheduling e delivery."""

    card: ClassVar[AgentCard] = AgentCard(
        name="pinterest",
        description="Genera e distribuisce pin Pinterest da listing Etsy pubblicati",
        input_schema={"action": "str", "section_key": "str"},
        layer="business",
        llm="haiku",
        confidence_threshold=0.85,
        pipeline_position=4,
    )

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        telegram_broadcaster: Callable | None = None,
        pinterest_api: Any | None = None,
    ) -> None:
        super().__init__(
            name="pinterest",
            model=MODEL_HAIKU,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )
        self._telegram_broadcast = telegram_broadcaster
        self.pinterest_api = pinterest_api

    def _extra_init_kwargs(self) -> dict:
        return {"pinterest_api": self.pinterest_api}

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        data = task.input_data or {}
        action: str = data.get("action", "warmup")

        if action == "warmup":
            section_key: str = data.get("section_key", "")
            result = await self.run_warmup(section_key)
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.COMPLETED,
                output_data=result,
                reply_voice=f"Warmup Pinterest completato per la sezione '{section_key}'.",
            )

        logger.warning("[pinterest] action '%s' non supportata in questa versione", action)
        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={"message": f"Action '{action}' non supportata in questa versione."},
        )
