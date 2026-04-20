"""ResearchPersonalAgent — ricerca web + sintesi Perplexity-style per dominio Personal.

Input:
  {"query": "...", "mode": "quick|deep"}

Pipeline (deep mode):
1. Query decomposition (Ollama caveman) → 2-3 sub-query
2. DuckDuckGo search per ogni sub-query (max 5 risultati ciascuna)
3. Estrazione testo da URL rilevanti (TextExtractor)
4. CRAG grading: filtra chunk non rilevanti
5. Sintesi Perplexity-style (Claude Haiku): sezioni numerate + citazioni [N]
6. Ritorna risposta + sources

Pipeline (quick mode):
1. Singola query DuckDuckGo (8 risultati)
2. Usa solo snippet (no estrazione full-page)
3. Sintesi diretta Haiku

Privacy: DuckDuckGo, nessun tracking, mai Tavily (riservato a Etsy).
Fail-safe: ogni step ritorna risultato parziale se fallisce — mai crash totale.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, ClassVar, Coroutine

import anthropic

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU, settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentCard, AgentResult, AgentTask, TaskStatus
from apps.backend.tools.text_extract import TextExtractor
from apps.backend.tools.web_search import WebSearchTool

logger = logging.getLogger("agentpexi.research_personal")

# ------------------------------------------------------------------
# Prompt caveman per step interni
# ------------------------------------------------------------------

_DECOMPOSE_SYSTEM = (
    "Split question into 3 search queries. Output ONLY:\n"
    "Q1: ...\n"
    "Q2: ...\n"
    "Q3: ..."
)

_GRADE_SYSTEM = "Relevant? Output ONLY: YES|NO"

_REWRITE_SYSTEM = "Rewrite search query to find better results. Output ONLY the new query."

_STOP_SYSTEM = (
    "Enough info to answer? Output ONLY: YES|NO\n"
    "If NO, output: NO\nMISSING: what specific aspect is missing"
)

# Prompt output Perplexity-style (user-facing, non caveman)
_SYNTHESIS_SYSTEM = (
    "Sei Pepe, assistente di Andrea. "
    "Produci una risposta strutturata in italiano nel formato:\n\n"
    "**[Titolo argomento]**\n\n"
    "Risposta diretta alla domanda in 2-3 frasi.\n\n"
    "**Punti chiave**\n"
    "• Punto 1 [1]\n"
    "• Punto 2 [2]\n"
    "...\n\n"
    "**Fonti**\n"
    "[1] Titolo — url\n"
    "[2] Titolo — url\n\n"
    "Regole: usa le citazioni [N] per ogni affermazione verificabile. "
    "Niente intro tipo 'Ecco cosa ho trovato'. "
    "Niente speculazioni non supportate dalle fonti. "
    "Se le fonti sono insufficienti, dillo esplicitamente."
)

_QUICK_SYNTHESIS_SYSTEM = (
    "Sei Pepe, assistente di Andrea. "
    "Rispondi in italiano in modo diretto e conciso (max 150 parole). "
    "Cita le fonti con [N] se rilevanti. Niente intro verbose."
)

# Massimo URL da cui estrarre il full-text per query
_MAX_URLS_TO_FETCH = 3
_MAX_CHARS_PER_URL = 5_000


class ResearchPersonalAgent(AgentBase):
    """Ricerca web + sintesi strutturata per dominio Personal. DuckDuckGo, mai Tavily."""

    card: ClassVar[AgentCard] = AgentCard(
        name="research_personal",
        description="Ricerca web con Tavily, sintesi locale via Ollama, nessun contesto business",
        input_schema={"query": "str", "depth": "quick|deep"},
        layer="personal",
        llm="ollama",
        confidence_threshold=0.90,
    )

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        web_search: WebSearchTool | None = None,
    ) -> None:
        super().__init__(
            name="research_personal",
            model=MODEL_HAIKU,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )
        self._search = web_search or WebSearchTool()
        self._extractor = TextExtractor(max_chars=_MAX_CHARS_PER_URL)

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        inp = task.input_data or {}
        query: str = inp.get("query", "").strip()
        # "depth" è il nome spec; accetta anche "mode" per compatibilità bot
        depth: str = inp.get("depth", inp.get("mode", "quick"))

        if not query:
            return self._fail("Parametro 'query' mancante")

        if depth == "deep":
            return await self._deep_search(query, task.task_id)
        else:
            return await self._quick_search(query, task.task_id)

    # ------------------------------------------------------------------
    # Quick mode
    # ------------------------------------------------------------------

    async def _quick_search(self, query: str, task_id: str) -> AgentResult:
        """Ricerca rapida: singola query + sintesi su snippet."""
        await self._log_step("search", f"Ricerca DuckDuckGo: '{query[:60]}'")
        results = await self._search.search(query, max_results=8, provider="duckduckgo")

        if not results:
            return self._fail("Nessun risultato DuckDuckGo per la query")

        await self._log_step("synthesize", f"Sintesi su {len(results)} risultati")
        context = self._format_snippets(results)
        synthesis = await self._synthesize_quick(query, context, results)

        # Step 6 — salvataggio pepe_memory
        await self._save_to_memory(query, synthesis, "quick", results[:5])

        # Step 7 — personal_learning
        await self._update_learning(query)

        return AgentResult(
            task_id=task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "response": synthesis,
                "sources": [{"title": r.get("title", ""), "url": r.get("url", "")} for r in results[:5]],
                "query": query,
                "depth": "quick",
                "confidence": 0.75,
            },
        )

    # ------------------------------------------------------------------
    # Deep mode
    # ------------------------------------------------------------------

    async def _deep_search(self, query: str, task_id: str) -> AgentResult:
        """Ricerca approfondita: decomposizione + full-text + CRAG + sintesi Perplexity."""

        # ── Step 1: query decomposition ──────────────────────────────
        await self._log_step("decompose", "Decomposizione query con Ollama")
        sub_queries = await self._decompose_query(query)
        if not sub_queries:
            sub_queries = [query]

        # ── Step 2: search per ogni sub-query ────────────────────────
        await self._log_step("search", f"DuckDuckGo per {len(sub_queries)} sub-query")
        all_results: list[dict] = []
        seen_urls: set[str] = set()

        for sq in sub_queries:
            res = await self._search.search(sq, max_results=5, provider="duckduckgo")
            for r in res:
                if r.get("url") and r["url"] not in seen_urls:
                    all_results.append(r)
                    seen_urls.add(r["url"])

        if not all_results:
            return self._fail("Nessun risultato trovato per nessuna sub-query")

        # ── Step 3: estrazione full-text dai migliori URL ────────────
        await self._log_step("extract", f"Estrazione testo da {_MAX_URLS_TO_FETCH} URL")
        enriched = await self._enrich_with_fulltext(all_results[:_MAX_URLS_TO_FETCH])
        # Aggiungi risultati rimanenti solo con snippet
        for r in all_results[_MAX_URLS_TO_FETCH:]:
            if "full_text" not in r:
                r["full_text"] = r.get("snippet", "")
        enriched.extend(all_results[_MAX_URLS_TO_FETCH:])

        # ── Step 4: CRAG grading ─────────────────────────────────────
        await self._log_step("grade", f"CRAG grading su {len(enriched)} fonti")
        relevant = await self._grade_sources(query, enriched)
        if not relevant:
            relevant = enriched   # fallback: usa tutto se grading elimina tutto

        # ── Step 4b: stop condition ──────────────────────────────────
        await self._log_step("stop_check", "Verifica se le info sono sufficienti")
        missing_aspect = await self._check_stop_condition(query, relevant)
        if missing_aspect:
            await self._log_step("followup", f"Follow-up: {missing_aspect[:60]}")
            followup_results = await self._search.search(
                missing_aspect, max_results=5, provider="duckduckgo"
            )
            for r in followup_results:
                if r.get("url") not in seen_urls:
                    r["full_text"] = r.get("snippet", "")
                    relevant.append(r)
                    seen_urls.add(r["url"])

        # ── Step 5: sintesi Perplexity-style ─────────────────────────
        await self._log_step("synthesize", f"Sintesi strutturata su {len(relevant)} fonti")
        synthesis = await self._synthesize_perplexity(query, relevant)

        sources = [
            {"title": r.get("title", ""), "url": r.get("url", "")}
            for r in relevant[:8]
        ]

        # Step 6 — salvataggio pepe_memory
        await self._save_to_memory(query, synthesis, "deep", relevant[:8])

        # Step 7 — personal_learning
        await self._update_learning(query)

        confidence = min(0.92, 0.6 + len(relevant) * 0.04)

        return AgentResult(
            task_id=task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "response": synthesis,
                "sources": sources,
                "query": query,
                "sub_queries": sub_queries,
                "depth": "deep",
                "confidence": round(confidence, 2),
            },
        )

    # ------------------------------------------------------------------
    # Query decomposition
    # ------------------------------------------------------------------

    async def _decompose_query(self, query: str) -> list[str]:
        """Decompone query in 2-3 sub-query via Ollama caveman."""
        try:
            raw = await self._call_llm_ollama(
                system=_DECOMPOSE_SYSTEM,
                user=query,
                max_tokens=80,
                temperature=0.2,
            )
            sub_queries = []
            for line in raw.strip().splitlines():
                line = line.strip()
                # Formato spec: "Q1: ...", "Q2: ...", "Q3: ..."
                # Fallback: "1. ...", "- ...", "• ..."
                for prefix in ("Q1:", "Q2:", "Q3:", "1.", "2.", "3.", "-", "•"):
                    if line.upper().startswith(prefix.upper()):
                        line = line[len(prefix):].strip()
                        break
                if line and len(line) > 5:
                    sub_queries.append(line)
            return sub_queries[:3]   # max 3
        except Exception as exc:
            logger.debug("Query decomposition fallita: %s — uso query originale", exc)
            return []

    # ------------------------------------------------------------------
    # Full-text extraction
    # ------------------------------------------------------------------

    async def _enrich_with_fulltext(self, results: list[dict]) -> list[dict]:
        """Estrae full-text da URL in parallelo, aggiunge a ogni result."""
        async def _fetch_one(r: dict) -> dict:
            url = r.get("url", "")
            if not url:
                r["full_text"] = r.get("snippet", "")
                return r
            try:
                text = await self._extractor.from_url(url)
                r["full_text"] = text or r.get("snippet", "")
            except Exception:
                r["full_text"] = r.get("snippet", "")
            return r

        enriched = await asyncio.gather(
            *[_fetch_one(r) for r in results],
            return_exceptions=False,
        )
        return list(enriched)

    # ------------------------------------------------------------------
    # CRAG grading
    # ------------------------------------------------------------------

    async def _grade_sources(self, query: str, sources: list[dict]) -> list[dict]:
        """Filtra le fonti per rilevanza con Ollama caveman (chiamate in parallelo)."""
        async def _grade_one(src: dict) -> dict | None:
            content = (src.get("full_text") or src.get("snippet", ""))[:400]
            if not content.strip():
                return None
            try:
                verdict = await self._call_llm_ollama(
                    system=_GRADE_SYSTEM,
                    user=f"QUERY: {query[:150]}\nCHUNK: {content}",
                    max_tokens=5,
                    temperature=0.0,
                )
                if verdict.strip().upper().startswith("YES"):
                    return src
                return None
            except Exception:
                return src  # fail-open

        results = await asyncio.gather(*(_grade_one(s) for s in sources))
        return [s for s in results if s is not None]

    # ------------------------------------------------------------------
    # Sintesi
    # ------------------------------------------------------------------

    async def _synthesize_perplexity(self, query: str, sources: list[dict]) -> str:
        """Sintesi strutturata Perplexity-style con Claude Haiku."""
        # Costruisce contesto numerato per le citazioni
        context_parts = []
        for i, src in enumerate(sources[:8], 1):
            title = src.get("title", "Fonte")
            url = src.get("url", "")
            text = (src.get("full_text") or src.get("snippet", ""))[:600]
            context_parts.append(f"[{i}] {title}\nURL: {url}\n{text}")
        context = "\n\n---\n\n".join(context_parts)

        messages = [{
            "role": "user",
            "content": f"Domanda: {query}\n\nFonti:\n\n{context}",
        }]
        try:
            return await self._call_llm(
                messages=messages,
                system_prompt=_SYNTHESIS_SYSTEM,
                max_tokens=900,
                domain_name="personal",
            )
        except Exception as exc:
            logger.warning("Sintesi Haiku fallita, provo Ollama: %s", exc)
            return await self._call_llm_ollama(
                system=_SYNTHESIS_SYSTEM,
                user=f"Domanda: {query}\n\nFonti:\n\n{context[:5000]}",
                max_tokens=600,
                temperature=0.3,
            )

    async def _synthesize_quick(
        self, query: str, context: str, results: list[dict]
    ) -> str:
        """Sintesi rapida su snippet per quick mode."""
        messages = [{
            "role": "user",
            "content": f"Domanda: {query}\n\nRisultati:\n\n{context}",
        }]
        try:
            return await self._call_llm(
                messages=messages,
                system_prompt=_QUICK_SYNTHESIS_SYSTEM,
                max_tokens=400,
                domain_name="personal",
            )
        except Exception as exc:
            logger.warning("Quick synthesis fallita: %s", exc)
            # Fallback diretto: unisci i migliori snippet
            snippets = "\n".join(
                f"• {r['title']}: {r['snippet'][:100]}" for r in results[:4]
            )
            return f"Risultati per «{query}»:\n{snippets}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_snippets(results: list[dict]) -> str:
        parts = []
        for i, r in enumerate(results[:8], 1):
            parts.append(f"[{i}] {r.get('title', '')}\n{r.get('snippet', '')}\n{r.get('url', '')}")
        return "\n\n".join(parts)

    async def _check_stop_condition(self, query: str, relevant: list[dict]) -> str | None:
        """Step 4 stop condition — ritorna l'aspetto mancante (stringa) o None se info sufficienti."""
        if not relevant:
            return None
        sample = " ".join(
            (r.get("full_text") or r.get("snippet", ""))[:100] for r in relevant[:3]
        )
        try:
            raw = await self._call_llm_ollama(
                system=_STOP_SYSTEM,
                user=f"Question: {query}\nSnippets available: {len(relevant)}\nSample: {sample[:400]}",
                max_tokens=60,
                temperature=0.0,
            )
            raw = (raw or "").strip()
            if raw.upper().startswith("NO"):
                # Cerca "MISSING: ..." nella risposta
                for line in raw.splitlines():
                    if line.upper().startswith("MISSING:"):
                        return line[len("MISSING:"):].strip()
                return f"dettagli aggiuntivi su: {query}"
        except Exception as exc:
            logger.debug("_check_stop_condition fallito (fail-open): %s", exc)
        return None  # YES o errore → procede

    async def _save_to_memory(
        self, query: str, synthesis: str, depth: str, sources: list[dict]
    ) -> None:
        """Step 6 — salva la sintesi in pepe_memory ChromaDB."""
        from datetime import datetime as _dt
        try:
            await self.memory.store_insight(
                synthesis,
                metadata={
                    "query": query,
                    "tag": "research_personal",
                    "depth": depth,
                    "sources": [r.get("url", "") for r in sources if r.get("url")],
                    "date": _dt.utcnow().strftime("%Y-%m-%d"),
                    "created_at": _dt.utcnow().isoformat(),
                },
            )
        except Exception as exc:
            logger.debug("_save_to_memory fallito (fail-safe): %s", exc)

    async def _update_learning(self, query: str) -> None:
        """Step 7 — aggiorna personal_learning con il topic della query."""
        # Usa le prime 4 parole (al max) come topic significativo, non solo la prima
        words = query.split()
        topic = "_".join(w.lower() for w in words[:4]) if words else "research"
        try:
            await self.memory.upsert_learning(
                agent="research_personal",
                pattern_type="topic",
                pattern_value=topic,
                signal_type="implicit_repeated",
                weight_delta=0.05,
            )
        except Exception as exc:
            logger.debug("_update_learning fallito (fail-safe): %s", exc)

    def _fail(self, reason: str) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            task_id=self._task_id,
            status=TaskStatus.FAILED,
            output_data={"error": reason},
        )
