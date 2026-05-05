"""FinanceAgent — LLM-powered insights and budget alerting mixin."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from apps.backend.agents._finance.constants import BUDGET_ALERT_EUR
from apps.backend.core.config import MODEL_HAIKU, MODEL_SONNET

logger = logging.getLogger("agentpexi.finance")


class _InsightsMixin:

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

        # Pricing context da Research (confronto prezzo raccomandato vs reale)
        research_pricing = lc.get("research_pricing", [])
        pricing_context_str = ""
        if research_pricing:
            pricing_context_str = "\n\n## Pricing raccomandato da Research (per confronto con prezzi reali)\n"
            for rp in research_pricing[:5]:
                pricing_context_str += f"  - Niche '{rp['niche']}': {rp['summary'][:200]}\n"

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
            f"{pricing_context_str}"
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
