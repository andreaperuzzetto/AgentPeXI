"""FinanceAgent — cost tracking, margin analysis, ROI per niche, budget alerts.

Nessuna dipendenza da Etsy API: tutti i dati vengono da SQLite locale
(agent_logs, llm_calls, etsy_listings). Funziona anche prima dell'approvazione Etsy.

Target confidence: 88%+ con listing sincronizzati.
Con soli dati di costo (pre-Etsy): 45% — TaskStatus.PARTIAL.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Callable, ClassVar, Coroutine

import anthropic

from apps.backend.agents._finance._calculations_mixin import _CalculationsMixin
from apps.backend.agents._finance._context_mixin import _ContextMixin
from apps.backend.agents._finance._insights_mixin import _InsightsMixin
from apps.backend.agents._finance._reporting_mixin import _ReportingMixin
from apps.backend.agents._finance._roi_mixin import _RoiMixin
from apps.backend.agents._finance.constants import (
    USD_EUR_RATE,
    ETSY_TRANSACTION_FEE_PCT,
    ETSY_PAYMENT_FEE_PCT,
    ETSY_PAYMENT_FEE_FIXED_EUR,
    ETSY_LISTING_FEE_EUR,
    BUDGET_ALERT_EUR,
)
from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentCard, AgentResult, AgentTask, TaskStatus

logger = logging.getLogger("agentpexi.finance")

# Re-export constants so existing importers still work
__all__ = [
    "FinanceAgent",
    "USD_EUR_RATE",
    "ETSY_TRANSACTION_FEE_PCT",
    "ETSY_PAYMENT_FEE_PCT",
    "ETSY_PAYMENT_FEE_FIXED_EUR",
    "ETSY_LISTING_FEE_EUR",
    "BUDGET_ALERT_EUR",
]


class FinanceAgent(
    _ContextMixin,
    _ReportingMixin,
    _InsightsMixin,
    _RoiMixin,
    _CalculationsMixin,
    AgentBase,
):
    """Agente finanziario: costi LLM, revenue Etsy, margini netti, ROI per nicchia."""

    card: ClassVar[AgentCard] = AgentCard(
        name="finance",
        description="Report economico: costi LLM, revenue Etsy, margini, ROI per nicchia",
        input_schema={"period_days": "int = 30"},
        layer="business",
        llm="haiku",
        confidence_threshold=0.85,
    )

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        telegram_broadcaster: Callable | None = None,
    ) -> None:
        super().__init__(
            name="finance",
            model=MODEL_HAIKU,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )
        self._telegram_broadcast = telegram_broadcaster

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        period_days: int = task.input_data.get("period_days", 30)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # ----------------------------------------------------------------
        # Passo 1 — Costi LLM (sempre disponibili, no dipendenza Etsy)
        # ----------------------------------------------------------------
        costs_raw = await self.memory.get_cost_breakdown(period_days=period_days)
        costs_eur = self._usd_to_eur(costs_raw.get("total", 0.0))
        per_agent_costs_eur = {
            k: self._usd_to_eur(v)
            for k, v in costs_raw.get("per_agent", {}).items()
        }

        await self._log_step(
            "data_load",
            f"Costi LLM {period_days}gg: €{costs_eur:.4f} totale",
            output_data={
                "total_cost_usd": costs_raw.get("total", 0.0),
                "total_cost_eur": costs_eur,
                "per_agent": per_agent_costs_eur,
            },
        )

        # ----------------------------------------------------------------
        # Passo 2 — Revenue e listing (da SQLite locale)
        # ----------------------------------------------------------------
        revenue_stats = await self.memory.get_revenue_stats(period_days=period_days)
        total_revenue_eur: float = revenue_stats.get("total_revenue_eur", 0.0)
        total_sales: int = int(revenue_stats.get("total_sales", 0))
        active_count: int = int(revenue_stats.get("active_count", 0))
        avg_price_eur: float = revenue_stats.get("avg_price_eur", 0.0)

        await self._log_step(
            "data_load",
            f"Revenue {period_days}gg: €{total_revenue_eur:.2f} | {total_sales} vendite | {active_count} listing attivi",
            output_data=revenue_stats,
        )

        # ----------------------------------------------------------------
        # Passo 3 — Calcolo fee Etsy e margine netto
        # ----------------------------------------------------------------
        fees = self._calculate_etsy_fees(
            revenue_eur=total_revenue_eur,
            num_sales=total_sales,
            num_active_listings=active_count,
        )
        net_margin_eur = total_revenue_eur - fees["total_fees_eur"] - costs_eur
        gross_margin_eur = total_revenue_eur - fees["total_fees_eur"]
        net_margin_pct = (net_margin_eur / total_revenue_eur * 100) if total_revenue_eur > 0 else 0.0
        gross_margin_pct = (gross_margin_eur / total_revenue_eur * 100) if total_revenue_eur > 0 else 0.0
        roi_pct = (net_margin_eur / costs_eur * 100) if costs_eur > 0 else 0.0

        await self._log_step(
            "calculation",
            f"Margine netto: €{net_margin_eur:.2f} ({net_margin_pct:.1f}%) | ROI: {roi_pct:.1f}%",
            output_data={
                "fees": fees,
                "gross_margin_eur": round(gross_margin_eur, 4),
                "gross_margin_pct": round(gross_margin_pct, 2),
                "net_margin_eur": round(net_margin_eur, 4),
                "net_margin_pct": round(net_margin_pct, 2),
                "roi_pct": round(roi_pct, 2),
            },
        )

        # ----------------------------------------------------------------
        # Passo 4 — ROI per nicchia
        # ----------------------------------------------------------------
        niche_roi = await self._compute_niche_roi(period_days=period_days)

        await self._log_step(
            "calculation",
            f"ROI per nicchia: {len(niche_roi)} nicchie analizzate",
            output_data={"niche_roi": niche_roi[:5]},
        )

        # Scrivi niche_roi_snapshot + finance_insight per nicchia — leggibili da Research
        for niche_data in niche_roi:
            if not niche_data.get("niche"):
                continue

            niche_name = niche_data["niche"]

            # 1. niche_roi_snapshot — overview ROI (già da Block 4)
            snap_text = (
                f"Finance ROI snapshot nicchia '{niche_name}': "
                f"ROI {niche_data['roi_pct']:.1f}%, "
                f"{niche_data['total_sales']} vendite, "
                f"€{niche_data['net_margin_eur']:.4f} margine netto, "
                f"{niche_data['listing_count']} listing."
            )
            await self.memory.store_insight(snap_text, {
                "type": "niche_roi_snapshot",
                "niche": niche_name,
                "roi_pct": str(round(niche_data["roi_pct"], 2)),
                "total_sales": str(niche_data["total_sales"]),
                "net_margin_eur": str(round(niche_data["net_margin_eur"], 4)),
                "listing_count": str(niche_data["listing_count"]),
                "date": today_str,
                "agent": "finance",
            })

            # 2. finance_insight — economia di pricing (Break-even, costo per listing)
            #    Leggibile da Research durante la pricing analysis
            insight_text = (
                f"Finance insight nicchia '{niche_name}': "
                f"prezzo medio €{niche_data['avg_price_eur']:.2f}, "
                f"break-even a {niche_data['break_even_units']} vendite, "
                f"costo LLM per listing €{niche_data['cost_per_listing_eur']:.4f}, "
                f"ROI {niche_data['roi_pct']:.1f}%."
            )
            await self.memory.store_insight(insight_text, {
                "type": "finance_insight",
                "niche": niche_name,
                "avg_price_eur": str(round(niche_data["avg_price_eur"], 4)),
                "break_even_units": str(niche_data["break_even_units"]),
                "cost_per_listing_eur": str(round(niche_data["cost_per_listing_eur"], 6)),
                "roi_pct": str(round(niche_data["roi_pct"], 2)),
                "date": today_str,
                "agent": "finance",
            })

        # ----------------------------------------------------------------
        # Passo 5 — ROI per product_type
        # ----------------------------------------------------------------
        product_type_roi = await self._compute_product_type_roi(period_days=period_days)

        await self._log_step(
            "calculation",
            f"ROI per product_type: {len(product_type_roi)} tipi analizzati",
            output_data={"product_type_roi": product_type_roi[:5]},
        )

        # ----------------------------------------------------------------
        # Passo 6 — Breakdown costi per modello LLM
        # ----------------------------------------------------------------
        model_costs = await self.memory.get_model_cost_breakdown(period_days=period_days)
        model_costs_eur = [
            {**m, "cost_eur": self._usd_to_eur(m.get("total_cost_usd", 0.0))}
            for m in model_costs
        ]

        await self._log_step(
            "data_load",
            f"Breakdown modelli: {len(model_costs_eur)} modelli usati",
            output_data={"model_costs": model_costs_eur},
        )

        # ----------------------------------------------------------------
        # Passo 7 — Trend 7d vs 30d (confronto periodi)
        # ----------------------------------------------------------------
        trend = await self._compute_trend()

        await self._log_step(
            "calculation",
            f"Trend: rev 7d €{trend['revenue_7d']:.2f} vs 30d €{trend['revenue_30d']:.2f}",
            output_data=trend,
        )

        # ----------------------------------------------------------------
        # Passo 8 — Analisi LLM: cost efficiency (Haiku)
        # ----------------------------------------------------------------
        cost_insights = await self._generate_cost_insights(
            costs_eur=costs_eur,
            per_agent_costs_eur=per_agent_costs_eur,
            net_margin_eur=net_margin_eur,
            roi_pct=roi_pct,
            model_costs=model_costs_eur,
            period_days=period_days,
        )

        await self._log_step(
            "llm_analysis",
            "Cost efficiency analysis (Haiku)",
            output_data={"insights": str(cost_insights)[:300]},
        )

        # ----------------------------------------------------------------
        # Passo 8.5 — Leggi segnali upstream da ChromaDB
        # ----------------------------------------------------------------
        learning_context = await self._read_learning_context()
        await self._log_step(
            "data_load",
            f"Learning context: {len(learning_context['design_winners'])} design winner | "
            f"tasso fallimento publish {learning_context['failure_rate']:.1%} "
            f"({learning_context['failure_count']} falliti / "
            f"{learning_context['failure_count'] + learning_context['success_count']} tentativi)",
            output_data=learning_context,
        )

        # ----------------------------------------------------------------
        # Passo 9 — Analisi LLM: ROI e raccomandazioni (Sonnet)
        # ----------------------------------------------------------------
        roi_analysis = await self._generate_roi_analysis(
            niche_roi=niche_roi,
            product_type_roi=product_type_roi,
            trend=trend,
            net_margin_eur=net_margin_eur,
            roi_pct=roi_pct,
            period_days=period_days,
            learning_context=learning_context,
        )

        await self._log_step(
            "llm_analysis",
            "ROI + raccomandazioni strategiche (Sonnet)",
            output_data={"analysis": str(roi_analysis)[:300]},
        )

        # Scrivi finance_directive → ChromaDB (leggibile da Research)
        niches_to_scale = [
            n["niche"] for n in roi_analysis.get("top_niches_to_scale", []) if n.get("niche")
        ]
        niches_to_abandon = [
            n["niche"] for n in roi_analysis.get("niches_to_abandon", []) if n.get("niche")
        ]
        if niches_to_scale or niches_to_abandon:
            directive_text = (
                f"Finance directive {today_str}: "
                f"scale {' | '.join(niches_to_scale) if niches_to_scale else 'nessuna'}. "
                f"Abandon {' | '.join(niches_to_abandon) if niches_to_abandon else 'nessuna'}. "
                f"Strategia: {roi_analysis.get('strategic_recommendation', '')[:150]}"
            )
            await self.memory.store_insight(directive_text, {
                "type": "finance_directive",
                "niches_to_scale": "|".join(niches_to_scale),
                "niches_to_abandon": "|".join(niches_to_abandon),
                "date": today_str,
                "period_days": str(period_days),
                "agent": "finance",
            })
            await self._log_step(
                "tool_call",
                f"Finance directive salvata: scale={niches_to_scale}, abandon={niches_to_abandon}",
                output_data={"niches_to_scale": niches_to_scale, "niches_to_abandon": niches_to_abandon},
            )

        # ----------------------------------------------------------------
        # Passo 10 — Budget alert
        # ----------------------------------------------------------------
        alert_sent = await self._check_budget_alert(
            costs_eur=costs_eur,
            period_days=period_days,
        )

        # ----------------------------------------------------------------
        # Passo 11 — Report finale + ChromaDB
        # ----------------------------------------------------------------
        report = self._build_report(
            today_str=today_str,
            period_days=period_days,
            costs_eur=costs_eur,
            per_agent_costs_eur=per_agent_costs_eur,
            fees=fees,
            total_revenue_eur=total_revenue_eur,
            total_sales=total_sales,
            active_count=active_count,
            avg_price_eur=avg_price_eur,
            gross_margin_eur=gross_margin_eur,
            gross_margin_pct=gross_margin_pct,
            net_margin_eur=net_margin_eur,
            net_margin_pct=net_margin_pct,
            roi_pct=roi_pct,
            niche_roi=niche_roi,
            product_type_roi=product_type_roi,
            model_costs=model_costs_eur,
            trend=trend,
            cost_insights=cost_insights,
            roi_analysis=roi_analysis,
            budget_alert_sent=alert_sent,
        )

        await self.memory.store_insight(
            text=json.dumps(report, ensure_ascii=False, default=str),
            metadata={
                "type": "finance_report",
                "date": today_str,
                "agent": "finance",
                "period_days": str(period_days),
            },
        )

        await self._log_step(
            "tool_call",
            "Report finance salvato in ChromaDB",
            output_data={"date": today_str},
        )

        # ----------------------------------------------------------------
        # Passo 12 — Telegram summary
        # ----------------------------------------------------------------
        await self._send_finance_summary(report, today_str)

        # ----------------------------------------------------------------
        # Confidence scoring
        # ----------------------------------------------------------------
        confidence, missing_data = self._calculate_finance_confidence(
            costs_eur=costs_eur,
            revenue_stats=revenue_stats,
            niche_roi=niche_roi,
            model_costs=model_costs_eur,
            trend=trend,
        )

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED if confidence >= 0.60 else TaskStatus.PARTIAL,
            output_data=report,
            confidence=confidence,
            missing_data=missing_data,
            reply_voice="Report finanziario pronto, controlla il pannello e Telegram.",
        )

