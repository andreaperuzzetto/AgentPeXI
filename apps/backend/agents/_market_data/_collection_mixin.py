"""MarketDataAgent — Tier 1 + Tier 2 collection mixin."""
from __future__ import annotations

import asyncio
import dataclasses
import logging

from ._models import MarketSignals

logger = logging.getLogger("agentpexi.market_data")


class _CollectionMixin:

    async def collect_tier1(
        self,
        niche: str,
        product_type: str | None = None,
        force_refresh: bool = False,
    ) -> MarketSignals:
        """
        Raccoglie dati Tier 1 per la niche.
        Usa cache DB (24h) a meno che force_refresh=True.
        """
        if not force_refresh:
            cached = await self.get_cached_signals(niche, max_age_hours=24)
            if cached:
                logger.debug("market_data: cache HIT per '%s'", niche)
                return self._dict_to_signals(cached)

        logger.info("market_data: raccolta Tier 1 per '%s' (mock=%s)", niche, self._mock)

        if self._mock:
            signals = self._mock_tier1(niche, product_type)
        else:
            signals = await self._real_tier1(niche, product_type)

        signals.seasonal_boost = self._get_seasonal_boost(niche)
        signals.entry_score    = self._compute_entry_score(signals)

        await self._save_signals(signals)
        return signals

    async def collect_tier2(
        self,
        signals: MarketSignals,
    ) -> MarketSignals:
        """
        Arricchisce un MarketSignals esistente con Google Trends.
        Ricalcola entry_score con il blending Tier 1+2.
        Persiste una nuova riga in market_signals con tier=2.

        Non modifica l'oggetto in-place: ritorna una copia aggiornata.
        """
        logger.info(
            "market_data: raccolta Tier 2 per '%s' (mock=%s)", signals.niche, self._mock
        )

        if self._mock:
            trend_data = self._mock_tier2(signals.niche)
        else:
            trend_data = await self._real_tier2(signals.niche)

        enriched = dataclasses.replace(
            signals,
            google_trend_score = trend_data["score"],
            tier               = 2,
        )
        enriched.seasonal_boost = self._get_seasonal_boost(enriched.niche)
        enriched.entry_score    = self._compute_entry_score(enriched)

        await self._save_signals(enriched)
        return enriched

    async def collect_full(
        self,
        niche: str,
        product_type: str | None = None,
        force_refresh: bool = False,
    ) -> MarketSignals:
        """
        Pipeline completa: Tier 1 (Etsy) → Tier 2 (Google Trends).
        Entry point consigliato per il scoring pipeline.

        Se Tier 2 fallisce (timeout, rate limit, pytrends non installato)
        ritorna comunque i segnali Tier 1 — non blocca mai il flusso.
        """
        signals = await self.collect_tier1(niche, product_type, force_refresh)
        signals = await self.collect_tier2(signals)
        return signals

    async def _real_tier1(
        self,
        niche: str,
        product_type: str | None,
    ) -> MarketSignals:
        """Chiama Etsy API pubblica e autocomplete in parallelo."""
        search_task       = asyncio.create_task(self._search_etsy_listings(niche))
        autocomplete_task = asyncio.create_task(self._get_autocomplete(niche))

        search_data, ac_suggestions = await asyncio.gather(
            search_task, autocomplete_task, return_exceptions=True
        )

        if isinstance(search_data, Exception):
            logger.warning("market_data: Etsy search fallita per '%s': %s", niche, search_data)
            search_data = {"count": 0, "avg_reviews": 0.0, "avg_price_eur": 0.0}

        if isinstance(ac_suggestions, Exception):
            logger.warning("market_data: autocomplete fallita per '%s': %s", niche, ac_suggestions)
            ac_suggestions = []

        kw_root = niche.lower().split()[0] if niche else ""
        ac_hits = sum(1 for s in ac_suggestions if kw_root in s.lower())

        return MarketSignals(
            niche              = niche,
            product_type       = product_type,
            etsy_result_count  = search_data.get("count", 0),
            avg_reviews        = search_data.get("avg_reviews", 0.0),
            avg_price_eur      = search_data.get("avg_price_eur", 0.0),
            autocomplete_hits  = ac_hits,
            tier               = 1,
        )

    async def _real_tier2(self, niche: str) -> dict[str, float]:
        """
        Chiama Google Trends via pytrends (wrapper sincrono in thread executor).
        Ritorna {"score": float 0-100}.

        Fallisce silenziosamente: se pytrends non è installato o la chiamata
        va in timeout, ritorna score=0 e logga un warning.
        """
        try:
            from apps.backend.tools.trends import get_google_trends
            result = await get_google_trends(niche)
            score  = float(result.get("current_value") or result.get("avg_value") or 0)
            logger.debug(
                "market_data: Trends '%s' → score=%.1f direction=%s",
                niche, score, result.get("trend_direction", "?")
            )
            return {"score": round(score, 1)}
        except Exception as e:
            logger.warning("market_data: Tier 2 fallito per '%s': %s", niche, e)
            return {"score": 0.0}
