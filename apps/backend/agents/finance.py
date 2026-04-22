"""FinanceAgent — cost tracking, margin analysis, ROI per niche, budget alerts.

Nessuna dipendenza da Etsy API: tutti i dati vengono da SQLite locale
(agent_logs, llm_calls, etsy_listings). Funziona anche prima dell'approvazione Etsy.

Target confidence: 88%+ con listing sincronizzati.
Con soli dati di costo (pre-Etsy): 45% — TaskStatus.PARTIAL.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ClassVar, Coroutine

import anthropic

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU, MODEL_SONNET, settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentCard, AgentResult, AgentTask, TaskStatus

logger = logging.getLogger("agentpexi.finance")


# ---------------------------------------------------------------------------
# Costanti Etsy fee structure (2024)
# ---------------------------------------------------------------------------

USD_EUR_RATE: float = settings.USD_EUR_RATE  # centralizzato in config.py / .env
ETSY_TRANSACTION_FEE_PCT: float = 0.065    # 6.5% su prezzo di vendita
ETSY_PAYMENT_FEE_PCT: float = 0.030        # 3% Etsy Payments
ETSY_PAYMENT_FEE_FIXED_EUR: float = 0.23  # ~€0.25 per transazione (fisso)
ETSY_LISTING_FEE_EUR: float = 0.18        # ~€0.20 per listing pubblicato (one-time)

# Budget alert: legge da settings, fallback 70 €
BUDGET_ALERT_EUR: float = getattr(settings, "COST_ALERT_THRESHOLD_EUR", 70.0)


class FinanceAgent(AgentBase):
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

        # Scrivi niche_roi_snapshot per nicchia — leggibili da Research
        for niche_data in niche_roi:
            if niche_data.get("niche"):
                snap_text = (
                    f"Finance ROI snapshot nicchia '{niche_data['niche']}': "
                    f"ROI {niche_data['roi_pct']:.1f}%, "
                    f"{niche_data['total_sales']} vendite, "
                    f"€{niche_data['net_margin_eur']:.4f} margine netto, "
                    f"{niche_data['listing_count']} listing."
                )
                await self.memory.store_insight(snap_text, {
                    "type": "niche_roi_snapshot",
                    "niche": niche_data["niche"],
                    "roi_pct": str(round(niche_data["roi_pct"], 2)),
                    "total_sales": str(niche_data["total_sales"]),
                    "net_margin_eur": str(round(niche_data["net_margin_eur"], 4)),
                    "listing_count": str(niche_data["listing_count"]),
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

    # ------------------------------------------------------------------
    # Fee calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _usd_to_eur(usd: float) -> float:
        return round(usd * USD_EUR_RATE, 6)

    @staticmethod
    def _calculate_etsy_fees(
        revenue_eur: float,
        num_sales: int,
        num_active_listings: int,
    ) -> dict:
        """
        Calcola fee Etsy reali:
        - transaction fee: 6.5% revenue
        - payment processing: 3% + €0.23/transazione
        - listing fee: €0.18/listing pubblicato (one-time nel periodo)

        Nota: listing_fee qui è approssimato al numero di listing attivi nel periodo.
        Il costo reale è per ogni rinnovo (ogni 4 mesi su Etsy).
        """
        transaction_fee = revenue_eur * ETSY_TRANSACTION_FEE_PCT
        payment_fee_pct = revenue_eur * ETSY_PAYMENT_FEE_PCT
        payment_fee_fixed = num_sales * ETSY_PAYMENT_FEE_FIXED_EUR
        listing_fee = num_active_listings * ETSY_LISTING_FEE_EUR

        total_fees = transaction_fee + payment_fee_pct + payment_fee_fixed + listing_fee

        return {
            "transaction_fee_eur": round(transaction_fee, 4),
            "payment_fee_pct_eur": round(payment_fee_pct, 4),
            "payment_fee_fixed_eur": round(payment_fee_fixed, 4),
            "listing_fee_eur": round(listing_fee, 4),
            "total_fees_eur": round(total_fees, 4),
            "effective_fee_pct": round(
                total_fees / revenue_eur * 100 if revenue_eur > 0 else 0.0, 2
            ),
        }

    # ------------------------------------------------------------------
    # ROI per niche / product_type
    # ------------------------------------------------------------------

    async def _compute_niche_roi(self, period_days: int) -> list[dict]:
        """
        Calcola ROI per nicchia: revenue vs costo LLM attribuito proporzionalmente.

        Il costo LLM viene distribuito per listing count (proxy del lavoro svolto).
        ROI = (revenue_nette_fees - costo_llm_attribuito) / costo_llm_attribuito * 100
        """
        niches = await self.memory.get_revenue_by_niche(period_days=period_days)
        costs_raw = await self.memory.get_cost_breakdown(period_days=period_days)
        total_costs_eur = self._usd_to_eur(costs_raw.get("total", 0.0))

        total_listings = sum(n.get("listing_count", 0) for n in niches) or 1

        result = []
        for niche in niches:
            rev = niche.get("total_revenue_eur", 0.0)
            sales = niche.get("total_sales", 0)
            count = niche.get("listing_count", 0)

            # Quota costo LLM attribuita proporzionalmente ai listing
            llm_cost_attributed = total_costs_eur * (count / total_listings)

            # Fee Etsy proporzionate
            fees = self._calculate_etsy_fees(
                revenue_eur=rev,
                num_sales=sales,
                num_active_listings=count,
            )
            net_rev = rev - fees["total_fees_eur"]
            net_margin = net_rev - llm_cost_attributed
            roi = (net_margin / llm_cost_attributed * 100) if llm_cost_attributed > 0 else 0.0

            result.append({
                "niche": niche.get("niche", ""),
                "listing_count": count,
                "total_sales": sales,
                "total_revenue_eur": round(rev, 4),
                "etsy_fees_eur": fees["total_fees_eur"],
                "llm_cost_attributed_eur": round(llm_cost_attributed, 4),
                "net_margin_eur": round(net_margin, 4),
                "roi_pct": round(roi, 2),
                "avg_price_eur": niche.get("avg_price_eur", 0.0),
            })

        # Ordina per ROI decrescente
        result.sort(key=lambda x: x["roi_pct"], reverse=True)
        return result

    async def _compute_product_type_roi(self, period_days: int) -> list[dict]:
        """ROI per product_type: stesso calcolo per niche."""
        types = await self.memory.get_revenue_by_product_type(period_days=period_days)
        costs_raw = await self.memory.get_cost_breakdown(period_days=period_days)
        total_costs_eur = self._usd_to_eur(costs_raw.get("total", 0.0))
        total_listings = sum(t.get("listing_count", 0) for t in types) or 1

        result = []
        for pt in types:
            rev = pt.get("total_revenue_eur", 0.0)
            sales = pt.get("total_sales", 0)
            count = pt.get("listing_count", 0)

            llm_cost = total_costs_eur * (count / total_listings)
            fees = self._calculate_etsy_fees(
                revenue_eur=rev,
                num_sales=sales,
                num_active_listings=count,
            )
            net_rev = rev - fees["total_fees_eur"]
            net_margin = net_rev - llm_cost
            roi = (net_margin / llm_cost * 100) if llm_cost > 0 else 0.0

            result.append({
                "product_type": pt.get("product_type", ""),
                "listing_count": count,
                "total_sales": sales,
                "total_revenue_eur": round(rev, 4),
                "etsy_fees_eur": fees["total_fees_eur"],
                "llm_cost_attributed_eur": round(llm_cost, 4),
                "net_margin_eur": round(net_margin, 4),
                "roi_pct": round(roi, 2),
            })

        result.sort(key=lambda x: x["roi_pct"], reverse=True)
        return result

    # ------------------------------------------------------------------
    # Trend 7d vs 30d
    # ------------------------------------------------------------------

    async def _compute_trend(self) -> dict:
        """Confronto revenue e costi negli ultimi 7 vs 30 giorni."""
        rev_7 = await self.memory.get_revenue_stats(period_days=7)
        rev_30 = await self.memory.get_revenue_stats(period_days=30)
        cost_7 = await self.memory.get_cost_breakdown(period_days=7)
        cost_30 = await self.memory.get_cost_breakdown(period_days=30)

        rev_7d = rev_7.get("total_revenue_eur", 0.0)
        rev_30d = rev_30.get("total_revenue_eur", 0.0)
        cost_7d = self._usd_to_eur(cost_7.get("total", 0.0))
        cost_30d = self._usd_to_eur(cost_30.get("total", 0.0))

        # Annualizza i 7gg per confronto equo (× 30/7)
        rev_7d_normalized = rev_7d * (30 / 7)
        cost_7d_normalized = cost_7d * (30 / 7)

        rev_delta_pct = (
            (rev_7d_normalized - rev_30d) / rev_30d * 100 if rev_30d > 0 else 0.0
        )
        cost_delta_pct = (
            (cost_7d_normalized - cost_30d) / cost_30d * 100 if cost_30d > 0 else 0.0
        )

        # Daily revenue trend (per grafico)
        daily_trend = await self.memory.get_daily_revenue_trend(period_days=30)

        return {
            "revenue_7d": round(rev_7d, 4),
            "revenue_30d": round(rev_30d, 4),
            "revenue_7d_normalized_30d": round(rev_7d_normalized, 4),
            "revenue_delta_pct": round(rev_delta_pct, 2),
            "cost_7d": round(cost_7d, 6),
            "cost_30d": round(cost_30d, 6),
            "cost_7d_normalized_30d": round(cost_7d_normalized, 6),
            "cost_delta_pct": round(cost_delta_pct, 2),
            "daily_revenue": daily_trend,
            "sales_7d": rev_7.get("total_sales", 0),
            "sales_30d": rev_30.get("total_sales", 0),
        }

    # ------------------------------------------------------------------
    # LLM — Cost efficiency analysis (Haiku, veloce)
    # ------------------------------------------------------------------

    async def _generate_cost_insights(
        self,
        costs_eur: float,
        per_agent_costs_eur: dict,
        net_margin_eur: float,
        roi_pct: float,
        model_costs: list[dict],
        period_days: int,
    ) -> dict:
        """
        Analisi efficienza costi con Claude Haiku.
        Ritorna: agent_efficiency (dict), top_cost_concern (str), optimize_suggestion (str).
        Fallback su dati calcolati se il parsing JSON fallisce.
        """
        agents_str = "\n".join(
            f"  - {k}: €{v:.4f}" for k, v in sorted(
                per_agent_costs_eur.items(), key=lambda x: x[1], reverse=True
            )
        ) or "  (nessun dato agente)"

        models_str = "\n".join(
            f"  - {m['model']}: €{m['cost_eur']:.4f} | {m['call_count']} chiamate"
            for m in model_costs
        ) or "  (nessun dato modello)"

        system = (
            "Sei un analista finanziario specializzato in AI cost optimization. "
            "Rispondi SOLO con JSON valido, nessun testo esterno al JSON."
        )

        prompt = (
            f"Analizza l'efficienza dei costi LLM per un sistema multi-agente Etsy.\n\n"
            f"Periodo: {period_days} giorni\n"
            f"Costo LLM totale: €{costs_eur:.4f}\n"
            f"Margine netto: €{net_margin_eur:.2f}\n"
            f"ROI: {roi_pct:.1f}%\n\n"
            f"Costi per agente:\n{agents_str}\n\n"
            f"Costi per modello:\n{models_str}\n\n"
            f"Budget soglia alert: €{BUDGET_ALERT_EUR:.2f}/mese\n\n"
            f'Rispondi SOLO con JSON:\n'
            f'{{\n'
            f'  "agent_efficiency": {{\n'
            f'    "agente_piu_costoso": "nome agente",\n'
            f'    "percentuale_costo_totale": 0.0,\n'
            f'    "valutazione": "efficiente|accettabile|da_ottimizzare"\n'
            f'  }},\n'
            f'  "modello_ottimale": "quale modello usare di più e perché, max 80 caratteri",\n'
            f'  "top_cost_concern": "principale preoccupazione costi, max 100 caratteri",\n'
            f'  "optimize_suggestion": "azione immediata per ridurre costi, max 100 caratteri",\n'
            f'  "burn_rate_monthly_eur": 0.0\n'
            f'}}'
        )

        try:
            raw = await self._call_llm(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=system,
                model_override=MODEL_HAIKU,
                max_tokens=512,
            )
            parsed = self._parse_json_response(raw)
            if parsed:
                return parsed
        except Exception as exc:
            logger.warning("Cost insights LLM fallito: %s", exc)

        # Fallback deterministico
        most_expensive = max(per_agent_costs_eur, key=per_agent_costs_eur.get) if per_agent_costs_eur else "n/a"
        max_cost = per_agent_costs_eur.get(most_expensive, 0.0)
        pct = (max_cost / costs_eur * 100) if costs_eur > 0 else 0.0
        burn_rate = costs_eur / period_days * 30

        return {
            "agent_efficiency": {
                "agente_piu_costoso": most_expensive,
                "percentuale_costo_totale": round(pct, 1),
                "valutazione": "accettabile" if roi_pct > 0 else "da_ottimizzare",
            },
            "modello_ottimale": f"Haiku per task ripetitivi (costo minore)",
            "top_cost_concern": f"Burn rate €{burn_rate:.2f}/mese vs soglia €{BUDGET_ALERT_EUR:.2f}",
            "optimize_suggestion": "Aumentare uso Haiku per task di analisi dati",
            "burn_rate_monthly_eur": round(burn_rate, 4),
        }

    # ------------------------------------------------------------------
    # LLM — ROI analysis + raccomandazioni strategiche (Sonnet)
    # ------------------------------------------------------------------

    async def _generate_roi_analysis(
        self,
        niche_roi: list[dict],
        product_type_roi: list[dict],
        trend: dict,
        net_margin_eur: float,
        roi_pct: float,
        period_days: int,
        learning_context: dict | None = None,
    ) -> dict:
        """
        Analisi strategica ROI con Sonnet: quali nicchie prioritizzare,
        quali abbandonare, previsione trend.
        Ritorna: top_niches, underperforming_niches, strategic_recommendation, forecast.
        """
        top_niches = niche_roi[:5]
        worst_niches = [n for n in niche_roi if n["roi_pct"] < 0][-3:]

        niches_str = "\n".join(
            f"  {i+1}. {n['niche']}: ROI {n['roi_pct']:.1f}% | "
            f"rev €{n['total_revenue_eur']:.2f} | {n['total_sales']} vendite"
            for i, n in enumerate(top_niches)
        ) or "  (nessun dato nicchia)"

        worst_str = "\n".join(
            f"  - {n['niche']}: ROI {n['roi_pct']:.1f}% | rev €{n['total_revenue_eur']:.2f}"
            for n in worst_niches
        ) or "  (nessuna nicchia negativa)"

        pt_str = "\n".join(
            f"  - {p['product_type']}: ROI {p['roi_pct']:.1f}% | {p['listing_count']} listing"
            for p in product_type_roi[:4]
        ) or "  (nessun dato product_type)"

        rev_trend = "in crescita" if trend["revenue_delta_pct"] > 5 else \
                    "stabile" if abs(trend["revenue_delta_pct"]) <= 5 else "in calo"

        # Sezione design winners (da learning context)
        lc = learning_context or {}
        winners = lc.get("design_winners", [])
        failure_rate = lc.get("failure_rate", 0.0)
        failure_count = lc.get("failure_count", 0)
        success_count = lc.get("success_count", 0)

        winners_str = ""
        if winners:
            winners_str = "\n\n## Design winner confermati (template/colore che hanno generato vendite)\n"
            for w in winners[:6]:
                winners_str += (
                    f"  - Niche '{w['niche']}': template '{w['template']}', "
                    f"schema '{w['color_scheme']}' — {w['sales']} vendite, {w['views']} views\n"
                )

        publish_str = ""
        if failure_count + success_count > 0:
            publish_str = (
                f"\n\n## Efficienza deploy listing\n"
                f"  Tentativi totali: {failure_count + success_count} | "
                f"Successi: {success_count} | Fallimenti: {failure_count} | "
                f"Tasso fallimento: {failure_rate:.1%}\n"
                f"  Nota: i fallimenti riducono la revenue potenziale effettiva."
            )

        system = (
            "Sei un consulente strategico e-commerce specializzato in Etsy e digital products. "
            "Analisi concisa, orientata all'azione. "
            "Rispondi SOLO con JSON valido, nessun testo esterno al JSON."
        )

        prompt = (
            f"Analisi ROI strategica per shop Etsy digital products.\n\n"
            f"Periodo: {period_days} giorni\n"
            f"ROI globale: {roi_pct:.1f}%\n"
            f"Margine netto: €{net_margin_eur:.2f}\n"
            f"Trend revenue: {rev_trend} ({trend['revenue_delta_pct']:+.1f}% vs periodo precedente)\n\n"
            f"TOP nicchie per ROI:\n{niches_str}\n\n"
            f"Nicchie negative:\n{worst_str}\n\n"
            f"Product types:\n{pt_str}"
            f"{winners_str}"
            f"{publish_str}\n\n"
            f'Rispondi SOLO con JSON:\n'
            f'{{\n'
            f'  "top_niches_to_scale": [\n'
            f'    {{"niche": "nome", "reason": "perché scalare, max 80 caratteri"}}\n'
            f'  ],\n'
            f'  "niches_to_abandon": [\n'
            f'    {{"niche": "nome", "reason": "perché abbandonare, max 80 caratteri"}}\n'
            f'  ],\n'
            f'  "best_product_type": "product_type più redditizio, max 60 caratteri",\n'
            f'  "strategic_recommendation": "azione principale per massimizzare ROI, max 150 caratteri",\n'
            f'  "forecast_30d": {{\n'
            f'    "revenue_eur": 0.0,\n'
            f'    "confidence": "low|medium|high",\n'
            f'    "assumption": "base della previsione, max 80 caratteri"\n'
            f'  }}\n'
            f'}}'
        )

        try:
            raw = await self._call_llm(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=system,
                model_override=MODEL_SONNET,
                max_tokens=1024,
            )
            parsed = self._parse_json_response(raw)
            if parsed:
                return parsed
        except Exception as exc:
            logger.warning("ROI analysis LLM fallito: %s", exc)

        # Fallback deterministico
        best_niche = top_niches[0]["niche"] if top_niches else "n/a"
        worst_niche = worst_niches[0]["niche"] if worst_niches else "n/a"
        best_pt = product_type_roi[0]["product_type"] if product_type_roi else "n/a"
        rev_forecast = trend["revenue_30d"] * (1 + trend["revenue_delta_pct"] / 100)

        return {
            "top_niches_to_scale": [
                {"niche": best_niche, "reason": "ROI più alto nel periodo"}
            ] if best_niche != "n/a" else [],
            "niches_to_abandon": [
                {"niche": worst_niche, "reason": "ROI negativo persistente"}
            ] if worst_niche != "n/a" else [],
            "best_product_type": best_pt,
            "strategic_recommendation": (
                f"Aumentare produzione in {best_niche} e ridurre risorse su nicchie ROI negativo"
                if best_niche != "n/a" else "Dati insufficienti per raccomandazione"
            ),
            "forecast_30d": {
                "revenue_eur": round(max(0.0, rev_forecast), 2),
                "confidence": "low",
                "assumption": "Proiezione lineare dal trend 7d/30d",
            },
        }

    # ------------------------------------------------------------------
    # Budget alert
    # ------------------------------------------------------------------

    async def _check_budget_alert(
        self,
        costs_eur: float,
        period_days: int,
    ) -> bool:
        """
        Invia alert Telegram se i costi LLM superano la soglia mensile.
        Usa pending_actions per evitare duplicati nelle ultime 24h.
        Ritorna True se l'alert è stato inviato.
        """
        if costs_eur <= 0:
            return False

        # Normalizza a 30gg per confronto con threshold mensile
        monthly_equivalent = costs_eur / period_days * 30

        if monthly_equivalent < BUDGET_ALERT_EUR:
            return False

        # Controlla se alert già inviato nelle ultime 24h
        existing = await self.memory.get_pending_action("finance_budget_alert")
        if existing:
            return False

        pct_used = monthly_equivalent / BUDGET_ALERT_EUR * 100
        msg = (
            f"🚨 Budget Alert — Costi LLM\n"
            f"─────────────────────\n"
            f"💸 Costo periodo ({period_days}gg): €{costs_eur:.4f}\n"
            f"📊 Equivalente mensile: €{monthly_equivalent:.4f}\n"
            f"⚠️ Soglia: €{BUDGET_ALERT_EUR:.2f}/mese\n"
            f"📈 Utilizzo: {pct_used:.1f}% del budget\n\n"
            f"Azione: verifica agent_logs per agenti ad alto consumo.\n"
            f"#budget #alert #finance"
        )
        await self._notify_telegram(msg)

        # Salva pending_action per dedup (valido 24h)
        await self.memory.save_pending_action(
            "finance_budget_alert",
            {
                "costs_eur": costs_eur,
                "monthly_equivalent": monthly_equivalent,
                "threshold": BUDGET_ALERT_EUR,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
            },
            expires_hours=24,
        )

        return True

    # ------------------------------------------------------------------
    # Learning context reader (upstream ChromaDB signals)
    # ------------------------------------------------------------------

    async def _read_learning_context(self) -> dict:
        """
        Legge da ChromaDB i segnali prodotti dagli agenti upstream.

        Reads:
          - design_winner: combinazioni template/colore che hanno venduto
          - publish_failure / publish_success: tasso di fallimento deploy

        Returns:
            {
                "design_winners": list[dict],   # niche, template, color_scheme, sales, views
                "failure_count": int,
                "success_count": int,
                "failure_rate": float,           # 0.0-1.0
            }
        """
        design_winners: list[dict] = []
        failure_count = 0
        success_count = 0

        try:
            winners_raw = await self.memory.query_chromadb_recent(
                query="design winner best selling template niche",
                n_results=10,
                where={"type": {"$eq": "design_winner"}},
                primary_days=30,
                fallback_days=90,
            )
            for doc in (winners_raw or []):
                meta = doc.get("metadata", {})
                if meta.get("niche") and meta.get("template"):
                    design_winners.append({
                        "niche": meta["niche"],
                        "template": meta["template"],
                        "color_scheme": meta.get("color_scheme", ""),
                        "sales": meta.get("sales", "0"),
                        "views": meta.get("views", "0"),
                    })
        except Exception as exc:
            logger.warning("Finance: errore lettura design_winner: %s", exc)

        try:
            failures_raw = await self.memory.query_chromadb_recent(
                query="publish failure skipped error listing",
                n_results=50,
                where={"type": {"$eq": "publish_failure"}},
                primary_days=30,
                fallback_days=90,
            )
            failure_count = len(failures_raw or [])
        except Exception as exc:
            logger.warning("Finance: errore lettura publish_failure: %s", exc)

        try:
            successes_raw = await self.memory.query_chromadb_recent(
                query="publish success listing published etsy",
                n_results=50,
                where={"type": {"$eq": "publish_success"}},
                primary_days=30,
                fallback_days=90,
            )
            success_count = len(successes_raw or [])
        except Exception as exc:
            logger.warning("Finance: errore lettura publish_success: %s", exc)

        total_attempts = failure_count + success_count
        failure_rate = round(failure_count / total_attempts, 3) if total_attempts > 0 else 0.0

        return {
            "design_winners": design_winners,
            "failure_count": failure_count,
            "success_count": success_count,
            "failure_rate": failure_rate,
        }

    # ------------------------------------------------------------------
    # Report builder
    # ------------------------------------------------------------------

    def _build_report(
        self,
        today_str: str,
        period_days: int,
        costs_eur: float,
        per_agent_costs_eur: dict,
        fees: dict,
        total_revenue_eur: float,
        total_sales: int,
        active_count: int,
        avg_price_eur: float,
        gross_margin_eur: float,
        gross_margin_pct: float,
        net_margin_eur: float,
        net_margin_pct: float,
        roi_pct: float,
        niche_roi: list[dict],
        product_type_roi: list[dict],
        model_costs: list[dict],
        trend: dict,
        cost_insights: dict,
        roi_analysis: dict,
        budget_alert_sent: bool,
    ) -> dict:
        return {
            "date": today_str,
            "period_days": period_days,
            # Revenue
            "total_revenue_eur": round(total_revenue_eur, 4),
            "total_sales": total_sales,
            "active_listings": active_count,
            "avg_price_eur": round(avg_price_eur, 4),
            # Costi
            "llm_cost_eur": round(costs_eur, 6),
            "per_agent_costs_eur": {k: round(v, 6) for k, v in per_agent_costs_eur.items()},
            "etsy_fees": fees,
            # Margini
            "gross_margin_eur": round(gross_margin_eur, 4),
            "gross_margin_pct": round(gross_margin_pct, 2),
            "net_margin_eur": round(net_margin_eur, 4),
            "net_margin_pct": round(net_margin_pct, 2),
            "roi_pct": round(roi_pct, 2),
            # ROI per segmento
            "niche_roi": niche_roi,
            "product_type_roi": product_type_roi,
            # Modelli LLM
            "model_costs": model_costs,
            # Trend
            "trend": trend,
            # Analisi LLM
            "cost_insights": cost_insights,
            "roi_analysis": roi_analysis,
            # Budget
            "budget_threshold_eur": BUDGET_ALERT_EUR,
            "budget_alert_sent": budget_alert_sent,
        }

    # ------------------------------------------------------------------
    # Telegram summary
    # ------------------------------------------------------------------

    async def _send_finance_summary(self, report: dict, date_str: str) -> None:
        rev = report["total_revenue_eur"]
        costs = report["llm_cost_eur"]
        fees_total = report["etsy_fees"]["total_fees_eur"]
        net = report["net_margin_eur"]
        net_pct = report["net_margin_pct"]
        roi = report["roi_pct"]
        sales = report["total_sales"]
        period = report["period_days"]

        # Top niche
        top_niche_line = "—"
        if report["niche_roi"]:
            tn = report["niche_roi"][0]
            top_niche_line = (
                f"{tn['niche'][:30]} — ROI {tn['roi_pct']:.1f}% | €{tn['total_revenue_eur']:.2f}"
            )

        # Trend indicator
        delta = report["trend"].get("revenue_delta_pct", 0.0)
        trend_icon = "📈" if delta > 5 else "📉" if delta < -5 else "➡️"

        # Strategic rec
        rec = report.get("roi_analysis", {}).get(
            "strategic_recommendation", "—"
        )[:100]

        # Forecast
        forecast = report.get("roi_analysis", {}).get("forecast_30d", {})
        forecast_rev = forecast.get("revenue_eur", 0.0)
        forecast_conf = forecast.get("confidence", "low")

        margin_color = "🟢" if net_pct >= 30 else "🟡" if net_pct >= 0 else "🔴"

        msg = (
            f"💰 Report Finance — {date_str} ({period}gg)\n"
            f"─────────────────────\n"
            f"📦 Vendite: {sales} | Listing attivi: {report['active_listings']}\n"
            f"💵 Revenue lorda: €{rev:.2f}\n"
            f"💸 Fee Etsy: €{fees_total:.4f} ({report['etsy_fees']['effective_fee_pct']:.1f}%)\n"
            f"🤖 Costo LLM: €{costs:.4f}\n"
            f"─────────────────────\n"
            f"{margin_color} Margine netto: €{net:.4f} ({net_pct:.1f}%)\n"
            f"📊 ROI: {roi:.1f}%\n\n"
            f"🏆 Top nicchia: {top_niche_line}\n"
            f"{trend_icon} Trend revenue: {delta:+.1f}% vs periodo prec.\n"
            f"🔮 Forecast 30d: €{forecast_rev:.2f} (conf: {forecast_conf})\n\n"
            f"💡 Strategia: {rec}\n\n"
            f"#finance #report"
        )
        await self._notify_telegram(msg)

    # ------------------------------------------------------------------
    # Confidence scoring (target 88%)
    # ------------------------------------------------------------------

    def _calculate_finance_confidence(
        self,
        costs_eur: float,
        revenue_stats: dict,
        niche_roi: list[dict],
        model_costs: list[dict],
        trend: dict,
    ) -> tuple[float, list[str]]:
        """
        Score:
          45% — costi LLM disponibili e consistenti (no Etsy needed)
          25% — revenue data da listing sincronizzati
          20% — niche ROI calcolabile (≥2 nicchie con dati)
          10% — trend data (entrambi i periodi con dati)

        Target: 45+25+20+10 = 88% pieno. Con soli costi: 45% (PARTIAL).
        """
        missing: list[str] = []
        score = 0.0

        # 45% — costi LLM (sempre disponibili dopo i primi task)
        if costs_eur > 0:
            score += 0.45
        else:
            score += 0.15
            missing.append("Nessun costo LLM registrato nel periodo — DB vuoto o periodo troppo corto")

        if model_costs:
            pass  # bonus già incluso nel 45%

        # 25% — revenue e listing synced
        total_rev = revenue_stats.get("total_revenue_eur", 0.0)
        active = revenue_stats.get("active_count", 0)
        if total_rev > 0 and active > 0:
            score += 0.25
        elif active > 0:
            # Listing attivi ma revenue 0 — Etsy non ancora approvata
            score += 0.10
            missing.append(f"{active} listing attivi ma revenue €0 — sync Etsy non eseguito")
        else:
            missing.append("Nessun listing attivo nel DB locale — in attesa di approvazione Etsy")

        # 20% — niche ROI (almeno 2 nicchie con dati)
        niches_with_data = [n for n in niche_roi if n.get("listing_count", 0) > 0]
        if len(niches_with_data) >= 2:
            score += 0.20
        elif len(niches_with_data) == 1:
            score += 0.12
            missing.append("Solo 1 nicchia con dati — ROI comparison limitata")
        else:
            missing.append("Nessuna nicchia con dati listing — aspettare prime pubblicazioni")

        # 10% — trend data (entrambi 7d e 30d con almeno 1 datapoint)
        has_7d = trend.get("revenue_7d", 0.0) > 0 or trend.get("cost_7d", 0.0) > 0
        has_30d = trend.get("revenue_30d", 0.0) > 0 or trend.get("cost_30d", 0.0) > 0
        if has_7d and has_30d:
            score += 0.10
        elif has_30d:
            score += 0.05
            missing.append("Dati trend solo per periodo 30d — confronto 7d non disponibile")
        else:
            missing.append("Dati trend non disponibili — in attesa di operazioni nel periodo")

        return round(min(score, 1.0), 2), missing

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _notify_telegram(self, message: str) -> None:
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(message)
            except Exception:
                pass

    @staticmethod
    def _parse_json_response(text: str) -> dict | None:
        """Estrae e parsa il primo blocco JSON trovato nella risposta LLM."""
        text = text.strip()
        # Prova parsing diretto
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Prova a estrarre da blocco ```json ... ```
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        # Prova a estrarre il primo { ... } dalla risposta
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None
