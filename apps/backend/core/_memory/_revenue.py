"""Revenue mixin for MemoryManager."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("agentpexi.memory")


class RevenueMixin:
    # ------------------------------------------------------------------
    # Finance data helpers
    # ------------------------------------------------------------------

    async def get_revenue_stats(self, period_days: int = 30) -> dict:
        """Revenue aggregata dal DB locale (etsy_listings).

        Ritorna: total_revenue_eur, total_sales, active_count, draft_count,
        avg_price_eur, avg_revenue_per_listing.
        Nessuna chiamata Etsy — dati locali last_synced_at o created_at.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        cursor = await self._db.execute(
            """SELECT
               COALESCE(SUM(revenue_eur), 0.0)  AS total_revenue_eur,
               COALESCE(SUM(sales), 0)           AS total_sales,
               COUNT(*)                           AS total_listings,
               SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_count,
               SUM(CASE WHEN status = 'draft'  THEN 1 ELSE 0 END) AS draft_count,
               COALESCE(AVG(price_eur), 0.0)     AS avg_price_eur
               FROM etsy_listings
               WHERE created_at >= ? OR last_synced_at >= ?""",
            (since, since),
        )
        row = await cursor.fetchone()
        if not row:
            return {
                "total_revenue_eur": 0.0,
                "total_sales": 0,
                "total_listings": 0,
                "active_count": 0,
                "draft_count": 0,
                "avg_price_eur": 0.0,
                "avg_revenue_per_listing": 0.0,
            }
        d = dict(row)
        total_listings = d.get("total_listings") or 0
        d["avg_revenue_per_listing"] = (
            d["total_revenue_eur"] / total_listings if total_listings else 0.0
        )
        return d

    async def get_revenue_by_niche(self, period_days: int = 30) -> list[dict]:
        """Revenue, vendite, listing count per nicchia nel periodo."""
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = await self._db.execute(
            """SELECT niche,
               COUNT(*)                          AS listing_count,
               COALESCE(SUM(sales), 0)           AS total_sales,
               COALESCE(SUM(revenue_eur), 0.0)   AS total_revenue_eur,
               COALESCE(AVG(price_eur), 0.0)     AS avg_price_eur
               FROM etsy_listings
               WHERE (created_at >= ? OR last_synced_at >= ?)
               AND niche IS NOT NULL AND niche != ''
               GROUP BY niche
               ORDER BY total_revenue_eur DESC""",
            (since, since),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_revenue_by_product_type(self, period_days: int = 30) -> list[dict]:
        """Revenue, vendite per product_type nel periodo."""
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = await self._db.execute(
            """SELECT product_type,
               COUNT(*)                          AS listing_count,
               COALESCE(SUM(sales), 0)           AS total_sales,
               COALESCE(SUM(revenue_eur), 0.0)   AS total_revenue_eur,
               COALESCE(AVG(price_eur), 0.0)     AS avg_price_eur
               FROM etsy_listings
               WHERE (created_at >= ? OR last_synced_at >= ?)
               AND product_type IS NOT NULL AND product_type != ''
               GROUP BY product_type
               ORDER BY total_revenue_eur DESC""",
            (since, since),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_model_cost_breakdown(self, period_days: int = 30) -> list[dict]:
        """Costo, token totali, chiamate per modello LLM nel periodo."""
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = await self._db.execute(
            """SELECT model,
               COUNT(*)                          AS call_count,
               COALESCE(SUM(input_tokens), 0)    AS total_input_tokens,
               COALESCE(SUM(output_tokens), 0)   AS total_output_tokens,
               COALESCE(SUM(cost_usd), 0.0)      AS total_cost_usd
               FROM llm_calls
               WHERE timestamp >= ?
               GROUP BY model
               ORDER BY total_cost_usd DESC""",
            (since,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_daily_revenue_trend(self, period_days: int = 30) -> list[dict]:
        """Revenue giornaliera cumulativa da etsy_listings (per trend chart)."""
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = await self._db.execute(
            """SELECT DATE(last_synced_at) AS day,
               COALESCE(SUM(revenue_eur), 0.0) AS daily_revenue_eur,
               COALESCE(SUM(sales), 0)          AS daily_sales
               FROM etsy_listings
               WHERE last_synced_at >= ?
               GROUP BY DATE(last_synced_at)
               ORDER BY day""",
            (since,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
