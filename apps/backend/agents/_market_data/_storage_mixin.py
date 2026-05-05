"""MarketDataAgent — DB persistence and HTTP client mixin."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ._models import MarketSignals
from .constants import _HTTP_TIMEOUT

logger = logging.getLogger("agentpexi.market_data")


class _StorageMixin:

    async def get_cached_signals(
        self,
        niche: str,
        max_age_hours: int = 24,
    ) -> dict[str, Any] | None:
        """Ritorna i segnali più recenti dal DB se non più vecchi di max_age_hours."""
        cutoff = time.time() - (max_age_hours * 3600)
        db = await self._memory.get_db()
        cursor = await db.execute(
            """
            SELECT * FROM market_signals
            WHERE niche = ? AND collected_at >= ?
            ORDER BY collected_at DESC
            LIMIT 1
            """,
            (niche, cutoff),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def get_top_candidates(
        self,
        limit: int = 10,
        min_score: float = 0.2,
    ) -> list[dict[str, Any]]:
        """
        Ritorna le niche con entry_score più alto dal DB.
        Usato da AutopilotLoop per selezionare la prossima niche.
        """
        db = await self._memory.get_db()
        cursor = await db.execute(
            """
            SELECT niche, product_type,
                   MAX(entry_score) AS entry_score,
                   MAX(collected_at) AS last_collected
            FROM market_signals
            WHERE entry_score >= ?
            GROUP BY niche
            ORDER BY entry_score DESC
            LIMIT ?
            """,
            (min_score, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows] if rows else []

    async def _save_signals(self, signals: MarketSignals) -> int:
        """Salva i segnali in market_signals. Ritorna l'id della riga inserita."""
        db = await self._memory.get_db()
        cursor = await db.execute(
            """
            INSERT INTO market_signals (
                niche, product_type,
                etsy_result_count, avg_reviews, avg_price_eur, autocomplete_hits,
                google_trend_score, erank_search_volume,
                entry_score, seasonal_boost, tier, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signals.niche,
                signals.product_type,
                signals.etsy_result_count,
                signals.avg_reviews,
                signals.avg_price_eur,
                signals.autocomplete_hits,
                signals.google_trend_score,
                signals.erank_search_volume,
                signals.entry_score,
                signals.seasonal_boost,
                signals.tier,
                signals.collected_at,
            ),
        )
        await db.commit()
        return cursor.lastrowid

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=_HTTP_TIMEOUT,
            )
        return self._client

    async def close(self) -> None:
        """Chiude il client HTTP. Chiamare allo shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @staticmethod
    def _dict_to_signals(row: dict[str, Any]) -> MarketSignals:
        return MarketSignals(
            niche               = row["niche"],
            product_type        = row.get("product_type"),
            etsy_result_count   = row.get("etsy_result_count", 0),
            avg_reviews         = row.get("avg_reviews", 0.0),
            avg_price_eur       = row.get("avg_price_eur", 0.0),
            autocomplete_hits   = row.get("autocomplete_hits", 0),
            google_trend_score  = row.get("google_trend_score", 0.0),
            erank_search_volume = row.get("erank_search_volume", 0),
            entry_score         = row.get("entry_score", 0.0),
            seasonal_boost      = row.get("seasonal_boost", 1.0),
            tier                = row.get("tier", 1),
            collected_at        = row.get("collected_at", time.time()),
        )
