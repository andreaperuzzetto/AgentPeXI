"""FinanceAgent — report building and Telegram notifications mixin."""
from __future__ import annotations

import logging

from apps.backend.agents._finance.constants import BUDGET_ALERT_EUR

logger = logging.getLogger("agentpexi.finance")


class _ReportingMixin:

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
    # Notifica Telegram
    # ------------------------------------------------------------------

    async def _notify_telegram(self, message: str) -> None:
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(message)
            except Exception:
                logger.exception("Unexpected error")
