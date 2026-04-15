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
Sei un esperto analista di mercato specializzato in Etsy e digital products.
Il tuo compito è analizzare nicchie di mercato e fornire report strutturati e azionabili.

Prima di raccomandare una nicchia o un tipo di prodotto, considera le failure analysis
passate che ti vengono fornite. Se trovi failure analysis per quella nicchia:
- Se failure_type è "no_views_no_sales": scarta la nicchia, segnala il problema
- Se failure_type è "no_views": puoi procedere ma aggiusta le keyword strategy
- Se failure_type è "no_conversion": puoi procedere ma cambia il price point o il template

Il campo "avoid_in_future" di ogni failure analysis è il più importante:
contiene esattamente cosa non ripetere.

Per ogni nicchia analizzata devi valutare:
1. **Domanda**: volume di ricerca, trend (crescente/stabile/calante), stagionalità
2. **Offerta**: numero competitor, qualità media, top seller e le loro strategie
3. **Prezzi**: range di prezzo (min/max/medio), sweet spot per massimizzare vendite
4. **Gap**: opportunità non coperte, sotto-nicchie poco servite, formati mancanti
5. **Keyword SEO**: tag migliori per Etsy, long-tail keyword, parole chiave correlate
6. **Tipologia prodotto consigliata**: PDF printable, digital art PNG, SVG bundle, o mix
7. **Difficoltà di ingresso**: bassa/media/alta — basata su saturazione e qualità richiesta

Rispondi SEMPRE in JSON valido. Non aggiungere testo fuori dal JSON.
Usa questa struttura per il report:

{
  "niches": [
    {
      "name": "nome nicchia",
      "demand": {"level": "high|medium|low", "trend": "growing|stable|declining", "seasonality": "descrizione"},
      "competition": {"level": "high|medium|low", "top_sellers": ["shop1", "shop2"], "avg_quality": "high|medium|low"},
      "pricing": {"min_usd": 0.0, "max_usd": 0.0, "avg_usd": 0.0, "sweet_spot_usd": 0.0},
      "gaps": ["opportunità 1", "opportunità 2"],
      "keywords": ["keyword1", "keyword2", "keyword3"],
      "recommended_product_type": "printable_pdf|digital_art_png|svg_bundle|mixed",
      "entry_difficulty": "low|medium|high",
      "notes": "osservazioni aggiuntive"
    }
  ],
  "summary": "riassunto esecutivo con raccomandazione principale",
  "recommended_next_steps": ["azione 1", "azione 2"]
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
        failure_context = await self.memory.query_chromadb(
            query=f"failure analysis {query}",
            n_results=3,
            where={"type": "failure_analysis"},
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

        # Step 5 — Confidence
        confidence, missing_data = self._calculate_confidence(data_sources, output)
        output["confidence"] = confidence
        output["missing_data"] = missing_data
        output["data_sources"] = data_sources

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
        failure_context = await self.memory.query_chromadb(
            query=f"failure analysis {niche}",
            n_results=3,
            where={"type": "failure_analysis"},
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

        # Step 5 — Confidence
        confidence, missing_data = self._calculate_confidence(data_sources, output)
        output["confidence"] = confidence
        output["missing_data"] = missing_data
        output["data_sources"] = data_sources

        # Step 6 — Salva in ChromaDB con metadata estesi
        summary = output.get("summary", "") if isinstance(output, dict) else str(output)
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
        for r in sub_results:
            if r.status == TaskStatus.COMPLETED and isinstance(r.output_data, dict):
                niche_list = r.output_data.get("niches", [])
                all_niche_data.extend(niche_list)

        # Step 4 — Sintesi comparativa LLM
        synthesis = await self._call_llm(
            messages=[{
                "role": "user",
                "content": (
                    f"Hai analizzato {len(niches)} nicchie Etsy: {', '.join(niches)}.\n\n"
                    f"Ecco i dati raccolti per ciascuna:\n"
                    f"{json.dumps(all_niche_data, indent=2, default=str)}\n\n"
                    f"Produci un report JSON comparativo completo con tutte le nicchie "
                    f"e una raccomandazione chiara su quale/i perseguire prima. "
                    f"Segui la struttura indicata nel system prompt."
                ),
            }],
            system_prompt=SYSTEM_PROMPT,
        )

        output = await self._parse_and_validate(synthesis, SYSTEM_PROMPT)
        if output is None:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "JSON parsing fallito dopo retry nella sintesi multi-nicchia."},
            )

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data=output,
        )

    # ------------------------------------------------------------------
    # Utility — parsing, validazione, confidence
    # ------------------------------------------------------------------

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
    def _calculate_confidence(
        data_sources: dict[str, str],
        output: dict,
    ) -> tuple[float, list[str]]:
        """Calcola confidence 0.0-1.0 e lista missing_data.

        Basata su fonti dati reali, non su auto-valutazione LLM.
        """
        score = 0.0
        missing: list[str] = []

        # Pricing (peso 0.30) — critico per decisioni di business
        pricing_src = data_sources.get("pricing", "")
        if pricing_src in ("etsy_extract", "cached"):
            score += 0.30
        elif pricing_src == "blog_inference":
            score += 0.08
            missing.append("prezzi reali da listing Etsy (Etsy API non ancora disponibile)")

        # Trend (peso 0.25) — determina se entrare in una nicchia
        trend_src = data_sources.get("trend", "")
        if trend_src in ("google_trends", "cached"):
            score += 0.25
        elif trend_src == "llm_inference":
            score += 0.05
            missing.append("dati trend storici Google Trends")

        # Keywords (peso 0.25) — impatta direttamente la visibilità SEO
        kw_src = data_sources.get("keywords", "")
        if kw_src in ("erank_content", "cached"):
            score += 0.25
        elif kw_src == "llm_inference":
            score += 0.08
            missing.append("volume keyword reale da eRank o Marmalead")

        # Competitors (peso 0.20) — orienta il posizionamento
        comp_src = data_sources.get("competitors", "")
        if comp_src in ("etsy_extract", "cached"):
            score += 0.20
        elif comp_src == "blog_mention":
            score += 0.06
            missing.append("dati competitor reali (shop, review count, anzianità)")

        return round(min(score, 1.0), 2), missing
