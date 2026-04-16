"""ResearchAgent — analisi di mercato Etsy per nicchie di digital products."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_SONNET
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.tools import tavily as tavily_tool
from apps.backend.tools.trends import get_google_trends

SYSTEM_PROMPT = """\
Sei un venditore Etsy esperto con 5 anni di esperienza nei digital products.
Il tuo compito NON è analizzare il mercato — è decidere se e come entrare in una nicchia
per massimizzare le vendite reali, non la qualità dell'analisi.

Prima di tutto controlla le failure analysis passate:
- failure_type "no_views_no_sales": SCARTA la nicchia immediatamente, non recuperabile
- failure_type "no_views": problema di keyword/tag — puoi procedere MA devi cambiare completamente la tag strategy
- failure_type "no_conversion": problema di prezzo o descrizione — puoi procedere MA devi cambiare price point
- Il campo "avoid_in_future" è un divieto assoluto. Non puoi ignorarlo.

Per ogni nicchia valuta nell'ordine ESATTO:
1. È ancora redditizia? (domanda vs saturazione)
2. A che prezzo si vende DAVVERO? (non il range — il prezzo che converte)
3. Quali 13 tag Etsy esatti portano traffico? (non keyword generiche)
4. Che tipo di prodotto vuole il buyer? (non cosa è facile fare)
5. Quando pubblicare per il picco stagionale?
6. Cosa fanno i top seller che possiamo replicare?

Rispondi SEMPRE in JSON valido. Zero testo fuori dal JSON.

