"""ResearchAgent — analisi di mercato Etsy per nicchie di digital products."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, ClassVar

from apps.backend.agents._research.prompts import SYSTEM_PROMPT
from apps.backend.agents._research.context_mixin import _ResearchContextMixin
from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
from apps.backend.agents._research.warmup_mixin import WarmupOrchestratorMixin
from apps.backend.agents._research.analysis_mixin import _ResearchAnalysisMixin
from apps.backend.agents._research.validation_mixin import _ResearchValidationMixin
from apps.backend.agents._research.scoring_mixin import _ResearchScoringMixin
from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU, MODEL_SONNET
from apps.backend.core.models import AgentCard, AgentResult, AgentTask, TaskStatus
from apps.backend.tools import tavily as tavily_tool
from apps.backend.tools.trends import get_google_trends

logger = logging.getLogger("agentpexi.research")

__all__ = ["ResearchAgent", "SYSTEM_PROMPT"]


class ResearchAgent(
    _ResearchScoringMixin,
    _ResearchValidationMixin,
    _ResearchAnalysisMixin,
    _ResearchDiscoveryMixin,
    WarmupOrchestratorMixin,
    _ResearchContextMixin,
    AgentBase,
):
    """Agente specializzato in ricerca di mercato Etsy."""

    card: ClassVar[AgentCard] = AgentCard(
        name="research",
        description="Analisi nicchie Etsy: domanda, competizione, pricing, tag SEO, selling signals",
        input_schema={"niches": "list[str]", "product_type": "printable_pdf|digital_art_png|svg_bundle"},
        layer="business",
        llm="haiku",
        requires_clarification=["niches", "product_type"],
        confidence_threshold=0.85,
        pipeline_position=1,
    )

    def __init__(self, *, telegram_broadcaster: Callable | None = None, telegram_markup_sender: Callable | None = None, **kwargs: Any) -> None:
        super().__init__(name="research", model=MODEL_HAIKU, **kwargs)
        self._telegram_broadcast = telegram_broadcaster
        self._telegram_markup_sender = telegram_markup_sender
        self._entry_scorer = None   # lazy init — EntryPointScoring (step 1.5)

    async def _notify_telegram(self, message: str) -> None:
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(message)
            except Exception:
                logger.exception("Unexpected error")

    async def _get_entry_point_scorer(self):
        """
        Crea EntryPointScoring + MarketDataAgent leggendo mock_mode live da memory.mock_mode.
        Non viene cachato: mock_mode può cambiare a runtime via /mock on|off.
        """
        from apps.backend.agents.market_data import MarketDataAgent
        from apps.backend.core.entry_point_scoring import EntryPointScoring

        mock_mode = getattr(self.memory, "mock_mode", False)
        market_data = MarketDataAgent(memory=self.memory, mock_mode=mock_mode)
        self._entry_scorer = EntryPointScoring(
            memory=self.memory, market_data=market_data
        )
        return self._entry_scorer

    @staticmethod
    def _sanitize_prompt_input(value: str, max_len: int = 300) -> str:
        """Sanifica input utente prima dell'inserimento in un prompt LLM.

        Tronca alla lunghezza massima e rimuove sequenze tipiche di prompt injection.
        """
        import re
        value = value.strip()[:max_len]
        value = re.sub(
            r"(?i)(ignore\s+(previous|all|above|prior)\s+instructions?"
            r"|system\s*:|<\s*/?system\s*>|\[\s*system\s*\]"
            r"|assistant\s*:|<\s*/?assistant\s*>"
            r"|\\n---\\n|---END---|<\|im_end\|>|<\|im_start\|>)",
            "",
            value,
        )
        return value.strip()

    async def run(self, task: AgentTask) -> AgentResult:
        """Analizza nicchie Etsy e produce un report strutturato.

        Modalità:
        - mode="autonomous" o input vuoto → _autonomous_discovery() — Research
          decide autonomamente cosa produrre (data mining completo).
        - niches=[...] → analisi diretta delle nicchie indicate (usato da /niche).
        - query="..." → ricerca generica.
        """
        input_data = task.input_data or {}
        niches: list[str] = input_data.get("niches", [])
        query: str = input_data.get("query", "")
        mode: str = input_data.get("mode", "")

        # Modalità autonoma: Research decide cosa produrre senza input esterno
        if mode == "autonomous" or (not niches and not query):
            return await self._autonomous_discovery(task)

        # Fallback: se tutto vuoto usa qualsiasi stringa trovata nell'input
        if not niches and not query:
            for v in input_data.values():
                if isinstance(v, str) and v not in ("generic", "niche_analysis", "autonomous"):
                    query = v
                    break

        # Sanitizza gli input prima di qualsiasi uso nei prompt LLM
        niches = [self._sanitize_prompt_input(n) for n in niches if n]
        query = self._sanitize_prompt_input(query)

        if not niches and not query:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "Nessuna nicchia o query specificata nel task input."},
            )

        # Se c'è una query generica senza nicchie specifiche, usala direttamente
        if not niches and query:
            result = await self._single_research(task, query)
        elif len(niches) == 1:
            result = await self._single_niche_research(task, niches[0])
        else:
            result = await self._multi_niche_research(task, niches)

        # Notifica Telegram se completato con successo
        if result.status == TaskStatus.COMPLETED:
            _out = result.output_data or {}
            _subject = niches[0] if niches else query
            _summary = _out.get("summary", "")
            _tg_lines = [f"🔬 Ricerca Etsy: {_subject}"]
            if _summary:
                _tg_lines.append(f"{'─' * 28}\n{_summary}")
            await self._notify_telegram("\n".join(_tg_lines))

        return result
