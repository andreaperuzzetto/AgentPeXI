"""AnalyticsAgent — sync stats Etsy, failure analysis, bestseller proposals."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable, Coroutine

import anthropic

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU, MODEL_SONNET
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus

logger = logging.getLogger("agentpexi.analytics")


class AnalyticsAgent(AgentBase):
    """Agente analytics: sync stats, failure analysis, bestseller proposals."""

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        etsy_api: Any,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        telegram_broadcaster: Callable | None = None,
    ) -> None:
        super().__init__(
            name="analytics",
            model=MODEL_HAIKU,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )
        self.etsy_api = etsy_api
        self._telegram_broadcast = telegram_broadcaster

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")

        # --- Passo 1 — Lettura listing attivi ---
        listings = await self.memory.get_etsy_listings(status="active")
        if not listings:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.COMPLETED,
                output_data={"message": "Nessun listing attivo da sincronizzare"},
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
                        listing_id=int(lid),
                    )
                except Exception as exc:
                    logger.warning("Sync listing %s fallito: %s", lid, exc)
                    return None

            views = data.get("views", 0)
            favorites = data.get("num_favorers", 0)
            sales = data.get("quantity", 0) - data.get("quantity", 0)  # fallback
            # Etsy non ha un campo quantity_sold diretto; approssimiamo
            if "quantity_sold" in data:
                sales = data["quantity_sold"]
            elif "num_sold" in data:
                sales = data["num_sold"]
            status = data.get("state", "active")
            price = float(data.get("price", {}).get("amount", 0)) / 100 if isinstance(data.get("price"), dict) else float(data.get("price", 0))
            revenue_eur = sales * price

            now_iso = datetime.utcnow().isoformat()
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

        # --- Passo 3 — Failure analysis ---
        failure_counts = {"no_views": 0, "no_conversion": 0, "no_views_no_sales": 0}

        # Caso A: no views dopo 7 giorni
        no_views = await self.memory.get_listings_no_views(days=7)
        for lst in no_views:
            await self._analyze_no_views(lst)
            failure_counts["no_views"] += 1

        # Caso B: no conversion dopo 45 giorni
        no_conv = await self.memory.get_listings_no_conversion(days=45)
        for lst in no_conv:
            await self._analyze_no_conversion(lst)
            failure_counts["no_conversion"] += 1

        # Caso C: no views no sales dopo 45 giorni
        no_both = await self.memory.get_listings_no_views_no_sales(days=45)
        for lst in no_both:
            await self._analyze_no_views_no_sales(lst)
            failure_counts["no_views_no_sales"] += 1

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

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data=report,
        )

    # ------------------------------------------------------------------
    # Caso A — No views
    # ------------------------------------------------------------------

    async def _analyze_no_views(self, listing: dict) -> None:
        lid = listing["listing_id"]
        await self.memory.flag_no_views(lid)

        analysis = await self._failure_llm(
            prompt=self._no_views_prompt(listing),
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
            f"📊 7 giorni · 0 visualizzazioni\n"
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
            f"Questo listing Etsy non ha ricevuto nessuna visualizzazione dopo 7 giorni.\n"
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
        await self.memory.flag_no_conversion(lid)

        analysis = await self._failure_llm(
            prompt=self._no_conversion_prompt(listing),
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
        await self.memory.flag_no_views_no_sales(lid)

        analysis = await self._failure_llm(
            prompt=self._no_views_no_sales_prompt(listing),
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

    async def _failure_llm(self, prompt: str) -> dict | None:
        """Chiama Sonnet per failure analysis, parsa JSON."""
        response_text = await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="Sei un analista esperto di Etsy marketplace. Analizza i problemi dei listing e suggerisci azioni concrete.",
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
        today = datetime.utcnow().strftime("%Y-%m-%d")

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
        """Identifica bestseller (sales >= 10), propone varianti via pending_action."""
        all_listings = await self.memory.get_etsy_listings(status="active")
        top = sorted(
            [l for l in all_listings if (l.get("sales") or 0) >= 10],
            key=lambda x: x.get("revenue_eur", 0),
            reverse=True,
        )[:3]

        bestsellers = []
        for lst in top:
            lid = lst["listing_id"]
            bestsellers.append({
                "listing_id": lid,
                "title": lst.get("title", ""),
                "sales": lst.get("sales", 0),
                "revenue_eur": lst.get("revenue_eur", 0),
            })

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

        # Delta views vs ieri
        delta_views = 0
        try:
            prev_reports = await self.memory.query_chromadb(
                query="analytics_report",
                n_results=1,
                where={"type": "analytics_report"},
            )
            if prev_reports:
                prev = json.loads(prev_reports[0]["document"])
                delta_views = total_views - prev.get("total_views", 0)
        except Exception:
            pass

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
            "delta_views_vs_yesterday": delta_views,
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

        bs_line = "—"
        if report["bestsellers"]:
            bs = report["bestsellers"][0]
            bs_line = f"{bs['title'][:35]} — {bs['sales']} vendite"

        msg = (
            f"📊 Report Etsy — {date_str}\n"
            f"─────────────────────\n"
            f"👁 Views: {total_views} ({delta:+d} vs ieri)\n"
            f"❤️ Favorites: {total_fav}\n"
            f"🛒 Vendite: {total_sales}\n"
            f"💰 Revenue: €{total_rev:.2f}\n\n"
            f"🏆 Bestseller: {bs_line}\n"
            f"📋 Attivi: {active} | Bozze: {drafts}\n"
            f"⚠️ Da ottimizzare: {tot_failures}\n\n"
            f"#analytics #daily"
        )
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
