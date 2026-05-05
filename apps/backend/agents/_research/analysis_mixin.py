"""ResearchAgent — analysis mixin."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from apps.backend.agents._research.prompts import RESEARCH_SCHEMA_VERSION, SYSTEM_PROMPT
from apps.backend.core.config import MODEL_SONNET
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.tools import tavily as tavily_tool
from apps.backend.tools.trends import get_google_trends

logger = logging.getLogger("agentpexi.research")


class _ResearchAnalysisMixin:

    @staticmethod
    def _is_cache_valid(meta: dict) -> bool:
        """Return True only if cache entry is fresh (< 7 days) AND matches current schema version."""
        created_at_str = meta.get("created_at", "")
        if not created_at_str:
            return False
        try:
            from datetime import timedelta
            created_at = datetime.fromisoformat(created_at_str)
            # Normalise to naive UTC to handle both aware and naive ISO strings
            if created_at.tzinfo is not None:
                created_at = created_at.replace(tzinfo=None)
            age_ok = datetime.now(timezone.utc).replace(tzinfo=None) - created_at < timedelta(days=7)
        except (ValueError, TypeError):
            return False
        schema_ok = meta.get("schema_version") == RESEARCH_SCHEMA_VERSION
        return age_ok and schema_ok

    async def _single_research(self, task: AgentTask, query: str) -> AgentResult:
        """Ricerca generica basata su query libera — allineata a _single_niche_research."""
        # Step 0 — Failure analysis da ChromaDB
        failure_context = await self.memory.query_chromadb_recent(
            query=f"failure analysis {query}",
            n_results=3,
            where={"type": "failure_analysis"},
            primary_days=90,
            fallback_days=180,
        )
        failure_text = ""
        if failure_context:
            failure_text = "\n\n## Failure analysis passate per query simili\n"
            for fc in failure_context:
                failure_text += f"- {fc['document']}\n"

        # Step 0b — Contesto finanziario da Finance Agent
        finance_text = await self._read_finance_context(query)

        # Step 0c — Insight cross-domain da shared_memory (Personal ↔ Etsy)
        shared_text = await self._read_shared_context(query)

        # Step 1 — Ricerca parallela (4 chiamate)
        # return_exceptions=True: tool failure non propaga eccezione a execute().
        search_results, competitor_results, keyword_results, trend_data = await asyncio.gather(
            self._call_tool(
                tool_name="tavily",
                action="search",
                input_params={"query": query},
                fn=tavily_tool.search,
                query=query,
                max_results=10,
            ),
            self._call_tool(
                tool_name="tavily",
                action="search_competitors",
                input_params={"query": query},
                fn=tavily_tool.search_competitors,
                niche=query,
            ),
            self._call_tool(
                tool_name="tavily",
                action="search_keywords",
                input_params={"query": query},
                fn=tavily_tool.search_keywords,
                niche=query,
            ),
            self._call_tool(
                tool_name="google_trends",
                action="get_trends",
                input_params={"keyword": query},
                fn=get_google_trends,
                keyword=query,
            ),
            return_exceptions=True,
        )

        # Normalizza Exception → dict vuoto + log
        if isinstance(search_results, Exception):
            logger.warning("research[%s]: tavily.search fallito: %s", query, search_results)
            search_results = {}
        if isinstance(competitor_results, Exception):
            logger.warning("research[%s]: search_competitors fallito: %s", query, competitor_results)
            competitor_results = {}
        if isinstance(keyword_results, Exception):
            logger.warning("research[%s]: search_keywords fallito: %s", query, keyword_results)
            keyword_results = {}
        if isinstance(trend_data, Exception):
            logger.warning("research[%s]: get_google_trends fallito: %s", query, trend_data)
            trend_data = {}

        # Step 2 — Track data_sources
        data_sources = {
            "pricing": "blog_inference",
            "competitors": "blog_mention" if competitor_results else "llm_inference",
            "trend": "google_trends" if isinstance(trend_data, dict) and trend_data.get("percent_change") is not None and trend_data.get("source") == "google_trends" else "llm_inference",
            "keywords": "llm_inference",
        }

        # Step 3 — LLM analysis
        analysis = await self._call_llm(
            messages=[{
                "role": "user",
                "content": (
                    f"Analizza questi risultati di ricerca per il mercato Etsy digital products.\n\n"
                    f"Query: {query}\n\n"
                    f"## Risultati ricerca\n{json.dumps(search_results, indent=2, default=str)}\n\n"
                    f"## Dati competitor\n{json.dumps(competitor_results, indent=2, default=str)}\n\n"
                    f"## Dati keyword SEO\n{json.dumps(keyword_results, indent=2, default=str)}\n\n"
                    f"## Google Trends\n{json.dumps(trend_data, indent=2, default=str)}"
                    f"{failure_text}"
                    f"{finance_text}"
                    f"{shared_text}\n\n"
                    f"## Qualità dati disponibili\n{json.dumps(data_sources, indent=2)}\n"
                    f"Per i campi dove la fonte è 'llm_inference', indica uncertainty nella "
                    f"confidence e compila il campo con la migliore stima disponibile ma "
                    f"segnalalo in missing_data.\n\n"
                    f"Produci un report JSON completo seguendo la struttura indicata nel system prompt."
                ),
            }],
            system_prompt=SYSTEM_PROMPT,
        )

        # Step 4 — Parse e validazione
        output = await self._parse_and_validate(analysis, SYSTEM_PROMPT)
        if output is None:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "JSON parsing fallito dopo retry."},
            )

        # Fix 2 — Enforcement strutturato failure constraints
        output, violations = self._enforce_failure_constraints(output, failure_context)
        if violations:
            output["failure_constraints_applied"] = violations

        # Step 5 — Confidence
        confidence, missing_data = self._calculate_confidence(data_sources, output)
        output["confidence"] = confidence
        output["missing_data"] = missing_data
        output["data_sources"] = data_sources

        # Confidence gate: < 0.60 → secondo tentativo con query raffinate
        if confidence < 0.60:
            refined_output = await self._refine_low_confidence_research(
                niche=query,
                current_output=output,
                data_sources=data_sources,
                missing_data=missing_data,
                system_prompt=SYSTEM_PROMPT,
            )
            if refined_output is not None:
                output = refined_output
                refined_sources = output.get("data_sources", data_sources)
                confidence, missing_data = self._calculate_confidence(refined_sources, output)
                output["confidence"] = confidence
                output["missing_data"] = missing_data

        # Gate finale: < 0.50 → FAILED
        if confidence < 0.50:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={
                    "error": (
                        f"Dati insufficienti per query '{query}' dopo secondo tentativo. "
                        f"Confidence: {confidence:.2f}. "
                        f"Mancanti: {', '.join(missing_data)}. "
                        f"Azione richiesta: attendere Etsy API approval per dati reali."
                    ),
                    "confidence": confidence,
                    "missing_data": missing_data,
                    "partial_output": output,
                },
            )

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data=output,
            reply_voice="Ricerca completata, controlla il pannello.",
        )

    async def _single_niche_research(
        self, task: AgentTask, niche: str
    ) -> AgentResult:
        """Analisi approfondita di una singola nicchia."""
        # Legge market_context da EntryPointScoring (se presente)
        _input          = task.input_data or {}
        _market_context = _input.get("market_context", "")
        _entry_score    = _input.get("entry_score", 0.0)
        _market_block   = (
            f"\n\n{_market_context}" if _market_context else ""
        )

        # Step 0 — Cache check ChromaDB
        cached = await self.memory.query_chromadb(
            query=f"Research report per nicchia '{niche}'",
            n_results=1,
            where={"type": "research_report", "niche": niche},
        )
        use_cache = False
        cached_data = None
        if cached:
            meta = cached[0].get("metadata", {})
            if self._is_cache_valid(meta):
                use_cache = True
                cached_data = cached[0]

        # Step 0b — Failure analysis passate
        failure_context = await self.memory.query_chromadb_recent(
            query=f"failure analysis {niche}",
            n_results=3,
            where={"type": "failure_analysis"},
            primary_days=90,
            fallback_days=180,
        )
        failure_text = ""
        if failure_context:
            failure_text = "\n\n## Failure analysis passate per nicchie simili\n"
            for fc in failure_context:
                failure_text += f"- {fc['document']}\n"

        # Step 0c — Contesto finanziario da Finance Agent
        finance_text = await self._read_finance_context(niche)

        # Step 0d — Insight cross-domain da shared_memory (Personal ↔ Etsy)
        shared_text = await self._read_shared_context(niche)

        if use_cache and cached_data:
            # Solo Google Trends fresco — wrapped per sicurezza
            try:
                trend_data = await self._call_tool(
                    tool_name="google_trends",
                    action="get_trends",
                    input_params={"keyword": niche},
                    fn=get_google_trends,
                    keyword=niche,
                )
            except Exception as _gt_exc:
                logger.warning("research[%s]: google_trends (cache path) fallito: %s", niche, _gt_exc)
                trend_data = {}
            data_sources = {
                "pricing":     "cached",
                "competitors": "cached",
                "trend":       "google_trends" if isinstance(trend_data, dict) and trend_data.get("source") == "google_trends" else "llm_inference",
                "keywords":    "cached",
                "entry_point": "market_signals" if _market_context else "none",
            }

            analysis = await self._call_llm(
                messages=[{
                    "role": "user",
                    "content": (
                        f"Aggiorna l'analisi della nicchia Etsy: **{niche}** (digital products).\n\n"
                        f"## Dati cache (< 7 giorni)\n{cached_data['document']}\n\n"
                        f"## Google Trends aggiornato\n{json.dumps(trend_data, indent=2, default=str)}"
                        f"{_market_block}"
                        f"{failure_text}"
                        f"{finance_text}"
                        f"{shared_text}\n\n"
                        f"## Qualità dati disponibili\n{json.dumps(data_sources, indent=2)}\n\n"
                        f"Produci un report JSON completo seguendo la struttura indicata nel system prompt."
                    ),
                }],
                system_prompt=SYSTEM_PROMPT,
            )
        else:
            # Step 1 — Ricerca parallela (4 chiamate)
            # return_exceptions=True: se un tool fallisce non crasha l'intero gather.
            # _call_tool ri-rilancia eccezioni — senza questo le eccezioni Tavily
            # (network, API key) propagavano a execute() dando confidence=0.00.
            etsy_direct, competitor_results, keyword_results, trend_data = await asyncio.gather(
                self._call_tool(
                    tool_name="tavily",
                    action="search_etsy_direct",
                    input_params={"niche": niche},
                    fn=tavily_tool.search_etsy_direct,
                    niche=niche,
                ),
                self._call_tool(
                    tool_name="tavily",
                    action="search_competitors",
                    input_params={"niche": niche},
                    fn=tavily_tool.search_competitors,
                    niche=niche,
                ),
                self._call_tool(
                    tool_name="tavily",
                    action="search_keywords",
                    input_params={"niche": niche},
                    fn=tavily_tool.search_keywords,
                    niche=niche,
                ),
                self._call_tool(
                    tool_name="google_trends",
                    action="get_trends",
                    input_params={"keyword": niche},
                    fn=get_google_trends,
                    keyword=niche,
                ),
                return_exceptions=True,
            )

            # Normalizza Exception → dict vuoto + log
            if isinstance(etsy_direct, Exception):
                logger.warning("research[%s]: search_etsy_direct fallito: %s", niche, etsy_direct)
                etsy_direct = {}
            if isinstance(competitor_results, Exception):
                logger.warning("research[%s]: search_competitors fallito: %s", niche, competitor_results)
                competitor_results = {}
            if isinstance(keyword_results, Exception):
                logger.warning("research[%s]: search_keywords fallito: %s", niche, keyword_results)
                keyword_results = {}
            if isinstance(trend_data, Exception):
                logger.warning("research[%s]: get_google_trends fallito: %s", niche, trend_data)
                trend_data = {}

            # Step 2 — Track data_sources
            etsy_raw = etsy_direct.get("etsy_listings_raw", []) if isinstance(etsy_direct, dict) else []
            erank_raw = etsy_direct.get("erank_keyword_data", []) if isinstance(etsy_direct, dict) else []

            data_sources = {
                "pricing":     "etsy_extract" if etsy_raw else "blog_inference",
                "competitors": "etsy_extract" if etsy_raw else "blog_mention",
                "trend":       "google_trends" if isinstance(trend_data, dict) and trend_data.get("source") == "google_trends" else "llm_inference",
                "keywords":    "erank_content" if erank_raw else "llm_inference",
                "entry_point": "market_signals" if _market_context else "none",
            }

            # Step 3 — LLM analysis
            analysis = await self._call_llm(
                messages=[{
                    "role": "user",
                    "content": (
                        f"Analizza la nicchia Etsy: **{niche}** (digital products).\n\n"
                        f"## Dati Etsy reali (extract)\n{json.dumps(etsy_direct, indent=2, default=str)}\n\n"
                        f"## Dati competitor\n{json.dumps(competitor_results, indent=2, default=str)}\n\n"
                        f"## Dati keyword SEO\n{json.dumps(keyword_results, indent=2, default=str)}\n\n"
                        f"## Google Trends\n{json.dumps(trend_data, indent=2, default=str)}"
                        f"{_market_block}"
                        f"{failure_text}"
                        f"{finance_text}"
                        f"{shared_text}\n\n"
                        f"## Qualità dati disponibili\n{json.dumps(data_sources, indent=2)}\n"
                        f"Per i campi dove la fonte è 'llm_inference', indica uncertainty nella "
                        f"confidence e compila il campo con la migliore stima disponibile ma "
                        f"segnalalo in missing_data.\n\n"
                        f"Produci un report JSON completo seguendo la struttura indicata nel system prompt."
                    ),
                }],
                system_prompt=SYSTEM_PROMPT,
            )

        # Step 4 — Parse e validazione
        output = await self._parse_and_validate(analysis, SYSTEM_PROMPT)
        if output is None:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "JSON parsing fallito dopo retry."},
            )

        # Fix 2 — Enforcement strutturato failure constraints
        output, violations = self._enforce_failure_constraints(output, failure_context)
        if violations:
            output["failure_constraints_applied"] = violations

        # Step 5 — Confidence
        confidence, missing_data = self._calculate_confidence(data_sources, output)
        output["confidence"] = confidence
        output["missing_data"] = missing_data
        output["data_sources"] = data_sources

        # Confidence gate: < 0.60 → secondo tentativo con query raffinate
        if confidence < 0.60 and not use_cache:
            refined_output = await self._refine_low_confidence_research(
                niche=niche,
                current_output=output,
                data_sources=data_sources,
                missing_data=missing_data,
                system_prompt=SYSTEM_PROMPT,
            )
            if refined_output is not None:
                output = refined_output
                refined_sources = output.get("data_sources", data_sources)
                confidence, missing_data = self._calculate_confidence(refined_sources, output)
                output["confidence"] = confidence
                output["missing_data"] = missing_data

        # Gate finale: < 0.50 → FAILED
        if confidence < 0.50:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={
                    "error": (
                        f"Dati insufficienti per nicchia '{niche}' dopo secondo tentativo. "
                        f"Confidence: {confidence:.2f}. "
                        f"Mancanti: {', '.join(missing_data)}. "
                        f"Azione richiesta: attendere Etsy API approval per dati reali."
                    ),
                    "confidence": confidence,
                    "missing_data": missing_data,
                    "partial_output": output,
                },
            )

        # Step 6 — Salva in ChromaDB con metadata estesi
        summary = output.get("summary", "") if isinstance(output, dict) else str(output)
        first_viable = next((n for n in output.get("niches", []) if n.get("viable", True)), {})
        if summary:
            await self._call_tool(
                tool_name="chromadb",
                action="store_insight",
                input_params={"niche": niche},
                fn=self.memory.store_insight,
                text=f"Research report per nicchia '{niche}': {summary}",
                metadata={
                    "type": "research_report",
                    "niche": niche,
                    "agent": self.name,
                    "task_id": self._task_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "schema_version": RESEARCH_SCHEMA_VERSION,
                    "confidence": confidence,
                    "peak_months": str(first_viable.get("demand", {}).get("peak_months", [])),
                    "etsy_tags_13": json.dumps(first_viable.get("etsy_tags_13", [])[:13]),
                },
            )

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data=output,
            reply_voice="Ricerca completata, controlla il pannello.",
        )

    async def _multi_niche_research(
        self, task: AgentTask, niches: list[str]
    ) -> AgentResult:
        """Analizza più nicchie in parallelo tramite sub-agenti, poi sintetizza."""
        # Step 1 — Crea sub-task per ogni nicchia
        sub_tasks = [
            AgentTask(
                agent_name=self.name,
                input_data={"niches": [niche]},
                source=task.source,
            )
            for niche in niches
        ]

        # Step 2 — Esegui sub-agenti in parallelo con semaforo (max 3)
        sem = asyncio.Semaphore(3)

        async def _run_with_sem(st: AgentTask) -> AgentResult:
            async with sem:
                return await self.spawn_subagent(st)

        sub_results: list[AgentResult] = await asyncio.gather(
            *[_run_with_sem(st) for st in sub_tasks]
        )

        # Step 3 — Raccogli tutti i dati delle sotto-analisi
        all_niche_data = []
        failed_niches = []
        for r, niche in zip(sub_results, niches):
            if r.status == TaskStatus.COMPLETED and isinstance(r.output_data, dict):
                niche_list = r.output_data.get("niches", [])
                all_niche_data.extend(niche_list)
            else:
                failed_niches.append(niche)

        if not all_niche_data:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": f"Tutti i sub-agenti hanno fallito per le nicchie: {', '.join(niches)}."},
            )

        # Step 4 — Sintesi comparativa con Sonnet su dati COMPLETI.
        # Passa pricing, selling_signals, tags, entry_difficulty — non solo slim_summary.
        # Sonnet sceglie il vincitore con contesto completo.
        rec_prompt = (
            f"Hai analizzato {len(niches)} nicchie Etsy: {', '.join(niches)}.\n"
            f"Scegli quella con il massimo potenziale di vendita nei prossimi 30 giorni.\n\n"
            f"DATI COMPLETI:\n"
            f"{json.dumps(all_niche_data, indent=2, default=str)}\n\n"
            "Rispondi SOLO con questo JSON (niente altro):\n"
            "{\n"
            '  "summary": "raccomandazione esecutiva: quale nicchia perseguire subito e perché (2-3 frasi focalizzate su vendite)",\n'
            '  "recommended_niche": "nome della nicchia vincente",\n'
            '  "recommended_product_type": "printable_pdf|digital_art_png",\n'
            '  "recommended_next_steps": ["azione concreta 1", "azione concreta 2"],\n'
            '  "data_quality_warning": "stringa vuota se dati OK, altrimenti descrivi problemi"\n'
            "}"
        )
        rec_raw = await self._call_llm(
            messages=[{"role": "user", "content": rec_prompt}],
            system_prompt=None,
            model_override=MODEL_SONNET,
            max_tokens=1024,
        )
        rec_cleaned = rec_raw.strip()
        if rec_cleaned.startswith("```"):
            lines = rec_cleaned.split("\n")
            rec_cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
        try:
            rec = json.loads(rec_cleaned)
        except (json.JSONDecodeError, AttributeError):
            rec = {
                "summary": f"Analisi completata per {len(all_niche_data)} nicchie.",
                "recommended_next_steps": ["Valutare i dati per nicchia e procedere con la più viable."],
                "data_quality_warning": "",
            }

        dq_warning = rec.get("data_quality_warning", "")
        if failed_niches:
            prefix = f"Sub-agenti falliti per: {', '.join(failed_niches)}. "
            dq_warning = (prefix + dq_warning).strip()

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "niches": all_niche_data,
                "summary": rec.get("summary", ""),
                "recommended_next_steps": rec.get("recommended_next_steps", []),
                "data_quality_warning": dq_warning,
            },
            reply_voice="Ricerca completata, controlla il pannello.",
        )

    async def _refine_low_confidence_research(
        self,
        niche: str,
        current_output: dict,
        data_sources: dict,
        missing_data: list[str],
        system_prompt: str,
    ) -> dict | None:
        """
        Secondo tentativo di ricerca con query più specifiche per aumentare confidence.
        Chiamato solo quando confidence < 0.60.
        """
        refined_searches = []

        if data_sources.get("pricing") in ("blog_inference", "llm_inference"):
            refined_searches.append(
                self._call_tool(
                    tool_name="tavily",
                    action="search_pricing_refined",
                    input_params={"niche": niche},
                    fn=tavily_tool.search_etsy_pricing,
                    niche=niche,
                )
            )

        if data_sources.get("keywords") in ("llm_inference",):
            refined_searches.append(
                self._call_tool(
                    tool_name="tavily",
                    action="search_etsy_seo_community",
                    input_params={"niche": niche},
                    fn=tavily_tool.search_etsy_seo_community,
                    niche=niche,
                )
            )

        if not refined_searches:
            return None

        refined_results = await asyncio.gather(*refined_searches, return_exceptions=True)
        valid_results = [r for r in refined_results if not isinstance(r, Exception)]
        if not valid_results:
            return None

        refined_analysis = await self._call_llm(
            messages=[{
                "role": "user",
                "content": (
                    f"SECONDO TENTATIVO — La prima analisi di '{niche}' aveva confidence bassa.\n"
                    f"Dati mancanti: {', '.join(missing_data)}\n\n"
                    f"## Output precedente (da migliorare)\n"
                    f"{json.dumps(current_output, indent=2, default=str)}\n\n"
                    f"## Dati aggiuntivi raccolti\n"
                    f"{json.dumps(valid_results, indent=2, default=str)}\n\n"
                    f"Integra questi dati nell'analisi precedente. "
                    f"Aggiorna pricing, keyword e tag se trovi dati migliori. "
                    f"Produci JSON completo secondo la struttura del system prompt."
                ),
            }],
            system_prompt=system_prompt,
        )

        return await self._parse_and_validate(refined_analysis, system_prompt)
