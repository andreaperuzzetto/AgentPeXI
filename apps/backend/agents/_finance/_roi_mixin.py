"""FinanceAgent — ROI computation mixin."""
from __future__ import annotations

import math

from apps.backend.agents._finance.constants import (
    ETSY_TRANSACTION_FEE_PCT,
    ETSY_PAYMENT_FEE_PCT,
    ETSY_PAYMENT_FEE_FIXED_EUR,
)


class _RoiMixin:

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

            avg_price = niche.get("avg_price_eur", 0.0)

            # Break-even: quante vendite servono per coprire il costo LLM attribuito
            # Revenue netta per vendita = prezzo * (1 - transaction_fee - payment_fee%) - fixed_fee
            net_per_sale = (
                avg_price * (1 - ETSY_TRANSACTION_FEE_PCT - ETSY_PAYMENT_FEE_PCT)
                - ETSY_PAYMENT_FEE_FIXED_EUR
            )
            break_even = (
                math.ceil(llm_cost_attributed / net_per_sale)
                if net_per_sale > 0
                else 0
            )
            cost_per_listing_val = llm_cost_attributed / count if count > 0 else 0.0

            result.append({
                "niche": niche.get("niche", ""),
                "listing_count": count,
                "total_sales": sales,
                "total_revenue_eur": round(rev, 4),
                "etsy_fees_eur": fees["total_fees_eur"],
                "llm_cost_attributed_eur": round(llm_cost_attributed, 4),
                "net_margin_eur": round(net_margin, 4),
                "roi_pct": round(roi, 2),
                "avg_price_eur": round(avg_price, 4),
                "break_even_units": break_even,
                "cost_per_listing_eur": round(cost_per_listing_val, 6),
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
