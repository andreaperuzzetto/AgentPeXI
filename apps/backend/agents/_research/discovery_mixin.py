"""ResearchAgent — discovery mixin."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from apps.backend.core.config import MODEL_SONNET
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.tools import tavily as tavily_tool
from apps.backend.tools.trends import get_google_trends

logger = logging.getLogger("agentpexi.research")


class _ResearchDiscoveryMixin:

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
        now = datetime.now()
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
                and item.get("status") in (
                    "published", "pending_design", "pending_approval",
                    "approved", "scheduled", "failed",
                )
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
                logger.exception("Unexpected error")
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
                        "product_type":   sc.product_type or sc.metadata.get("product_type", "printable_pdf"),
                        "source":         sc.metadata.get("source", "entry_point_scored"),
                        "entry_score":    sc.final_score,
                        "market_context": self._build_market_context(sc),
                    }
                    for sc in scored
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

        # ── Mock mode bypass — skip LLM, ritorna primo candidato come winner ──
        if getattr(self.memory, "mock_mode", False):
            winner = candidates[0]
            mock_niches = [
                {
                    "name":                    winner["niche"],
                    "niche":                   winner["niche"],
                    "viable":                  True,
                    "final_score":             winner.get("entry_score", 0.75),
                    "product_type":            winner.get("product_type", "printable_pdf"),
                    "recommended_product_type": winner.get("product_type", "printable_pdf"),
                    "_candidate_product_type": winner.get("product_type", "printable_pdf"),
                    "_candidate_source":       winner.get("source", "mock"),
                    "confidence":              0.75,
                    "why_winner":              "[MOCK MODE] Candidato selezionato per test pipeline.",
                    "demand":                  {"level": "high", "trend": "rising", "peak_months": ["April", "May"]},
                    "competition":             {"level": "medium"},
                    "pricing":                 {"suggested_eur": 3.50, "range": "2.99-4.99"},
                    "keywords":                winner.get("keywords", ["mock keyword"]),
                    "etsy_tags_13":            ["mock tag"] * 13,
                    "selling_signals":         {},
                    "color_palette_hint":      "warm tones",
                    "summary":                 "[MOCK MODE] Test listing generato automaticamente.",
                }
            ]
            await self._notify_telegram(
                f"✅ Research mock completato\n"
                f"🏆 Winner: {winner['niche']} [{winner.get('product_type', 'printable_pdf')}]\n"
                f"💡 [MOCK MODE] Nessuna chiamata LLM reale"
            )
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.COMPLETED,
                output_data={
                    "niches": mock_niches,
                    "winner": {
                        "niche":        winner["niche"],
                        "product_type": winner.get("product_type", "printable_pdf"),
                        "why_winner":   "[MOCK MODE] Candidato selezionato per test pipeline.",
                        "confidence":   0.75,
                        "brief": {
                            "template":          "basic_template",
                            "art_type":          None,
                            "etsy_tags_13":      ["mock tag"] * 13,
                            "selling_signals":   {},
                            "pricing":           {"suggested_eur": 3.50},
                            "keywords":          ["mock keyword"],
                            "color_palette_hint": "warm tones",
                        },
                    },
                    "runner_up": {
                        "niche":        candidates[1]["niche"] if len(candidates) > 1 else "",
                        "product_type": "printable_pdf",
                        "why":          "mock runner-up",
                    },
                    "summary":             "[MOCK MODE] Pipeline di test completata.",
                    "candidates_analyzed": len(candidates),
                    "candidates_viable":   len(candidates),
                },
            )
        # ── Fine mock bypass ──────────────────────────────────────────────────

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
                logger.warning("research: sub-agente exception: %s", item)
                continue
            candidate, result = item
            if result.status == TaskStatus.COMPLETED and isinstance(result.output_data, dict):
                for niche_entry in result.output_data.get("niches", []):
                    niche_entry["_candidate_product_type"] = candidate["product_type"]
                    niche_entry["_candidate_source"] = candidate["source"]
                    if not niche_entry.get("recommended_product_type"):
                        niche_entry["recommended_product_type"] = candidate["product_type"]
                    full_niche_data.append(niche_entry)
            else:
                _out      = result.output_data or {}
                err_msg   = _out.get("error", "no error")
                conf      = _out.get("confidence", 0)
                missing   = _out.get("missing_data", [])
                partial   = _out.get("partial_output", {}) or {}
                d_sources = partial.get("data_sources", {})
                logger.warning(
                    "research: sub-agente FAILED '%s' — error='%s' — confidence=%.2f — "
                    "data_sources=%s — missing=%s",
                    candidate["niche"], err_msg, conf, d_sources, missing,
                )
                failed.append(candidate["niche"])

        if not full_niche_data:
            logger.warning(
                "research: nessun dato utilizzabile da %d sub-agenti. "
                "Cause possibili: (1) Tavily API limit/errore → search_competitors/keywords falliti "
                "→ LLM marca tutte le nicchie non-viable per assenza dati; "
                "(2) confidence < 0.50 su tutti i candidati. "
                "Azione: ricaricare crediti Tavily o usare mock mode per test. "
                "Candidati analizzati: %s. Sub-agenti falliti: %s",
                len(candidates), [c["niche"] for c in candidates], failed,
            )
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={
                    "error": (
                        f"Tutti i sub-agenti falliti. Candidati: {[c['niche'] for c in candidates]}. "
                        f"Causa probabile: Tavily API limit esaurito o confidence < 0.50 per assenza dati Etsy. "
                        f"Azioni: (1) ricarica crediti Tavily, (2) usa mock mode per test pipeline."
                    )
                },
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
