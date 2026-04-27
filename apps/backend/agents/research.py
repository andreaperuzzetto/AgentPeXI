"""ResearchAgent — analisi di mercato Etsy per nicchie di digital products."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU, MODEL_SONNET
from apps.backend.core.models import AgentCard, AgentResult, AgentTask, TaskStatus
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
      "recommended_product_type": "printable_pdf|digital_art_png|svg_bundle",
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

    def __init__(self, *, telegram_broadcaster: Callable | None = None, **kwargs: Any) -> None:
        super().__init__(name="research", model=MODEL_HAIKU, **kwargs)
        self._telegram_broadcast = telegram_broadcaster
        self._entry_scorer = None   # lazy init — EntryPointScoring (step 1.5)

    async def _notify_telegram(self, message: str) -> None:
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(message)
            except Exception:
                pass

    async def _get_entry_point_scorer(self):
        """
        Lazy init di EntryPointScoring + MarketDataAgent.
        Importazioni locali per evitare import circolari.
        mock_mode letto dalla tabella config (default False).
        """
        if self._entry_scorer is None:
            from apps.backend.agents.market_data import MarketDataAgent
            from apps.backend.core.entry_point_scoring import EntryPointScoring

            mock_mode = False
            try:
                db = await self.memory.get_db()
                cur = await db.execute(
                    "SELECT value FROM config WHERE key = 'system.mock_mode'"
                )
                row = await cur.fetchone()
                if row:
                    mock_mode = row["value"].lower() in ("true", "1", "on")
            except Exception:
                pass

            market_data = MarketDataAgent(memory=self.memory, mock_mode=mock_mode)
            self._entry_scorer = EntryPointScoring(
                memory=self.memory, market_data=market_data
            )
        return self._entry_scorer

    @staticmethod
    def _build_market_context(scored_candidate) -> str:
        """
        Converte un ScoredCandidate in una stringa compatta per il prompt LLM.
        Se signals è None (cold-start) ritorna stringa vuota.
        """
        sc = scored_candidate
        signals = sc.signals
        if signals is None:
            return ""

        lines = [
            "## Dati di mercato strutturati (MarketDataAgent)",
            f"Entry score: {sc.final_score:.3f} "
            f"(base={sc.base_score:.3f}, qgf={sc.quality_gap_factor}, "
            f"perf_mult={sc.performance_multiplier})",
            f"Listing Etsy trovati: {getattr(signals, 'etsy_result_count', 'n/d'):,}",
            f"Avg favorites (domanda proxy): {getattr(signals, 'avg_reviews', 0):.1f}",
            f"Avg prezzo: €{getattr(signals, 'avg_price_eur', 0):.2f}",
            f"Autocomplete hits: {getattr(signals, 'autocomplete_hits', 0)}",
            f"Google Trends score: {getattr(signals, 'google_trend_score', 0):.1f}/100",
            f"Seasonal boost: {getattr(signals, 'seasonal_boost', 1.0)}",
        ]
        return "\n".join(lines)

    async def _read_finance_context(self, niche: str) -> str:
        """
        Legge da ChromaDB i segnali prodotti da Finance:
          - niche_roi_snapshot: ROI storico per nicchia specifica
          - finance_directive: nicchie da scalare / abbandonare per direttiva strategica

        Ritorna una stringa pronta per essere iniettata nel prompt LLM.
        Ritorna stringa vuota se non ci sono dati (cold-start safe).
        """
        lines: list[str] = []

        # 1. ROI storico per questa nicchia
        try:
            roi_docs = await self.memory.query_chromadb_recent(
                query=f"Finance ROI snapshot nicchia {niche}",
                n_results=3,
                where={"type": {"$eq": "niche_roi_snapshot"}},
                primary_days=30,
                fallback_days=90,
            )
            if roi_docs:
                lines.append("## ROI storico (Finance)")
                for doc in roi_docs:
                    meta = doc.get("metadata", {})
                    doc_niche = meta.get("niche", "")
                    if doc_niche.lower() in niche.lower() or niche.lower() in doc_niche.lower():
                        roi_pct = meta.get("roi_pct", "n/d")
                        sales = meta.get("total_sales", "0")
                        margin = meta.get("net_margin_eur", "n/d")
                        lines.append(
                            f"  - Niche '{doc_niche}': ROI {roi_pct}%, "
                            f"{sales} vendite, €{margin} margine netto"
                        )
        except Exception:
            pass

        # 2. Finance insight — economia di pricing (break-even, costo per listing)
        #    Critico per la pricing analysis: Research deve sapere il costo reale
        #    per listing e quante vendite servono per coprirlo.
        try:
            insight_docs = await self.memory.query_chromadb_recent(
                query=f"Finance insight nicchia {niche} break-even costo listing pricing",
                n_results=3,
                where={"type": {"$eq": "finance_insight"}},
                primary_days=30,
                fallback_days=90,
            )
            if insight_docs:
                lines.append("## Economia reale per listing (Finance)")
                for doc in insight_docs:
                    meta = doc.get("metadata", {})
                    doc_niche = meta.get("niche", "")
                    if doc_niche.lower() in niche.lower() or niche.lower() in doc_niche.lower():
                        avg_price = meta.get("avg_price_eur", "n/d")
                        break_even = meta.get("break_even_units", "n/d")
                        cost_pl = meta.get("cost_per_listing_eur", "n/d")
                        roi = meta.get("roi_pct", "n/d")
                        lines.append(
                            f"  - Niche '{doc_niche}': prezzo medio reale €{avg_price}, "
                            f"break-even a {break_even} vendite, "
                            f"costo LLM/listing €{cost_pl}, ROI attuale {roi}%"
                        )
                        lines.append(
                            f"    → Il tuo pricing deve garantire almeno {break_even} vendite "
                            f"per coprire i costi di produzione. "
                            f"Raccomanda prezzi che rendano questo realistico."
                        )
        except Exception:
            pass

        # 2. Direttiva strategica Finance (nicchie da scalare / abbandonare)
        try:
            directive_docs = await self.memory.query_chromadb_recent(
                query="finance directive scale abandon niche strategy",
                n_results=1,
                where={"type": {"$eq": "finance_directive"}},
                primary_days=30,
                fallback_days=90,
            )
            if directive_docs:
                meta = directive_docs[0].get("metadata", {})
                to_scale = meta.get("niches_to_scale", "")
                to_abandon = meta.get("niches_to_abandon", "")
                date = meta.get("date", "")

                lines.append(f"## Direttiva strategica Finance (aggiornata {date})")

                niche_lower = niche.lower()
                abandon_list = [n.strip().lower() for n in to_abandon.split("|") if n.strip()]
                scale_list = [n.strip().lower() for n in to_scale.split("|") if n.strip()]

                if any(niche_lower in ab or ab in niche_lower for ab in abandon_list):
                    lines.append(
                        f"  ⛔ ATTENZIONE: Finance ha classificato questa nicchia come "
                        f"'da abbandonare' (ROI negativo). Valuta con estrema cautela."
                    )
                elif any(niche_lower in sc or sc in niche_lower for sc in scale_list):
                    lines.append(
                        f"  ✅ Finance raccomanda di SCALARE questa nicchia (ROI positivo confermato)."
                    )
                else:
                    if to_scale:
                        lines.append(f"  Nicchie da scalare: {to_scale.replace('|', ', ')}")
                    if to_abandon:
                        lines.append(f"  Nicchie da abbandonare: {to_abandon.replace('|', ', ')}")
        except Exception:
            pass

        if not lines:
            return ""

        return "\n\n## Contesto finanziario (Finance Agent)\n" + "\n".join(lines)

    async def _read_shared_context(self, query: str) -> str:
        """Legge insight cross-domain da shared_memory.

        Ritorna una stringa pronta per l'iniezione nel prompt LLM.
        Stringa vuota se shared_memory è vuota o non disponibile (cold-start safe).
        """
        try:
            docs = await self.memory.query_shared_memory(
                query=query,
                n_results=2,
                agent="research",
            )
            if not docs:
                return ""
            lines = ["## Insight cross-domain (Personal ↔ Etsy)"]
            for doc in docs:
                text = doc.get("document", "").strip()
                if text:
                    lines.append(f"- {text[:200]}")
            return "\n".join(lines) if len(lines) > 1 else ""
        except Exception:
            return ""

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

    # ------------------------------------------------------------------
    # Modalità autonoma — Research decide cosa produrre
    # ------------------------------------------------------------------

    # Categorie macro per il mining: usate per Google Trends e Tavily discovery
    _DISCOVERY_CATEGORIES: list[str] = [
        "printable planner digital download",
        "wall art printable etsy",
        "habit tracker printable",
        "budget planner printable",
        "digital art print etsy bestseller",
        "quote print wall art etsy",
        "botanical print etsy",
        "journal printable etsy",
    ]

    # Stagionalità: mese → nicchie emergenti (look-ahead 5 settimane)
    # Mappa stagionale: mese → lista di (niche, start_day, end_day)
    # start_day / end_day = giorno del mese in cui la nicchia entra/esce dalla finestra di produzione.
    # La nicchia viene inclusa se oggi (o un giorno nei prossimi 35) cade in [start_day, end_day].
    # Nicchie puntuali (Easter, Valentine): finestra stretta — end_day vicino alla data evento.
    # Nicchie a lunga coda (Natale, back-to-school): start_day anticipato al mese precedente
    #   tramite entrate duplicate in più mesi.
    _SEASONAL_MAP: dict[int, list[tuple[str, int, int]]] = {
        1:  [("new year planner",          1,  15),   # solo prima metà gennaio
             ("january goal tracker",      1,  25),
             ("winter journal",            1,  31)],
        2:  [("valentine printable",       1,  14),   # scade il 14 febbraio
             ("love quote print",          1,  20),
             ("february planner",          1,  25)],
        3:  [("spring planner",            1,  31),
             ("march habit tracker",       1,  31),
             ("st patrick printable",      1,  17)],  # scade il 17 marzo
        4:  [("easter printable",          1,   8),   # easter 2026 = 5 apr, scade ~8 apr
             ("spring wall art",           1,  30),
             ("april budget planner",      1,  25)],
        5:  [("mother's day printable",    1,  12),   # mother's day 2026 = 10 mag
             ("spring botanical print",    1,  31),
             ("may journal",               1,  25)],
        6:  [("summer planner",            1,  30),
             ("graduation printable",      1,  15),
             ("june goal tracker",         1,  25)],
        7:  [("summer habit tracker",      1,  31),
             ("july wall art",             1,  31),
             ("vacation planner printable",1,  25)],
        8:  [("back to school planner",    1,  31),
             ("august budget tracker",     1,  25),
             ("fall prep journal",        15,  31)],
        9:  [("autumn planner",            1,  30),
             ("fall botanical print",      1,  30),
             ("september habit tracker",   1,  25),
             ("back to school planner",    1,  15)],  # coda back-to-school
        10: [("halloween printable",       1,  31),
             ("october journal",           1,  25),
             ("fall quote print",          1,  31),
             ("christmas printable",      15,  31),   # Natale inizia da metà ottobre
             ("winter wall art",          15,  31)],
        11: [("thanksgiving printable",    1,  28),   # thanksgiving = 4° giovedì nov
             ("november budget planner",   1,  25),
             ("gratitude journal",         1,  30),
             ("christmas printable",       1,  30),   # tutto novembre
             ("winter wall art",           1,  30)],
        12: [("christmas printable",       1,  24),   # scade la vigilia
             ("december planner",          1,  20),
             ("winter wall art",           1,  31)],
    }

    async def _mine_opportunity_candidates(self) -> list[dict[str, str]]:
        """Genera 6-8 candidati (niche, product_type) da fonti dati reali.

        Fonti (tutte in parallelo):
        1. Google Trends su _DISCOVERY_CATEGORIES
        2. ChromaDB: finance_directive (scale/abandon), niche_roi_snapshot, design_winner
        3. Stagionalità calendario (mese corrente + look-ahead 5 settimane)
        4. Tavily: trending Etsy digital products ora

        Ritorna lista deduplicata di {"niche": str, "product_type": str, "source": str}
        """
        import calendar as _cal

        now = datetime.now()
        import calendar as _cal

        # 1-4 in parallelo
        (
            trend_results,
            chroma_finance,
            chroma_winners,
            tavily_trending,
        ) = await asyncio.gather(
            # 1. Google Trends su 3 categorie macro (più sarebbe troppo lento)
            asyncio.gather(*[
                self._call_tool(
                    tool_name="google_trends",
                    action="get_trends",
                    input_params={"keyword": cat},
                    fn=get_google_trends,
                    keyword=cat,
                )
                for cat in self._DISCOVERY_CATEGORIES[:4]
            ]),
            # 2. Finance: niches_to_scale + niche_roi_snapshot
            self.memory.query_chromadb_recent(
                query="finance niche roi positive scale abandon directive",
                n_results=5,
                where={"type": {"$in": ["finance_directive", "niche_roi_snapshot"]}},
                primary_days=30,
                fallback_days=90,
            ),
            # 3. Analytics: design_winner (nicchie che hanno già convertito)
            self.memory.query_chromadb_recent(
                query="design winner etsy sales conversion",
                n_results=5,
                where={"type": {"$eq": "design_winner"}},
                primary_days=60,
                fallback_days=180,
            ),
            # 4. Tavily: trending Etsy digital products oggi
            self._call_tool(
                tool_name="tavily",
                action="search",
                input_params={"query": "etsy best selling digital products 2026 trending printable"},
                fn=tavily_tool.search,
                query="etsy best selling digital products 2026 trending printable",
                max_results=8,
                search_depth="advanced",
            ),
            return_exceptions=True,
        )

        candidates: list[dict[str, str]] = []
        seen: set[str] = set()

        def _add(niche: str, product_type: str, source: str) -> None:
            key = f"{niche.lower().strip()}:{product_type}"
            if key not in seen and len(niche.strip()) > 3:
                seen.add(key)
                candidates.append({"niche": niche.strip(), "product_type": product_type, "source": source})

        # Stagionalità: controlla oggi + i prossimi 35 giorni.
        # Per ogni giorno dell'orizzonte, cerca nicchie il cui (month, start_day..end_day)
        # contiene quel giorno. Una nicchia viene aggiunta al massimo una volta (dedup via seen).
        seen_seasonal: set[str] = set()
        for offset in range(36):  # oggi + 35 giorni
            check_date = now + timedelta(days=offset)
            month = check_date.month
            day = check_date.day
            for (seasonal_niche, start_day, end_day) in self._SEASONAL_MAP.get(month, []):
                if start_day <= day <= end_day and seasonal_niche not in seen_seasonal:
                    seen_seasonal.add(seasonal_niche)
                    pt = "digital_art_png" if any(
                        w in seasonal_niche for w in ("print", "art", "wall", "botanical", "quote")
                    ) else "printable_pdf"
                    _add(seasonal_niche, pt, f"seasonal_m{month}")

        # Finance directive: niches_to_scale hanno priorità massima
        if isinstance(chroma_finance, list):
            for doc in chroma_finance:
                meta = doc.get("metadata", {})
                dtype = meta.get("type", "")
                if dtype == "finance_directive":
                    for niche_str in meta.get("niches_to_scale", "").split("|"):
                        if niche_str.strip():
                            _add(niche_str.strip(), "printable_pdf", "finance_scale")
                elif dtype == "niche_roi_snapshot":
                    roi = float(meta.get("roi_pct", 0) or 0)
                    if roi > 20:  # ROI > 20% → candidato forte
                        niche_name = meta.get("niche", "")
                        if niche_name:
                            _add(niche_name, "printable_pdf", f"finance_roi_{roi:.0f}pct")

        # Design winner: nicchie già validate da Analytics
        if isinstance(chroma_winners, list):
            for doc in chroma_winners:
                meta = doc.get("metadata", {})
                niche_name = meta.get("niche", "")
                pt = meta.get("product_type", "printable_pdf")
                if niche_name:
                    _add(niche_name, pt, "analytics_winner")

        # Google Trends: prende le categorie con trend crescente
        if isinstance(trend_results, list):
            for i, t in enumerate(trend_results):
                if isinstance(t, dict) and t.get("percent_change", 0) > 10:
                    cat = self._DISCOVERY_CATEGORIES[i] if i < len(self._DISCOVERY_CATEGORIES) else ""
                    if cat:
                        pt = "digital_art_png" if any(w in cat for w in ("wall art", "botanical", "print", "quote")) else "printable_pdf"
                        _add(cat, pt, f"trends_+{t.get('percent_change', 0):.0f}pct")

        # Padding con DEFAULT_NICHES se candidati insufficienti (cold-start)
        if len(candidates) < 4:
            defaults_pdf = [
                ("minimalist weekly planner", "printable_pdf"),
                ("habit tracker pastel", "printable_pdf"),
                ("budget planner printable", "printable_pdf"),
                ("minimalist botanical print", "digital_art_png"),
                ("inspirational quote wall art", "digital_art_png"),
            ]
            for niche, pt in defaults_pdf:
                _add(niche, pt, "default_pool")
                if len(candidates) >= 8:
                    break

        # --- Dedup: escludi nicchie già in produzione o già pubblicate su Etsy ---
        # Stesso filtro di _pick_niche() nello scheduler — evita lavoro doppio.
        try:
            seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            recent_queue = await self.memory.get_production_queue(status=None, limit=100)
            blocked: set[str] = {
                item["niche"].lower()
                for item in recent_queue
                if item.get("created_at", "") >= seven_days_ago
                and item.get("status") in ("completed", "in_progress", "planned")
            }
        except Exception:
            blocked = set()

        deduped: list[dict[str, str]] = []
        for c in candidates:
            niche_lower = c["niche"].lower()
            if niche_lower in blocked:
                continue
            try:
                if await self.memory.is_duplicate_product(c["niche"], c["product_type"]):
                    blocked.add(niche_lower)
                    continue
            except Exception:
                pass
            deduped.append(c)

        # Se dedup ha svuotato tutto (cold-start o tutto già prodotto), ripristina i default
        if not deduped:
            deduped = candidates  # meglio rianalizzare che non produrre niente

        return deduped[:8]  # max 8 candidati per mantenere i costi sotto controllo

    async def _autonomous_discovery(self, task: AgentTask) -> AgentResult:
        """Modalità autonoma: Research decide cosa produrre.

        Flow:
        1. Mina 6-8 candidati (niche × product_type) da trend/chromadb/stagionalità/finance
        2. Analizza ogni candidato in parallelo con Haiku (_single_niche_research)
        3. Sintetizza con Sonnet su dati COMPLETI → sceglie 1 vincitore con brief completo
        4. Output: winner{niche, product_type, brief} pronto per Design Agent

        Il brief include: template/art_type, etsy_tags_13, selling_signals,
        pricing, keywords, color_palette_hint — Design Agent non decide niente da solo.
        """
        await self._log_step("thinking", "Modalità autonoma: mining opportunità Etsy…")

        # Constraint opzionale dal chiamante (es. /pipeline png → solo digital_art_png)
        pt_constraint: str = (task.input_data or {}).get("product_type_constraint", "")

        # Step 1 — genera candidati
        candidates = await self._mine_opportunity_candidates()

        # Filtra per product_type se vincolato dall'esterno
        if pt_constraint and pt_constraint != "printable_pdf":
            # Constraint esplicito non-default: filtra candidati
            filtered = [c for c in candidates if c.get("product_type") == pt_constraint]
            if filtered:
                candidates = filtered
                await self._log_step(
                    "thinking",
                    f"Constraint product_type='{pt_constraint}': {len(candidates)} candidati compatibili",
                )
            else:
                await self._log_step(
                    "thinking",
                    f"Constraint '{pt_constraint}': nessun candidato specifico — uso tutti i candidati come fallback",
                )

        if not candidates:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "Nessun candidato generato dal mining — ChromaDB vuoto e Tavily non disponibile."},
            )

        await self._log_step(
            "thinking",
            f"Candidati trovati: {len(candidates)} — "
            + ", ".join(f"{c['niche']} [{c['product_type']}]" for c in candidates[:4])
            + ("…" if len(candidates) > 4 else ""),
        )

        # Step 1b — EntryPointScoring: filtra e ordina, mantiene top-3
        # Fallisce silenziosamente: se lo scorer dà errore usa i candidati raw
        try:
            scorer = await self._get_entry_point_scorer()
            scored = await scorer.rank_candidates(candidates, top_k=3)
            if scored:
                candidates = [
                    {
                        "niche":          sc.niche,
                        "product_type":   sc.product_type or c.get("product_type", "printable_pdf"),
                        "source":         c.get("source", "entry_point_scored"),
                        "entry_score":    sc.final_score,
                        "market_context": self._build_market_context(sc),
                    }
                    for sc, c in zip(
                        scored,
                        {c["niche"]: c for c in candidates}.values(),
                    )
                ]
                await self._log_step(
                    "thinking",
                    "EntryPointScoring top-3: "
                    + ", ".join(
                        f"{c['niche']} [score={c['entry_score']:.2f}]"
                        for c in candidates
                    ),
                )
        except Exception as _ep_err:
            logger.warning(
                "research: EntryPointScoring fallito, uso candidati raw: %s", _ep_err
            )

        await self._notify_telegram(
            f"🔍 Research autonomo: analisi {len(candidates)} candidati in parallelo…"
        )

        # Step 2 — analisi parallela con Haiku (semaforo 3)
        sem = asyncio.Semaphore(3)

        async def _analyze(candidate: dict) -> tuple[dict, AgentResult]:
            async with sem:
                sub_task = AgentTask(
                    agent_name=self.name,
                    input_data={
                        "niches":          [candidate["niche"]],
                        "product_type_hint": candidate["product_type"],
                        "entry_score":     candidate.get("entry_score", 0.0),
                        "market_context":  candidate.get("market_context", ""),
                    },
                    source=task.source,
                )
                result = await self.spawn_subagent(sub_task)
                return candidate, result

        raw_results = await asyncio.gather(*[_analyze(c) for c in candidates], return_exceptions=True)

        # Step 3 — raccolta dati completi per sintesi
        full_niche_data: list[dict] = []
        failed: list[str] = []

        for item in raw_results:
            if isinstance(item, Exception):
                continue
            candidate, result = item
            if result.status == TaskStatus.COMPLETED and isinstance(result.output_data, dict):
                for niche_entry in result.output_data.get("niches", []):
                    # Arricchisce l'entry con il product_type suggerito dal mining
                    niche_entry["_candidate_product_type"] = candidate["product_type"]
                    niche_entry["_candidate_source"] = candidate["source"]
                    # Se Research ha espresso una preferenza di product_type, rispettala
                    if not niche_entry.get("recommended_product_type"):
                        niche_entry["recommended_product_type"] = candidate["product_type"]
                    full_niche_data.append(niche_entry)
            else:
                failed.append(candidates[raw_results.index(item)]["niche"] if not isinstance(item, Exception) else "unknown")

        if not full_niche_data:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": f"Tutti i sub-agenti hanno fallito. Candidati: {[c['niche'] for c in candidates]}"},
            )

        viable = [n for n in full_niche_data if n.get("viable", True)]
        if not viable:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "Nessun candidato viable dopo analisi. Mercato saturo o dati insufficienti.", "analyzed": full_niche_data},
            )

        await self._log_step("thinking", f"Sintesi con Sonnet su {len(viable)} candidati viable…")

        # Step 4 — sintesi Sonnet su dati COMPLETI (non slim_summary)
        # Includi tutto: pricing, selling_signals, tags, entry_difficulty, product_type
        synthesis_prompt = (
            f"Sei un imprenditore Etsy esperto. Hai analizzato {len(viable)} opportunità.\n"
            f"Devi scegliere UNA sola da produrre adesso — quella con il massimo potenziale di "
            f"vendita nei prossimi 30 giorni.\n\n"
            f"CRITERI DI SELEZIONE (in ordine di peso):\n"
            f"1. Domanda alta + trend crescente o stabile\n"
            f"2. Competition medium o low (non high a meno che il gap sia sfruttabile)\n"
            f"3. Sweet spot prezzo >= $2.99 e realistica per il tipo di prodotto\n"
            f"4. 13 tag Etsy validi e selling signals completi\n"
            f"5. Entry difficulty low o medium\n"
            f"6. Prodotto raccomandato (PDF o PNG) allineato con l'opportunità\n\n"
            f"DATI COMPLETI CANDIDATI:\n"
            f"{json.dumps(viable, indent=2, default=str)}\n\n"
            f"Rispondi SOLO con JSON:\n"
            "{\n"
            '  "winner": {\n'
            '    "niche": "nome esatto nicchia",\n'
            '    "product_type": "printable_pdf|digital_art_png",\n'
            '    "why_winner": "motivazione concisa (2-3 frasi, focalizzata su vendite)",\n'
            '    "confidence": 0.0,\n'
            '    "brief": {\n'
            '      "template": "nome template PDF (solo se printable_pdf, altrimenti null)",\n'
            '      "art_type": "wall_art|quote_print|botanical_print|nursery_print (solo se digital_art_png, altrimenti null)",\n'
            '      "etsy_tags_13": ["tag1", "...", "tag13"],\n'
            '      "selling_signals": {},\n'
            '      "pricing": {},\n'
            '      "keywords": [],\n'
            '      "color_palette_hint": "colori dominanti consigliati per il design (es: sage green, warm beige, dusty pink)"\n'
            "    }\n"
            "  },\n"
            '  "runner_up": {"niche": "...", "product_type": "...", "why": "..."},\n'
            '  "summary": "raccomandazione esecutiva 1-2 frasi",\n'
            '  "candidates_analyzed": ' + str(len(full_niche_data)) + ",\n"
            '  "candidates_viable": ' + str(len(viable)) + "\n"
            "}"
        )

        synthesis_raw = await self._call_llm(
            messages=[{"role": "user", "content": synthesis_prompt}],
            system_prompt=None,
            model_override=MODEL_SONNET,
            max_tokens=2048,
        )

        synthesis = self._try_parse_json(synthesis_raw)
        if synthesis is None:
            # Retry
            retry_raw = await self._call_llm(
                messages=[{
                    "role": "user",
                    "content": (
                        "Il seguente JSON è malformato. Riscrivilo correttamente senza testo aggiuntivo.\n\n"
                        f"{synthesis_raw}"
                    ),
                }],
                system_prompt=None,
                model_override=MODEL_SONNET,
            )
            synthesis = self._try_parse_json(retry_raw)

        if not synthesis or "winner" not in synthesis:
            # Fallback deterministico: prendi il primo viable con confidence più alta
            best = max(viable, key=lambda n: n.get("confidence", 0))
            synthesis = {
                "winner": {
                    "niche": best.get("name", viable[0].get("name", "unknown")),
                    "product_type": best.get("recommended_product_type", "printable_pdf"),
                    "why_winner": "Scelto per confidence massima tra i candidati viable (fallback deterministico).",
                    "confidence": best.get("confidence", 0.5),
                    "brief": {
                        "template": best.get("_template_hint"),
                        "art_type": None,
                        "etsy_tags_13": best.get("etsy_tags_13", []),
                        "selling_signals": best.get("selling_signals", {}),
                        "pricing": best.get("pricing", {}),
                        "keywords": best.get("keywords", []),
                        "color_palette_hint": "",
                    },
                },
                "summary": f"Fallback: {best.get('name')} scelto per confidence massima.",
                "candidates_analyzed": len(full_niche_data),
                "candidates_viable": len(viable),
            }

        # Arricchisce il winner con tutti i dati dell'analisi originale (per Design + Publisher)
        winner_niche_name = synthesis["winner"]["niche"].lower()
        original_entry = next(
            (n for n in full_niche_data if n.get("name", "").lower() in winner_niche_name
             or winner_niche_name in n.get("name", "").lower()),
            None,
        )
        if original_entry:
            synthesis["winner"]["full_research"] = original_entry

        await self._notify_telegram(
            f"✅ Research autonomo completato:\n"
            f"🏆 Winner: {synthesis['winner']['niche']} [{synthesis['winner']['product_type']}]\n"
            f"💡 {synthesis['winner'].get('why_winner', '')[:120]}"
        )

        # Persisti la decisione in ChromaDB — il learning loop domenicale può
        # correlare questa scelta con i dati Analytics/Finance successivi e
        # restituire un feedback sulla qualità della decisione stessa.
        winner_data = synthesis["winner"]
        winner_pricing = winner_data.get("brief", {}).get("pricing", {})
        await self._call_tool(
            tool_name="chromadb",
            action="store_insight",
            input_params={"niche": winner_data["niche"]},
            fn=self.memory.store_insight,
            text=(
                f"Research decision: niche='{winner_data['niche']}' "
                f"product_type='{winner_data['product_type']}' "
                f"confidence={winner_data.get('confidence', 0):.2f} — "
                f"{winner_data.get('why_winner', '')[:200]}"
            ),
            metadata={
                "type": "research_decision",
                "niche": winner_data["niche"],
                "product_type": winner_data["product_type"],
                "confidence": str(winner_data.get("confidence", 0)),
                "candidates_analyzed": str(synthesis.get("candidates_analyzed", len(full_niche_data))),
                "candidates_viable": str(synthesis.get("candidates_viable", len(viable))),
                "launch_price_usd": str(winner_pricing.get("launch_price_usd", "")),
                "sweet_spot_usd": str(winner_pricing.get("conversion_sweet_spot_usd", "")),
                "agent": self.name,
                "task_id": self._task_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "winner": synthesis["winner"],
                "runner_up": synthesis.get("runner_up"),
                "niches": full_niche_data,  # manteniamo compatibilità con codice esistente
                "summary": synthesis.get("summary", ""),
                "candidates_analyzed": synthesis.get("candidates_analyzed", len(full_niche_data)),
                "candidates_viable": synthesis.get("candidates_viable", len(viable)),
                "failed_candidates": failed,
            },
            reply_voice=f"Opportunità trovata: {synthesis['winner']['niche']}.",
        )

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

        # Step 0b — Contesto finanziario da Finance Agent
        finance_text = await self._read_finance_context(query)

        # Step 0c — Insight cross-domain da shared_memory (Personal ↔ Etsy)
        shared_text = await self._read_shared_context(query)

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

    # ------------------------------------------------------------------
    # Ricerca singola nicchia
    # ------------------------------------------------------------------

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
            created_at_str = meta.get("created_at", "")
            if created_at_str:
                try:
                    created_at = datetime.fromisoformat(created_at_str)
                    if datetime.now(timezone.utc).replace(tzinfo=None) - created_at < timedelta(days=7):
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

        # Step 0c — Contesto finanziario da Finance Agent
        finance_text = await self._read_finance_context(niche)

        # Step 0d — Insight cross-domain da shared_memory (Personal ↔ Etsy)
        shared_text = await self._read_shared_context(niche)

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

        # Entry point scoring (peso 0.15) — dati strutturati da MarketDataAgent
        # Aggiunge confidenza quando abbiamo segnali di mercato reali pre-LLM
        entry_src = data_sources.get("entry_point", "none")
        if entry_src == "market_signals":
            score += 0.15
        # Se assente: score non penalizzato (source opzionale)

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
