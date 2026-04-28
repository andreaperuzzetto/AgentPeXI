"""AnalyticsAgent — sync stats Etsy, failure analysis, bestseller proposals."""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from datetime import datetime, timezone
from typing import Any, Callable, ClassVar, Coroutine

import anthropic

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU, MODEL_SONNET, settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentCard, AgentResult, AgentTask, TaskStatus

logger = logging.getLogger("agentpexi.analytics")

# ---------------------------------------------------------------------------
# Ladder System — soglie diagnostiche (fonte: Alfie + Etsy Seller Handbook 2026)
# ---------------------------------------------------------------------------
VIEWS_MIN_7DAYS            = 30     # views minime dopo 7+ giorni live
CTR_MIN                    = 0.02   # 2% — sotto: thumbnail non converte
CONV_MIN                   = 0.01   # 1% su clicks — sotto: listing non converte
MIN_DAYS_LIVE              = 7      # non diagnosticare listing < 7 giorni
REMEDIATION_COOLDOWN_HOURS = 48     # evita notifiche ripetute per lo stesso problema


class AnalyticsAgent(AgentBase):
    """Agente analytics: sync stats, failure analysis, bestseller proposals."""

    card: ClassVar[AgentCard] = AgentCard(
        name="analytics",
        description="Sync stats Etsy, failure analysis, bestseller proposals",
        input_schema={},
        layer="business",
        llm="haiku",
        confidence_threshold=0.85,
    )

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        etsy_api: Any,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        telegram_broadcaster: Callable | None = None,
        production_queue: Any | None = None,
        learning_loop: Any | None = None,
    ) -> None:
        super().__init__(
            name="analytics",
            model=MODEL_HAIKU,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )
        self.etsy_api           = etsy_api
        self._telegram_broadcast = telegram_broadcaster
        self._production_queue  = production_queue
        self._learning_loop     = learning_loop          # wired in step 4.5
        # in-memory log: {queue_item_id: {action: last_attempt_ts}}
        self._remediation_log: dict[int, dict[str, float]] = {}

    def _extra_init_kwargs(self) -> dict:
        return {
            "etsy_api": self.etsy_api,
            "telegram_broadcaster": self._telegram_broadcast,
        }

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # --- Passo 1 — Lettura listing (draft + active, escluso archived) ---
        # draft = appena pubblicato in mock/staging; active = live su Etsy
        all_listings = await self.memory.get_etsy_listings()
        listings = [l for l in all_listings if l.get("status") not in ("archived", "removed")]
        if not listings:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.COMPLETED,
                output_data={"message": "Nessun listing attivo da sincronizzare"},
                reply_voice="Nessun listing attivo da sincronizzare.",
            )

        # --- Passo 2 — Sync stats (parallelo, max 5 concurrent) ---
        sem = asyncio.Semaphore(5)

        async def _sync_one(listing: dict) -> dict | None:
            lid = listing["listing_id"]
            async with sem:
                try:
                    data = await self._call_tool(
                        "etsy_api",
                        "get_listing",
                        {"listing_id": lid},
                        self.etsy_api.get_listing,
                        listing_id=lid,
                    )
                except Exception as exc:
                    logger.warning("Sync listing %s fallito: %s", lid, exc)
                    return None

            views = data.get("views", 0)
            favorites = data.get("num_favorers", 0)
            # Vendite reali da endpoint transactions (non quantity!)
            shop_id = data.get("shop_id") or settings.ETSY_SHOP_ID
            sales_real = await self._get_listing_sales(str(lid), str(shop_id))
            if sales_real is not None:
                sales = sales_real
            else:
                # Non sovrascrivere mai con 0 se la chiamata fallisce
                sales = listing.get("sales", 0)
            status = data.get("state", "active")
            price = float(data.get("price", {}).get("amount", 0)) / 100 if isinstance(data.get("price"), dict) else float(data.get("price", 0))
            revenue_eur = sales * price

            now_iso = datetime.now(timezone.utc).isoformat()
            await self.memory.update_etsy_listing_stats(
                listing_id=lid,
                views=views,
                favorites=favorites,
                sales=sales,
                revenue_eur=revenue_eur,
                status=status,
                last_synced_at=now_iso,
            )
            return {
                "listing_id": lid,
                "views": views,
                "favorites": favorites,
                "sales": sales,
                "revenue_eur": revenue_eur,
            }

        sync_results = await asyncio.gather(
            *[_sync_one(l) for l in listings],
            return_exceptions=True,
        )
        synced = [r for r in sync_results if isinstance(r, dict)]

        await self._log_step(
            "tool_call",
            f"Sincronizzati {len(synced)}/{len(listings)} listing",
            output_data={"synced": len(synced)},
        )

        # --- Passo 3 — Failure analysis (parallelo con Semaphore) ---
        failure_counts = {"no_views": 0, "no_conversion": 0, "no_views_no_sales": 0}
        analysis_sem = asyncio.Semaphore(3)
        already_analyzed: set[str] = set()
        failure_tasks: list = []

        async def _analyze_with_sem(lst: dict, analyzer_fn) -> None:
            async with analysis_sem:
                await analyzer_fn(lst)

        # Caso C prima (priorità su B e A — problema doppio)
        no_both = await self.memory.get_listings_no_views_no_sales(days=45)
        for lst in no_both:
            lid_str = str(lst["listing_id"])
            if lid_str not in already_analyzed:
                failure_tasks.append(_analyze_with_sem(lst, self._analyze_no_views_no_sales))
                failure_counts["no_views_no_sales"] += 1
                already_analyzed.add(lid_str)

        # Caso B — skip se già in Caso C
        no_conv = await self.memory.get_listings_no_conversion(days=45)
        for lst in no_conv:
            lid_str = str(lst["listing_id"])
            if lid_str not in already_analyzed:
                failure_tasks.append(_analyze_with_sem(lst, self._analyze_no_conversion))
                failure_counts["no_conversion"] += 1
                already_analyzed.add(lid_str)

        # Caso A — skip se già in Caso B o C (soglia 14 giorni, non 7)
        no_views = await self.memory.get_listings_no_views(days=14)
        for lst in no_views:
            lid_str = str(lst["listing_id"])
            if lid_str not in already_analyzed:
                failure_tasks.append(_analyze_with_sem(lst, self._analyze_no_views))
                failure_counts["no_views"] += 1
                already_analyzed.add(lid_str)

        await asyncio.gather(*failure_tasks, return_exceptions=True)

        # --- Passo 4 — Bestseller e proposte varianti ---
        bestsellers = await self._find_bestsellers()

        # --- Passo 5 — Report aggregato ---
        report = await self._build_report(
            listings=listings,
            synced=synced,
            failure_counts=failure_counts,
            bestsellers=bestsellers,
            today_str=today_str,
        )

        # Salva report in ChromaDB
        await self.memory.store_insight(
            text=json.dumps(report, ensure_ascii=False, default=str),
            metadata={"type": "analytics_report", "date": today_str, "agent": "analytics"},
        )

        # --- Passo 6 — Summary Telegram ---
        await self._send_daily_summary(report, today_str)

        confidence, missing_data = self._calculate_analytics_confidence(
            listings, synced, failure_counts,
        )

        _n_synced = len(synced)
        _sync_label = "listing sincronizzato" if _n_synced == 1 else "listing sincronizzati"
        _reply_voice = f"Analytics aggiornato. {_n_synced} {_sync_label}."

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED if confidence >= 0.60 else TaskStatus.PARTIAL,
            output_data=report,
            confidence=confidence,
            missing_data=missing_data,
            reply_voice=_reply_voice,
        )

    # ------------------------------------------------------------------
    # Sales tracking via transactions
    # ------------------------------------------------------------------

    async def _get_listing_sales(self, listing_id: str, shop_id: str) -> int | None:
        """
        Conta vendite reali via GET /shops/{shop_id}/listings/{listing_id}/transactions.
        Ritorna None se la chiamata fallisce (non 0 — differenza critica).
        """
        try:
            transactions = await self._call_tool(
                "etsy_api",
                "get_shop_transactions",
                {"shop_id": shop_id, "listing_id": listing_id},
                self.etsy_api.get_shop_transactions,
                shop_id=shop_id,
                listing_id=listing_id,
            )
            if isinstance(transactions, dict):
                results = transactions.get("results", [])
            elif isinstance(transactions, list):
                results = transactions
            else:
                results = []
            return sum(t.get("quantity", 1) for t in results)
        except Exception as exc:
            logger.warning("Get transactions listing %s fallito: %s", listing_id, exc)
            return None  # None = dati non disponibili, non 0

    # ------------------------------------------------------------------
    # Caso A — No views
    # ------------------------------------------------------------------

    async def _analyze_no_views(self, listing: dict) -> None:
        lid = listing["listing_id"]
        niche = listing.get("niche", "")
        await self.memory.flag_no_views(lid)
        historical_context = await self._fetch_similar_failures(niche, "no_views")

        analysis = await self._failure_llm(
            prompt=self._no_views_prompt(listing),
            historical_context=historical_context,
        )
        if not analysis:
            return

        chromadb_id = await self._save_failure_chromadb(
            listing=listing,
            failure_type="no_views",
            analysis=analysis,
        )

        await self.memory.save_listing_analysis(
            listing_id=lid,
            analysis_type="no_views",
            cause=analysis["cause"],
            recommendations=analysis["recommendations"],
            avoid_in_future=analysis["avoid_in_future"],
            chromadb_id=chromadb_id,
        )

        recs = "\n".join(f"• {r}" for r in analysis["recommendations"])
        msg = (
            f"⚠️ Listing da ottimizzare — visibilità\n"
            f"📦 {listing.get('title', '')[:60]}\n"
            f"📊 14 giorni · 0 visualizzazioni\n"
            f"🔍 Problema: {analysis['cause']}\n\n"
            f"💡 Cosa fare:\n{recs}\n\n"
            f"🔗 https://www.etsy.com/your-shop/listings/{lid}/edit\n"
            f"#ottimizza #no_views"
        )
        await self._notify_telegram(msg)

    @staticmethod
    def _no_views_prompt(listing: dict) -> str:
        tags = listing.get("tags") or []
        if isinstance(tags, str):
            tags = json.loads(tags) if tags.startswith("[") else [tags]
        return (
            f"Questo listing Etsy non ha ricevuto nessuna visualizzazione dopo 14 giorni.\n"
            f"Problema: discoverabilità — il listing non appare nelle ricerche Etsy.\n\n"
            f"Titolo: {listing.get('title', '')}\n"
            f"Tag: {', '.join(tags)}\n"
            f"Nicchia: {listing.get('niche', '')}\n"
            f"Prezzo: €{listing.get('price_eur', 0)}\n"
            f"Formato: {listing.get('size', '')} {listing.get('template', '')}\n\n"
            f"Analizza titolo e tag. Il problema è probabilmente: keyword troppo generiche, "
            f"nicchia troppo competitiva, tag non allineati alla terminologia Etsy, o titolo "
            f"mal strutturato per l'algoritmo Etsy.\n\n"
            f'Rispondi SOLO con JSON:\n'
            f'{{\n'
            f'  "cause": "causa principale in max 80 caratteri",\n'
            f'  "recommendations": [\n'
            f'    "azione concreta 1",\n'
            f'    "azione concreta 2",\n'
            f'    "azione concreta 3"\n'
            f'  ],\n'
            f'  "avoid_in_future": "cosa NON ripetere in prodotti simili, max 80 caratteri"\n'
            f'}}'
        )

    # ------------------------------------------------------------------
    # Caso B — No conversion
    # ------------------------------------------------------------------

    async def _analyze_no_conversion(self, listing: dict) -> None:
        lid = listing["listing_id"]
        views = listing.get("views", 0)

        # Gate: almeno 30 views per avere dati significativi
        if views < 30:
            logger.info(
                "Skip no_conversion analysis listing %s: solo %d views (min 30)",
                lid, views,
            )
            await self.memory.flag_no_conversion(lid)
            return

        niche = listing.get("niche", "")
        await self.memory.flag_no_conversion(lid)
        historical_context = await self._fetch_similar_failures(niche, "no_conversion")

        analysis = await self._failure_llm(
            prompt=self._no_conversion_prompt(listing),
            historical_context=historical_context,
        )
        if not analysis:
            return

        chromadb_id = await self._save_failure_chromadb(
            listing=listing,
            failure_type="no_conversion",
            analysis=analysis,
        )

        await self.memory.save_listing_analysis(
            listing_id=lid,
            analysis_type="no_conversion",
            cause=analysis["cause"],
            recommendations=analysis["recommendations"],
            avoid_in_future=analysis["avoid_in_future"],
            chromadb_id=chromadb_id,
        )

        recs = "\n".join(f"• {r}" for r in analysis["recommendations"])
        views = listing.get("views", 0)
        favs = listing.get("favorites", 0)
        msg = (
            f"📉 Listing da ottimizzare — conversione\n"
            f"📦 {listing.get('title', '')[:60]}\n"
            f"📊 45 giorni · {views} views · {favs} ❤️ · 0 vendite\n"
            f"🔍 Problema: {analysis['cause']}\n\n"
            f"💡 Cosa fare:\n{recs}\n\n"
            f"🔗 https://www.etsy.com/your-shop/listings/{lid}/edit\n"
            f"#ottimizza #no_conversion"
        )
        await self._notify_telegram(msg)

    @staticmethod
    def _no_conversion_prompt(listing: dict) -> str:
        tags = listing.get("tags") or []
        if isinstance(tags, str):
            tags = json.loads(tags) if tags.startswith("[") else [tags]
        views = listing.get("views", 0)
        favs = listing.get("favorites", 0)
        ab = listing.get("ab_price_variant", "?")
        return (
            f"Questo listing Etsy ha ricevuto {views} visualizzazioni e {favs} preferiti "
            f"ma 0 vendite dopo 45 giorni. C'è interesse, ma non converte in acquisto.\n"
            f"Problema: conversione — qualcosa blocca l'acquisto.\n\n"
            f"Titolo: {listing.get('title', '')}\n"
            f"Tag: {', '.join(tags)}\n"
            f"Nicchia: {listing.get('niche', '')}\n"
            f"Prezzo: €{listing.get('price_eur', 0)} (variante A/B: {ab})\n"
            f"Formato: {listing.get('size', '')} {listing.get('template', '')}\n"
            f"Views: {views} | Favorites: {favs}\n\n"
            f"Il problema può essere: prezzo non allineato alle aspettative, "
            f"descrizione poco convincente, prodotto non perfettamente adatto "
            f"alla nicchia, mancanza di social proof, o thumbnail non attraente. "
            f"Con {favs} preferiti e 0 vendite il problema è probabilmente "
            f"il prezzo o la descrizione.\n\n"
            f'Rispondi SOLO con JSON:\n'
            f'{{\n'
            f'  "cause": "causa principale in max 80 caratteri",\n'
            f'  "recommendations": [\n'
            f'    "azione concreta 1",\n'
            f'    "azione concreta 2",\n'
            f'    "azione concreta 3"\n'
            f'  ],\n'
            f'  "avoid_in_future": "cosa NON ripetere in prodotti simili, max 80 caratteri"\n'
            f'}}'
        )

    # ------------------------------------------------------------------
    # Caso C — No views + no sales
    # ------------------------------------------------------------------

    async def _analyze_no_views_no_sales(self, listing: dict) -> None:
        lid = listing["listing_id"]
        niche = listing.get("niche", "")
        await self.memory.flag_no_views_no_sales(lid)
        historical_context = await self._fetch_similar_failures(niche, "no_views_no_sales")

        analysis = await self._failure_llm(
            prompt=self._no_views_no_sales_prompt(listing),
            historical_context=historical_context,
        )
        if not analysis:
            return

        chromadb_id = await self._save_failure_chromadb(
            listing=listing,
            failure_type="no_views_no_sales",
            analysis=analysis,
        )

        await self.memory.save_listing_analysis(
            listing_id=lid,
            analysis_type="no_views_no_sales",
            cause=analysis["cause"],
            recommendations=analysis["recommendations"],
            avoid_in_future=analysis["avoid_in_future"],
            chromadb_id=chromadb_id,
        )

        recs = "\n".join(f"• {r}" for r in analysis["recommendations"])
        msg = (
            f"🚫 Listing da archiviare\n"
            f"📦 {listing.get('title', '')[:60]}\n"
            f"📊 45 giorni · 0 views · 0 vendite\n"
            f"🔍 Problema: {analysis['cause']}\n\n"
            f"💡 Cosa fare:\n{recs}\n\n"
            f"⚠️ Considera di archiviare questo listing su Etsy.\n"
            f"🔗 https://www.etsy.com/your-shop/listings/{lid}/edit\n"
            f"#archivia #no_views_no_sales"
        )
        await self._notify_telegram(msg)

    @staticmethod
    def _no_views_no_sales_prompt(listing: dict) -> str:
        tags = listing.get("tags") or []
        if isinstance(tags, str):
            tags = json.loads(tags) if tags.startswith("[") else [tags]
        return (
            f"Questo listing Etsy ha 0 visualizzazioni e 0 vendite dopo 45 giorni.\n"
            f"Nessun interesse registrato. Problema doppio: discoverabilità E validità "
            f"della nicchia stessa.\n\n"
            f"Titolo: {listing.get('title', '')}\n"
            f"Tag: {', '.join(tags)}\n"
            f"Nicchia: {listing.get('niche', '')}\n"
            f"Prezzo: €{listing.get('price_eur', 0)}\n"
            f"Formato: {listing.get('size', '')} {listing.get('template', '')}\n\n"
            f"Questo è il segnale più negativo possibile. La nicchia potrebbe essere "
            f"troppo di nicchia, stagionale, già satura, o il prodotto non corrisponde "
            f"a ciò che gli acquirenti cercano su Etsy.\n\n"
            f'Rispondi SOLO con JSON:\n'
            f'{{\n'
            f'  "cause": "causa principale in max 80 caratteri",\n'
            f'  "recommendations": [\n'
            f'    "azione concreta 1 — probabilmente abbandonare questa nicchia",\n'
            f'    "azione concreta 2",\n'
            f'    "azione concreta 3"\n'
            f'  ],\n'
            f'  "avoid_in_future": "nicchia/approccio da NON ripetere mai, max 80 caratteri"\n'
            f'}}'
        )

    # ------------------------------------------------------------------
    # Failure analysis helpers
    # ------------------------------------------------------------------

    async def _fetch_similar_failures(self, niche: str, failure_type: str) -> str:
        """
        Cerca in ChromaDB failure patterns per niche simili.
        Ritorna stringa contestuale da iniettare nel prompt LLM.
        Ritorna "" se ChromaDB è vuoto o la query fallisce.
        """
        try:
            results = await self.memory.query_chromadb_recent(
                query=f"FAILURE {failure_type} niche {niche}",
                n_results=3,
                where={"type": "failure_analysis", "failure_type": failure_type},
                primary_days=90,
                fallback_days=180,
            )
            if not results:
                return ""

            context_lines = []
            for r in results:
                doc = r.get("document", "")
                if "cause:" in doc and "avoid:" in doc:
                    context_lines.append(f"- {doc}")

            if not context_lines:
                return ""

            return (
                f"\nCONTESTO STORICO — fallimenti simili già registrati:\n"
                + "\n".join(context_lines[:3])
                + "\nUsa questo storico per dare raccomandazioni coerenti "
                  "ed evitare di ripetere consigli già dati.\n"
            )
        except Exception:
            return ""

    async def _failure_llm(self, prompt: str, historical_context: str = "") -> dict | None:
        """Chiama Sonnet per failure analysis, parsa JSON."""
        enriched_prompt = prompt
        if historical_context:
            insert_before = "Rispondi SOLO con JSON:"
            if insert_before in enriched_prompt:
                enriched_prompt = enriched_prompt.replace(
                    insert_before,
                    historical_context + insert_before,
                )
            else:
                enriched_prompt += "\n" + historical_context

        response_text = await self._call_llm(
            messages=[{"role": "user", "content": enriched_prompt}],
            system_prompt=(
                "Sei un analista esperto di Etsy marketplace. Analizza i problemi dei listing "
                "e suggerisci azioni concrete. Se hai storico di fallimenti simili, usa quelle "
                "informazioni per dare raccomandazioni coerenti nel tempo."
            ),
            model_override=MODEL_SONNET,
        )
        return self._parse_analysis_json(response_text)

    async def _save_failure_chromadb(
        self,
        listing: dict,
        failure_type: str,
        analysis: dict,
    ) -> str | None:
        niche = listing.get("niche", "")
        template = listing.get("template", "")
        cause = analysis["cause"]
        avoid = analysis["avoid_in_future"]
        recs = "; ".join(analysis["recommendations"])
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        text = (
            f"FAILURE {failure_type} | niche: {niche} | template: {template} | "
            f"cause: {cause} | avoid: {avoid} | recommendations: {recs}"
        )
        chromadb_id = await self.memory.store_insight(
            text=text,
            metadata={
                "type": "failure_analysis",
                "failure_type": failure_type,
                "niche": niche,
                "template": template,
                "date": today,
            },
        )
        return chromadb_id

    @staticmethod
    def _parse_analysis_json(text: str) -> dict | None:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            start = 1 if lines[0].startswith("```") else 0
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            cleaned = "\n".join(lines[start:end]).strip()
        try:
            data = json.loads(cleaned)
            if "cause" in data and "recommendations" in data and "avoid_in_future" in data:
                return data
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return None

    # ------------------------------------------------------------------
    # Passo 4 — Bestseller e proposte varianti
    # ------------------------------------------------------------------

    async def _find_bestsellers(self) -> list[dict]:
        """Identifica bestseller con soglia dinamica, propone varianti via pending_action."""
        all_listings = await self.memory.get_etsy_listings(status="active")

        total_sales_all = sum((l.get("sales") or 0) for l in all_listings)
        avg_sales = total_sales_all / max(len(all_listings), 1)
        # Soglia dinamica: almeno 2 vendite, o 50% sopra la media, cap a 10
        threshold = min(10, max(2, avg_sales * 1.5))

        top = sorted(
            [l for l in all_listings if (l.get("sales") or 0) >= threshold],
            key=lambda x: x.get("revenue_eur", 0),
            reverse=True,
        )[:3]

        bestsellers = []
        for lst in top:
            lid = lst["listing_id"]
            niche = lst.get("niche", "")
            template = lst.get("template", "")
            color_scheme = lst.get("color_scheme", "")
            bestsellers.append({
                "listing_id": lid,
                "title": lst.get("title", ""),
                "sales": lst.get("sales", 0),
                "revenue_eur": lst.get("revenue_eur", 0),
            })

            # Nota: il segnale ChromaDB per template/colore vincenti viene scritto da
            # pepe.py (_store_design_winner via _handle_learning_loop) come tipo
            # "design_winner" — effettivamente letto da Design e Finance.
            # "success_pattern" era ridondante e non letto da nessun agente.

            # Controlla se già esiste un pending_action per questo listing
            existing = await self.memory.get_pending_action("production_queue_proposal")
            if existing and existing.get("payload", {}).get("listing_id") == lid:
                continue

            payload = {
                "listing_id": lid,
                "listing_title": lst.get("title", ""),
                "niche": lst.get("niche", ""),
                "template": lst.get("template", ""),
                "product_type": lst.get("product_type", ""),
                "sales": lst.get("sales", 0),
                "revenue_eur": lst.get("revenue_eur", 0),
                "color_scheme": lst.get("color_scheme", ""),
            }
            await self.memory.save_pending_action(
                "production_queue_proposal", payload, expires_hours=24
            )

            title = lst.get("title", "")[:60]
            sales = lst.get("sales", 0)
            revenue = lst.get("revenue_eur", 0)
            msg = (
                f"💡 Opportunità variante identificata\n"
                f"📦 {title}\n"
                f"📊 {sales} vendite · €{revenue:.2f} revenue\n\n"
                f"Questo prodotto funziona. Prova una variante con\n"
                f"schema colore diverso o formato alternativo (es. Letter\n"
                f"invece di A4, o palette terracotta invece di sage).\n\n"
                f"Vuoi metterla in coda di produzione?\n"
                f"Rispondi \"sì\" per aggiungerla o \"no\" per ignorare.\n"
                f"(proposta valida 24 ore)\n\n"
                f"#bestseller #variante"
            )
            await self._notify_telegram(msg)

        return bestsellers

    async def _write_design_outcomes(
        self,
        niche: str,
        template: str,
        color_scheme: str,
        performance: str,
        summary: str,
    ) -> str | None:
        """Salva design outcome in ChromaDB per il learning loop."""
        return await self.memory.store_insight(
            text=(
                f"DESIGN_OUTCOME niche: {niche} | template: {template} | "
                f"color_scheme: {color_scheme} | performance: {performance} | "
                f"{summary}"
            ),
            metadata={
                "type": "design_outcome",
                "niche": niche,
                "template": template,
                "color_scheme": color_scheme,
                "performance": performance,
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            },
        )

    # ------------------------------------------------------------------
    # Passo 5 — Report aggregato
    # ------------------------------------------------------------------

    async def _build_report(
        self,
        listings: list[dict],
        synced: list[dict],
        failure_counts: dict,
        bestsellers: list[dict],
        today_str: str,
    ) -> dict:
        total_views = sum(s.get("views", 0) for s in synced)
        total_favorites = sum(s.get("favorites", 0) for s in synced)
        total_sales = sum(s.get("sales", 0) for s in synced)
        total_revenue = sum(s.get("revenue_eur", 0) for s in synced)

        # A/B performance
        all_listings = await self.memory.get_etsy_listings()
        ab_perf = {"A": {"count": 0, "views": 0, "sales": 0, "revenue": 0},
                   "B": {"count": 0, "views": 0, "sales": 0, "revenue": 0}}
        for l in all_listings:
            v = l.get("ab_price_variant")
            if v in ab_perf:
                ab_perf[v]["count"] += 1
                ab_perf[v]["views"] += l.get("views", 0)
                ab_perf[v]["sales"] += l.get("sales", 0)
                ab_perf[v]["revenue"] += l.get("revenue_eur", 0)

        for v in ab_perf:
            c = ab_perf[v]["count"]
            if c > 0:
                ab_perf[v]["avg_views"] = ab_perf[v]["views"] / c
                ab_perf[v]["avg_sales"] = ab_perf[v]["sales"] / c
                ab_perf[v]["avg_revenue"] = ab_perf[v]["revenue"] / c

        # Conversion rate per variante
        for v in ("A", "B"):
            v_views = ab_perf[v].get("views", 0)
            v_sales = ab_perf[v].get("sales", 0)
            ab_perf[v]["conversion_rate"] = round(v_sales / v_views, 4) if v_views > 0 else 0.0

        # Winner esplicito (solo se dati sufficienti)
        ab_winner = None
        ab_winner_confidence = "insufficient_data"

        a_conv = ab_perf["A"].get("conversion_rate", 0)
        b_conv = ab_perf["B"].get("conversion_rate", 0)
        a_count = ab_perf["A"].get("count", 0)
        b_count = ab_perf["B"].get("count", 0)

        if a_count >= 3 and b_count >= 3:
            if a_conv > b_conv * 1.1:
                ab_winner = "A"
                ab_winner_confidence = "low" if (a_count + b_count) < 10 else "medium"
            elif b_conv > a_conv * 1.1:
                ab_winner = "B"
                ab_winner_confidence = "low" if (a_count + b_count) < 10 else "medium"
            else:
                ab_winner = "inconclusive"
                ab_winner_confidence = "medium"

        ab_perf["winner"] = ab_winner
        ab_perf["winner_confidence"] = ab_winner_confidence

        # Delta views giornaliero (daily, non cumulativo)
        delta_views_today = 0
        try:
            for synced_item in synced:
                s_lid = synced_item["listing_id"]
                current_views = synced_item.get("views", 0)
                prev_views = await self.memory.get_listing_prev_views(s_lid)
                if prev_views is not None:
                    delta_views_today += max(0, current_views - prev_views)
        except Exception:
            delta_views_today = 0

        # Conteggi per status
        drafts = len([l for l in all_listings if l.get("status") == "draft"])
        active_count = len([l for l in all_listings if l.get("status") == "active"])

        return {
            "date": today_str,
            "total_listings_active": active_count,
            "total_views": total_views,
            "total_favorites": total_favorites,
            "total_sales": total_sales,
            "total_revenue_eur": total_revenue,
            "failures": failure_counts,
            "bestsellers": bestsellers,
            "ab_performance": ab_perf,
            "delta_views_vs_yesterday": delta_views_today,
            "drafts": drafts,
        }

    # ------------------------------------------------------------------
    # Passo 6 — Summary Telegram
    # ------------------------------------------------------------------

    async def _send_daily_summary(self, report: dict, date_str: str) -> None:
        total_views = report["total_views"]
        total_fav = report["total_favorites"]
        total_sales = report["total_sales"]
        total_rev = report["total_revenue_eur"]
        delta = report["delta_views_vs_yesterday"]
        active = report["total_listings_active"]
        drafts = report.get("drafts", 0)
        failures = report["failures"]
        tot_failures = sum(failures.values())

        # Bestseller
        if report["bestsellers"]:
            bs = report["bestsellers"][0]
            bs_line = f"{bs['title'][:40]} ({bs['sales']} vendite)"
        else:
            bs_line = "nessuno"

        # A/B test
        ab = report.get("ab_performance", {})
        ab_winner = ab.get("winner")
        if ab_winner and ab_winner != "inconclusive":
            ab_line = f"A/B: variante {ab_winner} vince ({ab.get('winner_confidence', '')} confidence)\n"
        elif ab_winner == "inconclusive":
            ab_line = "A/B: dati insufficienti\n"
        else:
            ab_line = ""

        # Failures con dettaglio
        failure_detail = ""
        if tot_failures:
            parts = []
            if failures.get("no_views"):
                parts.append(f"{failures['no_views']} senza views >7gg")
            if failures.get("no_conversion"):
                parts.append(f"{failures['no_conversion']} senza conversioni >45gg")
            failure_detail = f"Da ottimizzare: {', '.join(parts)}\n"

        delta_sign = f"+{delta}" if delta >= 0 else str(delta)

        msg = (
            f"Etsy — {date_str}\n"
            f"{'─' * 14}\n"
            f"Views: {total_views} ({delta_sign} vs ieri)  |  Favorites: {total_fav}\n"
            f"Vendite: {total_sales}  |  Revenue: €{total_rev:.2f}\n"
            f"Listing attivi: {active}  |  Bozze: {drafts}\n"
            f"{ab_line}"
            f"Bestseller: {bs_line}\n"
            f"{failure_detail}"
        ).rstrip()
        await self._notify_telegram(msg)

    # ------------------------------------------------------------------
    # Notifica Telegram
    # ------------------------------------------------------------------

    async def _notify_telegram(self, message: str) -> None:
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(message)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------

    def _calculate_analytics_confidence(
        self,
        listings: list[dict],
        synced: list[dict],
        failure_counts: dict,
    ) -> tuple[float, list[str]]:
        """Calcola confidence score per il report analytics."""
        missing: list[str] = []
        score = 0.0

        # 50% — sync success rate
        if listings:
            sync_rate = len(synced) / len(listings)
            score += 0.50 * sync_rate
            if sync_rate < 1.0:
                missing.append(f"{len(listings) - len(synced)} listing non sincronizzati")
        else:
            score += 0.50

        # 30% — sales data quality
        if synced:
            with_real_sales = sum(1 for s in synced if s.get("sales", 0) > 0)
            if with_real_sales > 0:
                score += 0.30
            else:
                score += 0.10
                missing.append("Tutte le vendite a 0 — verificare endpoint transazioni Etsy")
        else:
            score += 0.30

        # 20% — failure analysis eseguita
        score += 0.20

        return round(score, 2), missing

    # ------------------------------------------------------------------
    # Ladder System — polling performance (schedulato ogni 6h)
    # ------------------------------------------------------------------

    async def poll_listing_performance(self) -> None:
        """
        Inserisce snapshot in listing_performance per ogni listing pubblicato.
        Poi esegue run_ladder_diagnostic_all() e aggiorna il LearningLoop
        (se disponibile — wired in step 4.5).

        Schedulato: IntervalTrigger(hours=6)
        """
        if self._production_queue is None:
            logger.warning("poll_listing_performance: production_queue non configurato — skip")
            return

        published = await self._production_queue.get_recent(
            status="published", days=90, limit=200
        )
        if not published:
            logger.info("poll_listing_performance: nessun listing pubblicato")
            return

        db  = await self.memory.get_db()
        sem = asyncio.Semaphore(5)

        async def _poll_one(item) -> bool:
            if not item.etsy_listing_id:
                return False
            async with sem:
                try:
                    stats = await self.etsy_api.get_listing_stats(item.etsy_listing_id)
                except Exception as exc:
                    logger.warning("get_listing_stats %s fallito: %s", item.etsy_listing_id, exc)
                    return False

            days_live = 0
            if item.published_at:
                days_live = int((_time.time() - item.published_at) / 86400)

            views      = stats.get("views", 0)
            clicks     = stats.get("clicks", 0)
            favorites  = stats.get("favorites", 0)
            orders     = stats.get("num_orders", 0)
            revenue    = stats.get("revenue_eur", 0.0)
            ctr        = round(clicks / max(views, 1), 4)
            conv_rate  = round(orders / max(clicks, 1), 4)
            fav_rate   = round(favorites / max(views, 1), 4)

            # template + color_scheme: lookup dal listing memory per CTR attribution
            template     = ""
            color_scheme = ""
            try:
                listings = await self.memory.get_etsy_listings()
                ml = next(
                    (l for l in listings if str(l.get("listing_id")) == str(item.etsy_listing_id)),
                    None,
                )
                if ml:
                    template     = ml.get("template") or ""
                    color_scheme = ml.get("color_scheme") or ""
            except Exception:
                pass

            await db.execute(
                """
                INSERT INTO listing_performance
                    (etsy_listing_id, production_queue_id, niche, product_type,
                     template, color_scheme,
                     views, clicks, favorites, orders, revenue_eur,
                     ctr, conversion_rate, favorite_rate, days_live)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(item.etsy_listing_id), item.id, item.niche, item.product_type,
                    template, color_scheme,
                    views, clicks, favorites, orders, revenue,
                    ctr, conv_rate, fav_rate, days_live,
                ),
            )
            return True

        results = await asyncio.gather(
            *[_poll_one(i) for i in published], return_exceptions=True
        )
        success = sum(1 for r in results if r is True)
        await db.commit()
        logger.info("poll_listing_performance: %d/%d snapshot inseriti", success, len(published))

        await self.run_ladder_diagnostic_all()

        if self._learning_loop is not None:
            try:
                await self._learning_loop.update_niche_intelligence()
            except Exception as exc:
                logger.warning("update_niche_intelligence fallito: %s", exc)

    # ------------------------------------------------------------------
    # Ladder System — diagnostica
    # ------------------------------------------------------------------

    async def run_ladder_diagnostic_all(self) -> list[dict]:
        """
        Esegue diagnostica Ladder su tutti i listing pubblicati con ≥ MIN_DAYS_LIVE.
        Aggiorna ladder_level in listing_performance. Ritorna lista risultati.
        """
        if self._production_queue is None:
            return []
        published = await self._production_queue.get_recent(
            status="published", days=90, limit=200
        )
        results = []
        for item in published:
            result = await self.run_ladder_diagnostic_by_id(item.id)
            results.append(result)
        return results

    async def run_ladder_diagnostic_by_id(self, queue_item_id: int) -> dict:
        """
        Diagnostica Ladder per singolo listing (tramite production_queue id).
        Classifica: too_new | views_low | ctr_low | conv_low | ok.
        Innesca _trigger_remediation se l'azione non è stata tentata di recente.
        """
        if self._production_queue is None:
            return {"error": "production_queue non configurato"}

        item = await self._production_queue.get_item(queue_item_id)
        if not item:
            return {"error": f"item {queue_item_id} non trovato"}

        db     = await self.memory.get_db()
        cursor = await db.execute(
            """
            SELECT views, clicks, orders, ctr, conversion_rate, days_live,
                   template, color_scheme
            FROM listing_performance
            WHERE production_queue_id = ?
            ORDER BY snapshot_at DESC
            LIMIT 1
            """,
            (queue_item_id,),
        )
        row = await cursor.fetchone()

        if not row or row["days_live"] < MIN_DAYS_LIVE:
            return {
                "item_id": queue_item_id,
                "niche":   item.niche,
                "level":   "too_new",
                "action":  None,
            }

        # Classificazione a cascata (dal basso della ladder)
        if row["views"] < VIEWS_MIN_7DAYS:
            level  = "views_low"
            action = "rewrite_seo"
        elif row["ctr"] < CTR_MIN:
            level  = "ctr_low"
            action = "regen_thumbnail"
        elif row["conversion_rate"] < CONV_MIN and row["clicks"] >= 10:
            level  = "conv_low"
            action = "update_listing"
        else:
            level  = "ok"
            action = None

        # Persiste ladder_level nel record più recente
        await db.execute(
            """
            UPDATE listing_performance
            SET ladder_level = ?, last_diagnostic_at = unixepoch()
            WHERE production_queue_id = ?
              AND snapshot_at = (
                  SELECT MAX(snapshot_at) FROM listing_performance
                  WHERE production_queue_id = ?
              )
            """,
            (level, queue_item_id, queue_item_id),
        )
        await db.commit()

        if action and not self._remediation_attempted_recently(queue_item_id, action):
            await self._trigger_remediation(item, level, action, row)

        result = {
            "item_id":   queue_item_id,
            "niche":     item.niche,
            "level":     level,
            "action":    action,
            "views":     row["views"],
            "ctr":       f"{row['ctr'] * 100:.1f}%",
            "conv":      f"{row['conversion_rate'] * 100:.1f}%",
            "days_live": row["days_live"],
        }
        logger.info(
            "Ladder [%s] listing %s → %s (action: %s)",
            item.niche, item.etsy_listing_id, level, action,
        )
        return result

    async def _trigger_remediation(self, item: Any, level: str, action: str, row: Any) -> None:
        """
        Azione correttiva identificata dal Ladder System.

        Flusso per ogni action:
          rewrite_seo    → flag_for_seo_revision (abbassa score niche) +
                           crea nuovo pending_design (AutopilotLoop farà fresh research)
          regen_thumbnail → flag_low_ctr (ChromaDB — DesignAgent eviterà template/colore) +
                            crea nuovo pending_design (DesignAgent genererà thumbnail alternativa)
          update_listing  → solo notifica Telegram (richiede intervento manuale)

        Registra il tentativo in _remediation_log per evitare spam (cooldown 48h).
        """
        template     = row["template"]     if row["template"]     else ""
        color_scheme = row["color_scheme"] if row["color_scheme"] else ""
        title        = (item.listing_title or item.niche)[:60]
        queued_msg   = ""    # appendice al messaggio se item enqueued

        if action == "rewrite_seo":
            # 1. Segnala al LearningLoop: abbassa score per ridurre priorità niche
            if self._learning_loop is not None:
                try:
                    await self._learning_loop.flag_for_seo_revision(
                        item.niche, item.product_type
                    )
                except Exception as exc:
                    logger.warning("flag_for_seo_revision fallito: %s", exc)

            # 2. Enqueue nuovo pending_design — il pipeline farà fresh keyword research
            if self._production_queue is not None:
                try:
                    run_id = f"remediation_seo_{item.id}"
                    new_id = await self._production_queue.create_item(
                        niche=item.niche,
                        product_type=item.product_type,
                        keywords=item.keywords or [],
                        entry_score=0.85,      # alta priorità — listing esistente fallisce
                        loop_run_id=run_id,
                    )
                    queued_msg = f"\n🔄 Nuovo listing enqueued (#{new_id}) per SEO alternativo."
                    logger.info(
                        "Ladder rewrite_seo: enqueued item #%d per niche=%s",
                        new_id, item.niche,
                    )
                except Exception as exc:
                    logger.warning("create_item per rewrite_seo fallito: %s", exc)

            msg = (
                f"🔍 SEO revision needed\n"
                f"📦 {title}\n"
                f"📊 {row['views']} views dopo {row['days_live']} giorni"
                f" — soglia: {VIEWS_MIN_7DAYS}\n\n"
                f"Score niche abbassato — sarà ri-analizzata con meno priorità."
                f"{queued_msg}\n"
                f"#ladder #views_low"
            )

        elif action == "regen_thumbnail":
            # 1. Segnala al LearningLoop: template+color_scheme → low_ctr_signal in ChromaDB
            if self._learning_loop is not None:
                try:
                    await self._learning_loop.flag_low_ctr(
                        item.niche, item.product_type, template, color_scheme
                    )
                except Exception as exc:
                    logger.warning("flag_low_ctr fallito: %s", exc)

            # 2. Enqueue nuovo pending_design — DesignAgent leggerà low_ctr_signal
            #    e genererà thumbnail con template/colore diverso
            if self._production_queue is not None:
                try:
                    run_id = f"remediation_thumbnail_{item.id}"
                    new_id = await self._production_queue.create_item(
                        niche=item.niche,
                        product_type=item.product_type,
                        keywords=item.keywords or [],
                        entry_score=0.90,      # priorità massima — CTR critico
                        loop_run_id=run_id,
                    )
                    queued_msg = f"\n🔄 Nuovo listing enqueued (#{new_id}) con thumbnail alternativa."
                    logger.info(
                        "Ladder regen_thumbnail: enqueued item #%d per niche=%s template=%s/%s",
                        new_id, item.niche, template, color_scheme,
                    )
                except Exception as exc:
                    logger.warning("create_item per regen_thumbnail fallito: %s", exc)

            ctr_pct = f"{row['ctr'] * 100:.1f}%"
            msg = (
                f"🖼 Thumbnail update needed\n"
                f"📦 {title}\n"
                f"📊 CTR {ctr_pct} < soglia {CTR_MIN * 100:.0f}%"
                f" — thumbnail non converte\n\n"
                f"Template '{template}' / colore '{color_scheme}' segnalato come low-CTR."
                f" La prossima generazione lo eviterà."
                f"{queued_msg}\n"
                f"#ladder #ctr_low"
            )

        elif action == "update_listing":
            # Richiede intervento manuale — solo notifica
            msg = (
                f"📝 Listing update needed\n"
                f"📦 {title}\n"
                f"📊 Conv {row['conversion_rate'] * 100:.1f}% < soglia"
                f" {CONV_MIN * 100:.0f}% su {row['clicks']} click\n\n"
                f"Suggerimenti: più foto mockup, description benefits-first,"
                f" controlla prezzo vs competitor.\n"
                f"#ladder #conv_low"
            )

        else:
            return

        await self._notify_telegram(msg)
        self._log_remediation_attempt(item.id, action)

    def _remediation_attempted_recently(self, queue_item_id: int, action: str) -> bool:
        """
        True se lo stesso action è già stato notificato per questo item
        nelle ultime REMEDIATION_COOLDOWN_HOURS ore.
        """
        last_ts  = self._remediation_log.get(queue_item_id, {}).get(action, 0.0)
        cooldown = REMEDIATION_COOLDOWN_HOURS * 3600
        return (_time.time() - last_ts) < cooldown

    def _log_remediation_attempt(self, queue_item_id: int, action: str) -> None:
        """Registra timestamp dell'ultimo tentativo di remediation."""
        if queue_item_id not in self._remediation_log:
            self._remediation_log[queue_item_id] = {}
        self._remediation_log[queue_item_id][action] = _time.time()
