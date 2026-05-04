"""Etsy listings mixin for MemoryManager."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apps.backend.core._memory._base import _json_dumps, _json_loads

logger = logging.getLogger("agentpexi.memory")


class EtsyListingsMixin:
    # ------------------------------------------------------------------
    # Etsy listings (expanded)
    # ------------------------------------------------------------------

    async def add_etsy_listing(
        self,
        listing_id: str,
        production_queue_task_id: str | None,
        title: str,
        tags: list[str],
        product_type: str,
        niche: str,
        template: str,
        color_scheme: str,
        size: str,
        ab_price_variant: str,
        price_eur: float,
        file_path: str,
    ) -> None:
        await self._db.execute(
            """INSERT INTO etsy_listings
               (listing_id, production_queue_task_id, title, tags,
                product_type, niche, template, color_scheme, size,
                ab_price_variant, price_eur, file_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                listing_id,
                production_queue_task_id,
                title,
                _json_dumps(tags),
                product_type,
                niche,
                template,
                color_scheme,
                size,
                ab_price_variant,
                price_eur,
                file_path,
            ),
        )
        await self._db.commit()

    async def update_etsy_listing_stats(
        self,
        listing_id: str,
        views: int,
        favorites: int,
        sales: int,
        revenue_eur: float,
        status: str,
        last_synced_at: str,
    ) -> None:
        # Aggiornamento atomico: views_prev e stats nella stessa transazione.
        # BEGIN IMMEDIATE blocca writer concorrenti — nessuna coroutine può
        # leggere uno stato parziale (views_prev aggiornato, views vecchio).
        await self._db.execute("BEGIN IMMEDIATE")
        try:
            await self._db.execute(
                "UPDATE etsy_listings SET views_prev = views WHERE listing_id = ?",
                (listing_id,),
            )
            await self._db.execute(
                """UPDATE etsy_listings SET
                   views = ?, favorites = ?, sales = ?,
                   revenue_eur = ?, status = ?, last_synced_at = ?
                   WHERE listing_id = ?""",
                (views, favorites, sales, revenue_eur, status, last_synced_at, listing_id),
            )
            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise

    async def get_etsy_listings(self, status: str | None = None, limit: int | None = None) -> list[dict]:
        limit_clause = f" LIMIT {int(limit)}" if limit else ""
        if status:
            cursor = await self._db.execute(
                f"SELECT * FROM etsy_listings WHERE status = ? ORDER BY created_at DESC{limit_clause}",
                (status,),
            )
        else:
            cursor = await self._db.execute(
                f"SELECT * FROM etsy_listings ORDER BY created_at DESC{limit_clause}"
            )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["tags"] = _json_loads(d.get("tags"))
            result.append(d)
        return result

    async def get_etsy_listings_count(self) -> int:
        """Conta totale listing in etsy_listings (qualsiasi status)."""
        cursor = await self._db.execute("SELECT COUNT(*) FROM etsy_listings")
        row = await cursor.fetchone()
        return row[0]

    async def get_listings_no_views(self, days: int = 7) -> list[dict]:
        """views == 0, active, created_at < now - days, no_views_flagged_at IS NULL."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """SELECT * FROM etsy_listings
               WHERE views = 0 AND status = 'active'
               AND created_at < ? AND no_views_flagged_at IS NULL""",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_listings_no_conversion(self, days: int = 45) -> list[dict]:
        """views > 0, sales == 0, active, created_at < now - days, no_conversion_flagged_at IS NULL."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """SELECT * FROM etsy_listings
               WHERE views > 0 AND sales = 0 AND status = 'active'
               AND created_at < ? AND no_conversion_flagged_at IS NULL""",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_listings_no_views_no_sales(self, days: int = 45) -> list[dict]:
        """views == 0, sales == 0, active, created_at < now - days, no_views_no_sales_flagged_at IS NULL."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """SELECT * FROM etsy_listings
               WHERE views = 0 AND sales = 0 AND status = 'active'
               AND created_at < ? AND no_views_no_sales_flagged_at IS NULL""",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def flag_no_views(self, listing_id: str) -> None:
        await self._db.execute(
            "UPDATE etsy_listings SET no_views_flagged_at = CURRENT_TIMESTAMP WHERE listing_id = ?",
            (listing_id,),
        )
        await self._db.commit()

    async def flag_no_conversion(self, listing_id: str) -> None:
        await self._db.execute(
            "UPDATE etsy_listings SET no_conversion_flagged_at = CURRENT_TIMESTAMP WHERE listing_id = ?",
            (listing_id,),
        )
        await self._db.commit()

    async def flag_no_views_no_sales(self, listing_id: str) -> None:
        await self._db.execute(
            "UPDATE etsy_listings SET no_views_no_sales_flagged_at = CURRENT_TIMESTAMP WHERE listing_id = ?",
            (listing_id,),
        )
        await self._db.commit()

    async def get_listing_prev_views(self, listing_id: str) -> int | None:
        """Ritorna views_prev prima dell'ultimo update_etsy_listing_stats()."""
        cursor = await self._db.execute(
            "SELECT views_prev FROM etsy_listings WHERE listing_id = ?",
            (listing_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return row[0]

    # ------------------------------------------------------------------
    # Listing analyses
    # ------------------------------------------------------------------

    async def save_listing_analysis(
        self,
        listing_id: str,
        analysis_type: str,
        cause: str,
        recommendations: list[str],
        avoid_in_future: str,
        chromadb_id: str | None = None,
    ) -> None:
        await self._db.execute(
            """INSERT INTO listing_analyses
               (listing_id, analysis_type, cause, recommendations,
                avoid_in_future, chromadb_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                listing_id,
                analysis_type,
                cause,
                _json_dumps(recommendations),
                avoid_in_future,
                chromadb_id,
            ),
        )
        await self._db.commit()

    async def get_listing_analyses(self, listing_id: str) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM listing_analyses WHERE listing_id = ? ORDER BY created_at DESC",
            (listing_id,),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["recommendations"] = _json_loads(d.get("recommendations"))
            result.append(d)
        return result

    async def get_all_listing_analyses(self, limit: int = 20) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM listing_analyses ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["recommendations"] = _json_loads(d.get("recommendations"))
            result.append(d)
        return result
