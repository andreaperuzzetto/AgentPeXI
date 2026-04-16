"""RecallAgent — ricerca nella memoria con CRAG + Autonomous RAG.

Input: {"query": "...", "time_from": "ISO8601|null", "time_to": "ISO8601|null", "context": "..."}

Pipeline (v2):
1. Multi-source search: screen_memory (15) + pepe_memory (5)
2. CRAG grading: ogni chunk classificato RELEVANT/IRRELEVANT via Ollama caveman
   → se < 3 rilevanti: query rewrite + retry (una volta sola)
3. Sintesi Ollama con contesto filtrato
4. Autonomous RAG stop: Ollama valuta se la risposta è completa
   → se NO: ricerca supplementare mirata + sintesi integrata
5. Ritorna risposta + sorgenti + metadata

Privacy totale: nessun dato schermo esce dal Mac.
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

# ------------------------------------------------------------------
# Prompt caveman per step interni (Ollama qwen3:8b)
# ------------------------------------------------------------------

_GRADE_SYSTEM = (
    "Grade chunk relevance to query.\n"
    "Output ONLY: RELEVANT or IRRELEVANT"
)

_REWRITE_SYSTEM = (
    "Rewrite query for better memory recall.\n"
    "Output ONLY the new query, italian, max 15 words."
)

_STOP_SYSTEM = (
    "Is this answer complete enough for the question?\n"
    "Output ONLY: YES or NO"
)

_SYNTHESIS_SYSTEM = (
    "Sei Pepe, assistente personale di Andrea. "
    "Hai accesso alla memoria di quello che Andrea ha visto sullo schermo e alle note del sistema. "
    "Rispondi in italiano, in modo conciso e naturale. "
    "Cita l'app e l'orario solo se rilevanti. "
    "Non inventare informazioni non presenti nel contesto."
)

# Soglia minima chunk rilevanti prima di tentare query rewrite
_MIN_RELEVANT = 3


class RecallAgent(AgentBase):
    """Ricerca multi-sorgente con CRAG + Autonomous RAG. Privacy totale — nessuna API esterna."""

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
    ) -> None:
        super().__init__(
            name="recall",
            model=settings.OLLAMA_MODEL,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        inp = task.input_data or {}
        query: str = inp.get("query", "").strip()
        time_from: str | None = inp.get("time_from")
        time_to: str | None = inp.get("time_to")
        hint: str = inp.get("context", "")   # es. last_app=Safari

        if not query:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "Parametro 'query' mancante"},
            )

        # ── Step 1: multi-source search ──────────────────────────────
        await self._log_step("search", f"Ricerca multi-sorgente: '{query[:60]}'")
        time_filter = self._build_time_filter(time_from, time_to)
        chunks = await self._multi_search(query, time_filter, n_screen=15, n_pepe=5)

        # ── Step 2: CRAG grading ─────────────────────────────────────
        await self._log_step("grade", f"CRAG grading su {len(chunks)} chunk")
        relevant = await self._grade_chunks(query, chunks)

        # Se < _MIN_RELEVANT → query rewrite + retry
        if len(relevant) < _MIN_RELEVANT:
            await self._log_step("rewrite", "Chunk insufficienti — riscrittura query")
            rewritten = await self._rewrite_query(query, hint)
            if rewritten and rewritten != query:
                extra = await self._multi_search(rewritten, time_filter, n_screen=15, n_pepe=5)
                extra_rel = await self._grade_chunks(rewritten, extra)
                # Unisci senza duplicati (by document text)
                existing_docs = {c.get("document", "") for c in relevant}
                for c in extra_rel:
                    if c.get("document", "") not in existing_docs:
                        relevant.append(c)
                        existing_docs.add(c.get("document", ""))

        # Nessun risultato dopo retry
        if not relevant:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.COMPLETED,
                output_data={
                    "response": "Non ho trovato nulla in memoria che corrisponda alla tua richiesta.",
                    "sources": [],
                    "results_found": 0,
                    "confidence": 0.3,
                },
            )

        # ── Step 3: sintesi ──────────────────────────────────────────
        await self._log_step("synthesize", f"Sintesi su {len(relevant)} chunk rilevanti")
        grouped = self._group_by_app(relevant)
        context = self._build_context(grouped)
        synthesis = await self._synthesize(query, context)

        # ── Step 4: Autonomous RAG stop condition ────────────────────
        await self._log_step("stop_check", "Verifica completezza risposta")
        is_complete = await self._check_stop(query, synthesis)

        if not is_complete:
            await self._log_step("supplement", "Ricerca supplementare per completare risposta")
            supp_query = f"dettagli aggiuntivi: {query}"
            supp_chunks = await self._multi_search(supp_query, time_filter, n_screen=8, n_pepe=3)
            supp_rel = await self._grade_chunks(supp_query, supp_chunks)
            if supp_rel:
                supp_ctx = self._build_context(self._group_by_app(supp_rel))
                synthesis = await self._synthesize_integrated(query, context, supp_ctx, synthesis)
                relevant.extend(supp_rel)

        # ── Step 5: output ───────────────────────────────────────────
        sources = [
            {
                "app": app,
                "timestamp": chunks_[0].get("metadata", {}).get("timestamp", ""),
                "chunks": len(chunks_),
                "source_type": chunks_[0].get("metadata", {}).get("source_type", "screen"),
            }
            for app, chunks_ in self._group_by_app(relevant).items()
        ]

        confidence = min(0.95, 0.5 + len(relevant) * 0.03)

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "response": synthesis,
                "sources": sources,
                "query": query,
                "results_found": len(relevant),
                "confidence": round(confidence, 2),
            },
        )

    # ------------------------------------------------------------------
    # Multi-source search
    # ------------------------------------------------------------------

    async def _multi_search(
        self,
        query: str,
        where: dict | None,
        n_screen: int = 15,
        n_pepe: int = 5,
    ) -> list[dict]:
        """Cerca su screen_memory e pepe_memory, unisce i risultati."""
        results: list[dict] = []

        # Screen memory (privacy locale)
        try:
            screen = await self.memory.search_screen_memory(
                query=query, n_results=n_screen, where=where
            )
            for r in (screen or []):
                r.setdefault("metadata", {})["source_type"] = "screen"
            results.extend(screen or [])
        except Exception as exc:
            logger.warning("search_screen_memory fallita: %s", exc)

        # Pepe memory (insights/note generali)
        try:
            pepe_res = await self.memory.query_insights(query, n_results=n_pepe)
            for r in (pepe_res or []):
                r.setdefault("metadata", {})["source_type"] = "notes"
                r.setdefault("metadata", {}).setdefault("app_name", "Note")
            results.extend(pepe_res or [])
        except Exception as exc:
            logger.warning("query_insights fallita: %s", exc)

        return results

    # ------------------------------------------------------------------
    # CRAG grading
    # ------------------------------------------------------------------

    async def _grade_chunks(self, query: str, chunks: list[dict]) -> list[dict]:
        """Classifica ogni chunk come RELEVANT/IRRELEVANT con Ollama caveman.

        Batch di 5 per limitare latenza. Ritorna solo i RELEVANT.
        """
        if not chunks:
            return []

        relevant: list[dict] = []
        for chunk in chunks:
            doc = chunk.get("document", "")[:400]
            if not doc.strip():
                continue
            try:
                verdict = await self._call_llm_ollama(
                    system=_GRADE_SYSTEM,
                    user=f"QUERY: {query[:150]}\nCHUNK: {doc}",
                    max_tokens=5,
                    temperature=0.0,
                )
                if verdict.strip().upper().startswith("RELEVANT"):
                    relevant.append(chunk)
            except Exception as exc:
                logger.debug("Grading chunk fallito: %s — incluso per fallback", exc)
                relevant.append(chunk)   # include on error (meglio troppo che poco)

        return relevant

    # ------------------------------------------------------------------
    # Query rewrite
    # ------------------------------------------------------------------

    async def _rewrite_query(self, original: str, hint: str = "") -> str:
        """Riscrive la query per migliorare il recall con Ollama."""
        try:
            user = f"ORIGINAL: {original}"
            if hint:
                user += f"\nHINT: {hint}"
            result = await self._call_llm_ollama(
                system=_REWRITE_SYSTEM,
                user=user,
                max_tokens=30,
                temperature=0.3,
            )
            return result.strip() or original
        except Exception as exc:
            logger.debug("Query rewrite fallito: %s", exc)
            return original

    # ------------------------------------------------------------------
    # Stop condition (Autonomous RAG)
    # ------------------------------------------------------------------

    async def _check_stop(self, query: str, answer: str) -> bool:
        """True se la risposta è completa, False se servono più dati."""
        try:
            result = await self._call_llm_ollama(
                system=_STOP_SYSTEM,
                user=f"QUESTION: {query[:150]}\nANSWER: {answer[:500]}",
                max_tokens=5,
                temperature=0.0,
            )
            return result.strip().upper().startswith("YES")
        except Exception as exc:
            logger.debug("Stop check fallito: %s — assumo YES", exc)
            return True   # fallback: non fare ricerca extra su errore

    # ------------------------------------------------------------------
    # Sintesi
    # ------------------------------------------------------------------

    async def _synthesize(self, query: str, context: str) -> str:
        """Prima sintesi dai chunk rilevanti."""
        messages = [{
            "role": "user",
            "content": (
                f"Domanda di Andrea: {query}\n\n"
                f"Memoria disponibile:\n\n{context}\n\n"
                "Rispondi basandoti solo su queste informazioni."
            ),
        }]
        try:
            return await self._call_llm(
                messages=messages,
                system_prompt=_SYNTHESIS_SYSTEM,
                max_tokens=1024,
                domain_name="personal",
            )
        except Exception as exc:
            logger.error("Sintesi Recall fallita: %s", exc)
            return f"Ho trovato {context.count('[App:')} sorgenti ma la sintesi non è disponibile."

    async def _synthesize_integrated(
        self,
        query: str,
        context_primary: str,
        context_supplement: str,
        draft_answer: str,
    ) -> str:
        """Sintesi integrata: combina risposta draft + dati supplementari."""
        messages = [{
            "role": "user",
            "content": (
                f"Domanda di Andrea: {query}\n\n"
                f"Prima risposta (incompleta):\n{draft_answer}\n\n"
                f"Dati supplementari trovati:\n\n{context_supplement}\n\n"
                "Integra le informazioni e dai una risposta completa e coerente."
            ),
        }]
        try:
            return await self._call_llm(
                messages=messages,
                system_prompt=_SYNTHESIS_SYSTEM,
                max_tokens=1024,
                domain_name="personal",
            )
        except Exception as exc:
            logger.error("Sintesi integrata Recall fallita: %s", exc)
            return draft_answer   # fallback: risposta parziale è meglio di niente

    # ------------------------------------------------------------------
    # Helpers statici
    # ------------------------------------------------------------------

    @staticmethod
    def _build_time_filter(time_from: str | None, time_to: str | None) -> dict | None:
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
        grouped: dict[str, list[dict]] = defaultdict(list)
        for r in results:
            app = r.get("metadata", {}).get("app_name", "Sconosciuta")
            grouped[app].append(r)
        for app in grouped:
            grouped[app].sort(key=lambda x: x.get("metadata", {}).get("timestamp", ""))
        return dict(grouped)

    @staticmethod
    def _build_context(grouped: dict[str, list[dict]]) -> str:
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

