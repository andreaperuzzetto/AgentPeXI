"""AnalyticsAgent — sync stats Etsy, failure analysis, bestseller proposals."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, ClassVar, Coroutine

import anthropic

from apps.backend.agents._analytics.constants import (
    VIEWS_MIN_7DAYS,
    CTR_MIN,
    CONV_MIN,
    MIN_DAYS_LIVE,
    REMEDIATION_COOLDOWN_HOURS,
)
from apps.backend.agents._analytics.failure_mixin import _AnalyticsFailureMixin
from apps.backend.agents._analytics.bestsellers_mixin import _AnalyticsBestsellersMixin
from apps.backend.agents._analytics.reporting_mixin import _AnalyticsReportingMixin
from apps.backend.agents._analytics.diagnostics_mixin import _AnalyticsDiagnosticsMixin
from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU, settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentCard, AgentResult, AgentTask, TaskStatus

logger = logging.getLogger("agentpexi.analytics")

# Re-export constants so existing importers still work
__all__ = [
    "AnalyticsAgent",
    "VIEWS_MIN_7DAYS",
    "CTR_MIN",
    "CONV_MIN",
    "MIN_DAYS_LIVE",
    "REMEDIATION_COOLDOWN_HOURS",
]


class AnalyticsAgent(
    _AnalyticsDiagnosticsMixin,
    _AnalyticsReportingMixin,
    _AnalyticsBestsellersMixin,
    _AnalyticsFailureMixin,
    AgentBase,
):
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

