"""ResearchAgent — analisi di mercato Etsy per nicchie di digital products."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_SONNET
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.tools import tavily as tavily_tool

SYSTEM_PROMPT = """\
Sei un esperto analista di mercato specializzato in Etsy e digital products.
Il tuo compito è analizzare nicchie di mercato e fornire report strutturati e azionabili.

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
        input_data = task.input_data
        niches: list[str] = input_data.get("niches", [])
        query: str = input_data.get("query", "")

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
        """Ricerca generica basata su query libera."""
        # Step 1 — Ricerca Tavily
        search_results = await self._call_tool(
            tool_name="tavily",
            action="search",
            input_params={"query": query},
            fn=tavily_tool.search,
            query=query,
            max_results=10,
        )

        # Step 2 — Analisi LLM
        analysis = await self._call_llm(
            messages=[{
                "role": "user",
                "content": (
                    f"Analizza questi risultati di ricerca per il mercato Etsy digital products.\n\n"
                    f"Query: {query}\n\n"
                    f"Risultati ricerca:\n{json.dumps(search_results, indent=2, default=str)}\n\n"
                    f"Produci un report JSON completo seguendo la struttura indicata nel system prompt."
                ),
            }],
            system_prompt=SYSTEM_PROMPT,
        )

        output = self._parse_json(analysis)

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
        # Step 1 — Tre ricerche parallele via Tavily
        niche_results, competitor_results, keyword_results = await asyncio.gather(
            self._call_tool(
                tool_name="tavily",
                action="search_etsy_niche",
                input_params={"niche": niche},
                fn=tavily_tool.search_etsy_niche,
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
        )

        # Step 2 — Analisi LLM con tutti i dati raccolti
        analysis = await self._call_llm(
            messages=[{
                "role": "user",
                "content": (
                    f"Analizza la nicchia Etsy: **{niche}** (digital products).\n\n"
                    f"## Dati nicchia\n{json.dumps(niche_results, indent=2, default=str)}\n\n"
                    f"## Dati competitor\n{json.dumps(competitor_results, indent=2, default=str)}\n\n"
                    f"## Dati keyword SEO\n{json.dumps(keyword_results, indent=2, default=str)}\n\n"
                    f"Produci un report JSON completo seguendo la struttura indicata nel system prompt."
                ),
            }],
            system_prompt=SYSTEM_PROMPT,
        )

        output = self._parse_json(analysis)

        # Step 3 — Salva insight in ChromaDB per memoria a lungo termine
        summary = output.get("summary", "") if isinstance(output, dict) else str(output)
        if summary:
            await self._call_tool(
                tool_name="chromadb",
                action="store_insight",
                input_params={"niche": niche},
                fn=self.memory.store_insight,
                text=f"Research report per nicchia '{niche}': {summary}",
                metadata={"agent": self.name, "niche": niche, "task_id": self._task_id},
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

        # Step 2 — Esegui sub-agenti in parallelo
        sub_results: list[AgentResult] = await asyncio.gather(
            *[self.spawn_subagent(st) for st in sub_tasks]
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

        output = self._parse_json(synthesis)

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data=output,
        )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Estrae JSON dalla risposta LLM (tollera markdown fences)."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Rimuovi fences markdown
            lines = cleaned.split("\n")
            start = 1 if lines[0].startswith("```") else 0
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            cleaned = "\n".join(lines[start:end]).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {"raw_response": text}
