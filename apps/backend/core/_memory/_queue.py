"""Production queue mixin for MemoryManager."""
from __future__ import annotations

import logging

from apps.backend.core._memory._base import _json_dumps, _json_loads

logger = logging.getLogger("agentpexi.memory")


class QueueMixin:
    # ------------------------------------------------------------------
    # Production queue (deduplicazione pipeline)
    # ------------------------------------------------------------------

    async def add_to_production_queue(
        self,
        task_id: str,
        product_type: str,
        niche: str,
        brief: dict,
    ) -> int:
        """Inserisce un nuovo item nella coda. Ritorna l'id row."""
        cursor = await self._db.execute(
            """INSERT INTO production_queue (task_id, product_type, niche, brief)
               VALUES (?, ?, ?, ?)""",
            (task_id, product_type, niche, _json_dumps(brief)),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_production_queue_item(self, task_id: str) -> dict | None:
        """Ritorna item per task_id, None se non esiste."""
        cursor = await self._db.execute(
            "SELECT * FROM production_queue WHERE task_id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["brief"] = _json_loads(d.get("brief"))
        d["file_paths"] = _json_loads(d.get("file_paths"))
        return d

    async def update_production_queue_status(
        self,
        task_id: str,
        status: str,
        file_paths: list[str] | None = None,
    ) -> None:
        """Aggiorna status e opzionalmente file_paths. Setta updated_at = now."""
        if file_paths is not None:
            await self._db.execute(
                """UPDATE production_queue SET status = ?, file_paths = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE task_id = ?""",
                (status, _json_dumps(file_paths), task_id),
            )
        else:
            await self._db.execute(
                """UPDATE production_queue SET status = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE task_id = ?""",
                (status, task_id),
            )
        await self._db.commit()

    async def get_production_queue(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Lista items, filtrabili per status. Ordinati per created_at DESC."""
        if status:
            cursor = await self._db.execute(
                "SELECT * FROM production_queue WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM production_queue ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["brief"] = _json_loads(d.get("brief"))
            d["file_paths"] = _json_loads(d.get("file_paths"))
            result.append(d)
        return result

    async def is_duplicate_product(self, niche: str, product_type: str) -> bool:
        """True se esiste già un item completed o in_progress con stessa niche+product_type."""
        cursor = await self._db.execute(
            """SELECT 1 FROM production_queue
               WHERE niche = ? AND product_type = ?
               AND status IN ('completed', 'in_progress') LIMIT 1""",
            (niche, product_type),
        )
        if await cursor.fetchone():
            return True
        cursor = await self._db.execute(
            """SELECT 1 FROM etsy_listings
               WHERE niche = ? AND product_type = ? LIMIT 1""",
            (niche, product_type),
        )
        return (await cursor.fetchone()) is not None

    async def get_production_queue_stats(self) -> dict:
        """Statistiche aggregate production_queue."""
        from datetime import date as _date

        today = _date.today().isoformat()
        stats: dict[str, int] = {}
        for status in ("planned", "in_progress", "completed", "skipped"):
            cursor = await self._db.execute(
                "SELECT COUNT(*) as cnt FROM production_queue WHERE status = ?",
                (status,),
            )
            row = await cursor.fetchone()
            stats[status] = row["cnt"] if row else 0
        cursor = await self._db.execute(
            "SELECT COUNT(*) as cnt FROM production_queue "
            "WHERE status = 'completed' AND date(created_at) = ?",
            (today,),
        )
        row = await cursor.fetchone()
        stats["completed_today"] = row["cnt"] if row else 0
        return stats

    async def get_listings_by_niche(self, niche: str, limit: int = 10) -> list[dict]:
        """Ritorna listing per una nicchia specifica."""
        cursor = await self._db.execute(
            "SELECT * FROM etsy_listings WHERE niche = ? ORDER BY created_at DESC LIMIT ?",
            (niche, limit),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["tags"] = _json_loads(d.get("tags"))
            result.append(d)
        return result

    async def get_stale_listings_without_sales(
        self, min_views: int = 50, days_old: int = 30, limit: int = 20
    ) -> list[dict]:
        """Restituisce listing con 0 vendite ma molte views, creati da almeno `days_old` giorni."""
        async with self._db.execute(
            """
            SELECT niche, price_eur, views, sales
            FROM etsy_listings
            WHERE sales = 0 AND views > ?
            AND created_at < datetime('now', ? || ' days')
            LIMIT ?
            """,
            (min_views, f"-{days_old}", limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
