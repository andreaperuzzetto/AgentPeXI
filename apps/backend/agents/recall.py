"""RecallAgent — ricerca nella memoria schermo (ChromaDB screen_memory).

Input: {"query": "cosa stavo leggendo?", "time_from": "ISO8601|null", "time_to": "ISO8601|null"}

Pipeline:
1. Costruisce filtro temporale ChromaDB dai parametri
2. Similarity search su screen_memory
3. Raggruppa chunks per app + finestra temporale
4. Ollama sintetizza i risultati in risposta coerente
5. Restituisce risposta + metadata (app, orario, confidence)

Tutto locale: nessun dato esce dal Mac.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Coroutine

import anthropic

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus

logger = logging.getLogger("agentpexi.recall")


class RecallAgent(AgentBase):
    """Ricerca nella screen_memory con Ollama. Privacy totale — nessuna API esterna."""

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
    ) -> None:
        super().__init__(
            name="recall",
            model=settings.OLLAMA_MODEL,   # Ollama — mai Claude per i dati schermo
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        inp = task.input_data or {}
        query: str = inp.get("query", "")
        time_from: str | None = inp.get("time_from")
        time_to: str | None = inp.get("time_to")

        if not query:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "Parametro 'query' mancante"},
            )

        # --- Step 1: costruisce filtro temporale ---
        await self._log_step("search", f"Ricerca screen_memory: '{query[:60]}'")
        where = self._build_time_filter(time_from, time_to)

        # --- Step 2: similarity search ---
        results = await self.memory.search_screen_memory(
            query=query,
            n_results=15,
            where=where,
        )

        if not results:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.COMPLETED,
                output_data={
                    "response": "Non ho trovato nulla in memoria schermo che corrisponda alla tua richiesta.",
                    "sources": [],
                },
            )

        # --- Step 3: raggruppa per app + finestra temporale ---
        grouped = self._group_by_app(results)

        # --- Step 4: sintesi con Ollama ---
        context = self._build_context(grouped)
        synthesis = await self._synthesize(query, context)

        # --- Step 5: restituisce risposta + metadata sorgenti ---
        sources = [
            {
                "app": app,
                "timestamp": chunks[0]["metadata"].get("timestamp", ""),
                "chunks": len(chunks),
            }
            for app, chunks in grouped.items()
        ]

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "response": synthesis,
                "sources": sources,
                "query": query,
                "results_found": len(results),
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_time_filter(time_from: str | None, time_to: str | None) -> dict | None:
        """Costruisce il filtro ChromaDB $and/$gte/$lte sui timestamp."""
        conditions = []
        if time_from:
            conditions.append({"timestamp": {"$gte": time_from}})
        if time_to:
            conditions.append({"timestamp": {"$lte": time_to}})
        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    @staticmethod
    def _group_by_app(results: list[dict]) -> dict[str, list[dict]]:
        """Raggruppa i risultati per app_name."""
        grouped: dict[str, list[dict]] = defaultdict(list)
        for r in results:
            app = r.get("metadata", {}).get("app_name", "Sconosciuta")
            grouped[app].append(r)
        # Ordina per timestamp dentro ogni gruppo
        for app in grouped:
            grouped[app].sort(
                key=lambda x: x.get("metadata", {}).get("timestamp", ""),
            )
        return dict(grouped)

    @staticmethod
    def _build_context(grouped: dict[str, list[dict]]) -> str:
        """Formatta i risultati raggruppati in testo per il prompt Ollama."""
        parts = []
        for app, chunks in grouped.items():
            ts = chunks[0].get("metadata", {}).get("timestamp", "")
            try:
                ts_fmt = datetime.fromisoformat(ts).strftime("%d/%m/%Y %H:%M") if ts else ""
            except ValueError:
                ts_fmt = ts
            texts = "\n".join(c.get("document", "") for c in chunks[:5])
            parts.append(f"[App: {app} — {ts_fmt}]\n{texts}")
        return "\n\n---\n\n".join(parts)

    async def _synthesize(self, query: str, context: str) -> str:
        """Sintetizza i risultati con Ollama in risposta coerente."""
        system = (
            "Sei Pepe, assistente personale di Andrea. "
            "Hai accesso alla memoria di quello che Andrea ha visto sullo schermo. "
            "Rispondi in italiano, in modo conciso e naturale. "
            "Cita l'app e l'orario solo se rilevanti. "
            "Non inventare informazioni non presenti nel contesto."
        )
        messages = [
            {
                "role": "user",
                "content": (
                    f"Domanda di Andrea: {query}\n\n"
                    f"Quello che ho in memoria schermo:\n\n{context}\n\n"
                    "Rispondi alla domanda basandoti solo su queste informazioni."
                ),
            }
        ]
        try:
            response = await self._call_llm(
                messages=messages,
                system_prompt=system,
                max_tokens=1024,
                domain_name="personal",   # → routing Ollama
            )
            return response
        except Exception as exc:
            logger.error("RecallAgent sintesi fallita: %s", exc)
            # Fallback: risposta grezza senza sintesi
            lines = []
            for app, info in [("contesto trovato", {"document": context[:500]})]:
                lines.append(f"Ho trovato riferimenti in memoria ma la sintesi Ollama non è disponibile: {exc}")
            return lines[0] if lines else "Errore nella sintesi."