Schema OBBLIGATORIO:
{
  "niches": [
    {
      "name": "nome nicchia",
      "viable": true,
      "viability_reason": "perché è viable o perché è stata scartata",
      "demand": {
        "level": "high|medium|low",
        "trend": "growing|stable|declining",
        "seasonality": "descrizione stagionalità",
        "peak_months": [1, 2, 3],
        "publish_timing_advice": "Pubblica X settimane prima del picco"
      },
      "competition": {
        "level": "high|medium|low",
        "top_sellers": ["shop1", "shop2"],
        "avg_quality": "high|medium|low",
        "what_top_sellers_do": "descrizione specifica delle strategie vincenti",
        "gap_to_exploit": "cosa NON fanno i top seller che possiamo fare noi"
      },
      "pricing": {
        "min_usd": 0.0,
        "max_usd": 0.0,
        "avg_usd": 0.0,
        "conversion_sweet_spot_usd": 0.0,
        "launch_price_usd": 0.0,
        "mature_price_usd": 0.0,
        "price_reasoning": "perché questo prezzo converte meglio degli altri"
      },
      "keywords": ["keyword1", "keyword2"],
      "etsy_tags_13": [
        "tag 1 esatto",
        "tag 2 esatto",
        "tag 3 esatto",
        "tag 4 esatto",
        "tag 5 esatto",
        "tag 6 esatto",
        "tag 7 esatto",
        "tag 8 esatto",
        "tag 9 esatto",
        "tag 10 esatto",
        "tag 11 esatto",
        "tag 12 esatto",
        "tag 13 esatto"
      ],
      "tag_strategy": "perché questi 13 tag — mix di high-volume e long-tail",
      "recommended_product_type": "printable_pdf|digital_art_png|svg_bundle|mixed",
      "product_format_details": "specifiche esatte: A4/US Letter, pagine, formato, contenuto",
      "entry_difficulty": "low|medium|high",
      "selling_signals": {
        "thumbnail_style": "cosa funziona visivamente in questa nicchia (es: lifestyle mockup con scrivania, flat lay minimale, ecc.)",
        "conversion_triggers": ["elemento 1 che fa cliccare acquista", "elemento 2"],
        "bundle_vs_single": "bundle|single|both",
        "bundle_reasoning": "perché",
        "first_listing_recommendation": "descrizione esatta del primo prodotto da pubblicare"
      },
      "failure_analysis_applied": {
        "failures_found": 0,
        "actions_taken": ["azione basata su failure 1", "azione basata su failure 2"],
        "avoided": ["cosa specifico evitato grazie alle failure"]
      },
      "notes": "osservazioni critiche per il Design Agent e Publisher Agent"
    }
  ],
  "summary": "raccomandazione esecutiva: quale nicchia perseguire subito e perché",
  "recommended_next_steps": ["azione concreta 1", "azione concreta 2"],
  "data_quality_warning": "stringa vuota se dati buoni, altrimenti descrivi cosa manca e come impatta l'affidabilità"
}
"""


class ResearchAgent(AgentBase):
    """Agente specializzato in ricerca di mercato Etsy."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="research", model=MODEL_SONNET, **kwargs)

    async def run(self, task: AgentTask) -> AgentResult:
        """Analizza una o più nicchie Etsy e produce un report strutturato."""
        input_data = task.input_data or {}
        niches: list[str] = input_data.get("niches", [])
        query: str = input_data.get("query", "")

        # Fallback: se tutto vuoto usa qualsiasi stringa trovata nell'input
        if not niches and not query:
            for v in input_data.values():
                if isinstance(v, str) and v not in ("generic", "niche_analysis"):
                    query = v
                    break

        if not niches and not query:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "Nessuna nicchia o query specificata nel task input."},
            )

        # Se c'è una query generica senza nicchie specifiche, usala direttamente
        if not niches and query:
            return await self._single_research(task, query)

        # Se c'è una sola nicchia, ricerca diretta
        if len(niches) == 1:
            return await self._single_niche_research(task, niches[0])

        # Più nicchie → sub-agenti paralleli + sintesi
        return await self._multi_niche_research(task, niches)

    # ------------------------------------------------------------------
    # Ricerca singola query generica
    # ------------------------------------------------------------------

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

        # Step 1 — Ricerca parallela (4 chiamate)
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
        )

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
                    f"{failure_text}\n\n"
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
        )

    # ------------------------------------------------------------------
    # Ricerca singola nicchia
    # ------------------------------------------------------------------

    async def _single_niche_research(
        self, task: AgentTask, niche: str
    ) -> AgentResult:
        """Analisi approfondita di una singola nicchia."""
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
            created_at_str = meta.get("created_at", "")
            if created_at_str:
                try:
                    created_at = datetime.fromisoformat(created_at_str)
                    if datetime.utcnow() - created_at < timedelta(days=7):
                        use_cache = True
                        cached_data = cached[0]
                except (ValueError, TypeError):
                    pass

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

        if use_cache and cached_data:
            # Solo Google Trends fresco
            trend_data = await self._call_tool(
                tool_name="google_trends",
                action="get_trends",
                input_params={"keyword": niche},
                fn=get_google_trends,
                keyword=niche,
            )
            data_sources = {
                "pricing": "cached",
                "competitors": "cached",
                "trend": "google_trends" if isinstance(trend_data, dict) and trend_data.get("source") == "google_trends" else "llm_inference",
                "keywords": "cached",
            }

            analysis = await self._call_llm(
                messages=[{
                    "role": "user",
                    "content": (
                        f"Aggiorna l'analisi della nicchia Etsy: **{niche}** (digital products).\n\n"
                        f"## Dati cache (< 7 giorni)\n{cached_data['document']}\n\n"
                        f"## Google Trends aggiornato\n{json.dumps(trend_data, indent=2, default=str)}"
                        f"{failure_text}\n\n"
                        f"## Qualità dati disponibili\n{json.dumps(data_sources, indent=2)}\n\n"
                        f"Produci un report JSON completo seguendo la struttura indicata nel system prompt."
                    ),
                }],
                system_prompt=SYSTEM_PROMPT,
            )
        else:
            # Step 1 — Ricerca parallela (4 chiamate)
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
            )

            # Step 2 — Track data_sources
            etsy_raw = etsy_direct.get("etsy_listings_raw", []) if isinstance(etsy_direct, dict) else []
            erank_raw = etsy_direct.get("erank_keyword_data", []) if isinstance(etsy_direct, dict) else []

            data_sources = {
                "pricing": "etsy_extract" if etsy_raw else "blog_inference",
                "competitors": "etsy_extract" if etsy_raw else "blog_mention",
                "trend": "google_trends" if isinstance(trend_data, dict) and trend_data.get("source") == "google_trends" else "llm_inference",
                "keywords": "erank_content" if erank_raw else "llm_inference",
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
                        f"{failure_text}\n\n"
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
                    "created_at": datetime.utcnow().isoformat(),
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
        )

    # ------------------------------------------------------------------
    # Ricerca multi-nicchia con sub-agenti paralleli
    # ------------------------------------------------------------------

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

        # Step 4 — Sintesi comparativa: solo summary/recommendation.
        # I niches[] sono già strutturati e validati dai sub-agenti — non vanno ri-generati
        # via LLM perché il JSON aggregato supererebbe max_tokens e verrebbe troncato.
        slim_summary = [
            {
                "name": n.get("name"),
                "viable": n.get("viable"),
                "viability_reason": n.get("viability_reason"),
                "demand_level": n.get("demand", {}).get("level"),
                "demand_trend": n.get("demand", {}).get("trend"),
                "competition_level": n.get("competition", {}).get("level"),
                "conversion_sweet_spot_usd": n.get("pricing", {}).get("conversion_sweet_spot_usd"),
                "entry_difficulty": n.get("entry_difficulty"),
                "confidence": n.get("confidence"),
            }
            for n in all_niche_data
        ]
        rec_prompt = (
            f"Hai analizzato {len(niches)} nicchie Etsy: {', '.join(niches)}.\n"
            f"Ecco i dati chiave per ciascuna:\n"
            f"{json.dumps(slim_summary, indent=2, default=str)}\n\n"
            "Rispondi SOLO con questo JSON (niente altro):\n"
            "{\n"
            '  "summary": "raccomandazione esecutiva: quale nicchia perseguire subito e perché",\n'
            '  "recommended_next_steps": ["azione concreta 1", "azione concreta 2"],\n'
            '  "data_quality_warning": "stringa vuota se dati OK, altrimenti descrivi problemi"\n'
            "}"
        )
        rec_raw = await self._call_llm(
            messages=[{"role": "user", "content": rec_prompt}],
            system_prompt=None,
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
        )

    # ------------------------------------------------------------------
    # Utility — parsing, validazione, confidence
    # ------------------------------------------------------------------

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

    async def _parse_and_validate(
        self, text: str, system_prompt: str
    ) -> dict[str, Any] | None:
        """Parse JSON con retry su fallimento e validazione struttura."""
        # Tentativo 1
        result = self._try_parse_json(text)
        if result is None:
            # Retry con correction prompt
            corrected = await self._call_llm(
                messages=[{
                    "role": "user",
                    "content": (
                        "Il JSON seguente è malformato o incompleto. "
                        "Riscrivilo correttamente rispettando esattamente la struttura "
                        "indicata nel system prompt. Rispondi SOLO con JSON valido.\n\n"
                        f"JSON da correggere:\n{text}"
                    ),
                }],
                system_prompt=system_prompt,
            )
            result = self._try_parse_json(corrected)

        if result is None:
            return None  # Caller restituirà FAILED

        # Validazione campi obbligatori
        if not isinstance(result.get("niches"), list) or len(result["niches"]) == 0:
            return None
        for niche in result["niches"]:
            required = ["name", "keywords", "pricing", "recommended_product_type", "demand"]
            if not all(k in niche for k in required):
                return None
            if not isinstance(niche.get("keywords"), list) or len(niche["keywords"]) == 0:
                return None

        # Fix 3 — Valida e ottimizza tag per ogni nicchia
        for i, niche in enumerate(result["niches"]):
            result["niches"][i] = self._validate_and_fix_tags(niche)

        # Fix 4 — Viability gate
        result, _ = self._apply_viability_gate(result)
        if result is None:
            return None

        return result

    @staticmethod
    def _try_parse_json(text: str) -> dict[str, Any] | None:
        """Tenta il parse JSON, None se fallisce. Tolera markdown fences."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            start = 1
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            cleaned = "\n".join(lines[start:end]).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _validate_and_fix_tags(niche_data: dict) -> dict:
        """
        Valida e ottimizza i 13 tag Etsy per ogni nicchia.
        Etsy tag rules: max 20 chars each, no special chars except spaces,
        lowercase preferibile, possono essere frasi (multi-word).
        """
        import re as _re

        tags = niche_data.get("etsy_tags_13", [])

        if len(tags) != 13:
            if len(tags) > 13:
                tags = tags[:13]
            elif len(tags) < 13 and len(tags) > 0:
                keywords = niche_data.get("keywords", [])
                for kw in keywords:
                    if kw not in tags and len(tags) < 13:
                        tags.append(kw)
            niche_data["etsy_tags_13"] = tags[:13]

        fixed_tags = []
        for tag in tags:
            tag = str(tag).lower().strip()
            tag = _re.sub(r'[^a-z0-9\s\-]', '', tag)
            if len(tag) > 20:
                tag = tag[:20].rsplit(' ', 1)[0]
            if tag and len(tag) >= 2:
                fixed_tags.append(tag)

        seen: set[str] = set()
        unique_tags: list[str] = []
        for tag in fixed_tags:
            if tag not in seen:
                seen.add(tag)
                unique_tags.append(tag)

        niche_data["etsy_tags_13"] = unique_tags[:13]

        short_tags = [t for t in unique_tags if len(t.split()) == 1]
        long_tags = [t for t in unique_tags if len(t.split()) >= 2]

        if len(short_tags) > 8:
            niche_data.setdefault("notes", "")
            niche_data["notes"] += " [WARNING: troppi tag singola parola, considera frasi long-tail]"

        if len(long_tags) < 3:
            niche_data.setdefault("notes", "")
            niche_data["notes"] += " [WARNING: pochi tag long-tail, visibilità potenzialmente bassa]"

        return niche_data

    @staticmethod
    def _apply_viability_gate(result: dict) -> tuple[dict | None, list[dict]]:
        """
        Applica criteri di business per scartare nicchie non profittevoli.
        Ritorna (result_filtrato, lista_motivi_scarto).
        """
        discarded: list[dict] = []
        viable_niches: list[dict] = []

        for niche in result.get("niches", []):
            reasons_to_discard: list[str] = []

            pricing = niche.get("pricing", {})
            demand = niche.get("demand", {})
            competition = niche.get("competition", {})

            sweet_spot = (
                pricing.get("conversion_sweet_spot_usd", 0)
                or pricing.get("sweet_spot_usd", 0)
            )
            difficulty = niche.get("entry_difficulty", "medium")
            demand_level = demand.get("level", "medium")
            demand_trend = demand.get("trend", "stable")
            viable_flag = niche.get("viable", True)

            if viable_flag is False:
                discarded.append({
                    "name": niche["name"],
                    "reason": niche.get("viability_reason", "Marcata non viable dall'analisi"),
                })
                viable_niches.append(niche)
                continue

            if 0 < sweet_spot < 2.99:
                reasons_to_discard.append(
                    f"Prezzo sweet spot ${sweet_spot} troppo basso: "
                    f"dopo fee Etsy (6.5% + €0.20) e costi API, margine negativo o nullo"
                )

            if difficulty == "high" and demand_level == "low":
                reasons_to_discard.append(
                    "Combinazione fatale: alta difficoltà d'ingresso + bassa domanda. "
                    "ROI atteso negativo."
                )

            if demand_trend == "declining" and competition.get("level") == "high":
                reasons_to_discard.append(
                    "Mercato in declino con alta competizione: finestra di opportunità chiusa."
                )

            if not niche.get("etsy_tags_13"):
                reasons_to_discard.append(
                    "Nessun tag Etsy generato: Publisher Agent non può creare il listing."
                )

            if reasons_to_discard:
                niche["viable"] = False
                niche["viability_reason"] = " | ".join(reasons_to_discard)
                discarded.append({
                    "name": niche["name"],
                    "reason": niche["viability_reason"],
                })

            viable_niches.append(niche)

        result["niches"] = viable_niches

        all_viable = [n for n in viable_niches if n.get("viable", True)]
        if not all_viable:
            return None, discarded

        result["discarded_niches"] = discarded
        return result, discarded

    def _enforce_failure_constraints(
        self,
        output: dict,
        failure_context: list[dict],
    ) -> tuple[dict, list[str]]:
        """
        Verifica strutturalmente che l'output rispetti le failure analysis.
        Modifica l'output direttamente se trova violazioni.
        Ritorna (output_modificato, lista_violazioni).
        """
        violations: list[str] = []

        if not failure_context:
            return output, violations

        failure_map: dict[str, list[dict]] = {}
        for fc in failure_context:
            meta = fc.get("metadata", {})
            niche_name = meta.get("niche", "").lower()
            if niche_name:
                if niche_name not in failure_map:
                    failure_map[niche_name] = []
                failure_map[niche_name].append({
                    "failure_type": meta.get("failure_type", ""),
                    "avoid_in_future": meta.get("avoid_in_future", ""),
                    "document": fc.get("document", ""),
                })

        filtered_niches: list[dict] = []
        for niche in output.get("niches", []):
            niche_name = niche.get("name", "").lower()

            matched_failures: list[dict] = []
            for failed_niche, failures in failure_map.items():
                if (
                    failed_niche in niche_name
                    or niche_name in failed_niche
                    or any(
                        word in niche_name
                        for word in failed_niche.split()
                        if len(word) > 4
                    )
                ):
                    matched_failures.extend(failures)

            if not matched_failures:
                filtered_niches.append(niche)
                continue

            has_fatal = any(f["failure_type"] == "no_views_no_sales" for f in matched_failures)
            has_no_views = any(f["failure_type"] == "no_views" for f in matched_failures)
            has_no_conversion = any(f["failure_type"] == "no_conversion" for f in matched_failures)

            if has_fatal:
                violations.append(
                    f"Nicchia '{niche['name']}' SCARTATA: failure history no_views_no_sales. "
                    f"Avoid: {[f['avoid_in_future'] for f in matched_failures if f['failure_type'] == 'no_views_no_sales']}"
                )
                niche["viable"] = False
                niche["viability_reason"] = (
                    f"SCARTATA automaticamente: failure history no_views_no_sales. "
                    f"Problema: {matched_failures[0].get('avoid_in_future', 'non specificato')}"
                )
                filtered_niches.append(niche)
                continue

            if has_no_views:
                avoid_kw = [
                    f["avoid_in_future"]
                    for f in matched_failures
                    if f["failure_type"] == "no_views"
                ]
                violations.append(
                    f"Nicchia '{niche['name']}': no_views history — tag strategy deve evitare: {avoid_kw}"
                )
                faa = niche.setdefault("failure_analysis_applied", {"failures_found": 0, "actions_taken": [], "avoided": []})
                faa.setdefault("actions_taken", []).append(
                    f"Tag strategy modificata per failure no_views: evitati {avoid_kw}"
                )
                faa.setdefault("avoided", []).extend(avoid_kw)
                niche["tag_strategy"] = (
                    f"[FAILURE-ADJUSTED] {niche.get('tag_strategy', '')} — "
                    f"Evitati tag che hanno causato 0 views in precedenza: {avoid_kw}"
                )

            if has_no_conversion:
                avoid_price = [
                    f["avoid_in_future"]
                    for f in matched_failures
                    if f["failure_type"] == "no_conversion"
                ]
                violations.append(
                    f"Nicchia '{niche['name']}': no_conversion history — prezzo deve cambiare. Avoid: {avoid_price}"
                )
                faa = niche.setdefault("failure_analysis_applied", {"failures_found": 0, "actions_taken": [], "avoided": []})
                faa.setdefault("actions_taken", []).append(
                    f"Price point ajustato per failure no_conversion: {avoid_price}"
                )
                current_price = niche.get("pricing", {}).get("conversion_sweet_spot_usd", 0)
                niche["pricing"]["price_reasoning"] = (
                    f"[FAILURE-ADJUSTED] Prezzo modificato rispetto a history no_conversion. "
                    f"Precedente problematico: {avoid_price}. Nuovo: {current_price}"
                )

            filtered_niches.append(niche)

        output["niches"] = filtered_niches
        return output, violations

    @staticmethod
    def _calculate_confidence(
        data_sources: dict[str, str],
        output: dict,
    ) -> tuple[float, list[str]]:
        """
        Confidence 0.0-1.0 basata su:
        - Qualità fonti dati (55%)
        - Completezza output selling-critical (45%)
        """
        score = 0.0
        missing: list[str] = []

        # === PARTE 1: Qualità fonti dati (55% del totale) ===

        # Pricing (peso 0.20)
        pricing_src = data_sources.get("pricing", "")
        if pricing_src in ("etsy_api", "etsy_extract", "cached"):
            score += 0.20
        elif pricing_src == "blog_inference":
            score += 0.06
            missing.append("prezzi reali da listing Etsy")
        elif pricing_src == "llm_inference":
            score += 0.02
            missing.append("qualsiasi dato prezzo reale")

        # Trend (peso 0.15)
        trend_src = data_sources.get("trend", "")
        if trend_src in ("google_trends", "cached"):
            score += 0.15
        else:
            score += 0.03
            missing.append("dati trend Google Trends")

        # Keywords (peso 0.12)
        kw_src = data_sources.get("keywords", "")
        if kw_src in ("erank_content", "cached", "erank_api"):
            score += 0.12
        elif kw_src == "community_search":
            score += 0.07
        elif kw_src == "llm_inference":
            score += 0.03
            missing.append("volume keyword reale da eRank o community")

        # Competitors (peso 0.08)
        comp_src = data_sources.get("competitors", "")
        if comp_src in ("etsy_api", "etsy_extract", "cached"):
            score += 0.08
        elif comp_src == "blog_mention":
            score += 0.04
            missing.append("dati competitor reali con metriche shop")

        # === PARTE 2: Completezza output selling-critical (45% del totale) ===

        niches = output.get("niches", [])
        viable_niches = [n for n in niches if n.get("viable", True)]

        if not viable_niches:
            missing.append("nessuna nicchia viable trovata")
            return round(min(score, 1.0), 2), missing

        sample = viable_niches[0]

        # 13 tag presenti e validi (peso 0.15)
        tags = sample.get("etsy_tags_13", [])
        if len(tags) == 13:
            score += 0.15
        elif len(tags) >= 8:
            score += 0.08
            missing.append(f"solo {len(tags)}/13 tag Etsy generati")
        else:
            score += 0.02
            missing.append(f"tag insufficienti: {len(tags)}/13 — listing non pubblicabile")

        # Selling signals presenti (peso 0.15)
        selling = sample.get("selling_signals", {})
        selling_complete = all([
            selling.get("thumbnail_style"),
            selling.get("conversion_triggers"),
            selling.get("bundle_vs_single"),
            selling.get("first_listing_recommendation"),
        ])
        if selling_complete:
            score += 0.15
        elif selling:
            score += 0.07
            missing.append("selling signals incompleti (thumbnail style o conversion triggers mancanti)")
        else:
            score += 0.01
            missing.append("selling signals assenti — Design Agent lavora senza guida visiva")

        # Pricing specifico per conversione (peso 0.10)
        pricing = sample.get("pricing", {})
        if pricing.get("conversion_sweet_spot_usd") and pricing.get("launch_price_usd"):
            score += 0.10
        elif pricing.get("conversion_sweet_spot_usd") or pricing.get("sweet_spot_usd"):
            score += 0.05
            missing.append("launch price strategy mancante")
        else:
            score += 0.01
            missing.append("pricing strategico assente")

        # Seasonal timing (peso 0.05)
        if sample.get("demand", {}).get("peak_months") and sample.get("demand", {}).get("publish_timing_advice"):
            score += 0.05
        else:
            score += 0.01
            missing.append("timing stagionale non specificato")

        return round(min(score, 1.0), 2), missing
