"""WebSearchTool — wrapper unificato DuckDuckGo (personal) e Tavily (etsy).

DuckDuckGo: gratuito, locale, nessuna API key.
Tavily: qualità superiore, usato esclusivamente per Research Etsy.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("agentpexi.web_search")

# Intervallo minimo tra chiamate DuckDuckGo (anti rate-limit)
_DDGS_MIN_INTERVAL = 2.0   # secondi
_DDGS_RETRY_WAIT   = 3     # secondi prima del retry
_DDGS_MAX_RETRIES  = 2


class WebSearchTool:
    """Wrapper unificato per ricerche web.

    Uso:
        results = await tool.search("query", max_results=5, provider="duckduckgo")
        # [{"title": str, "url": str, "snippet": str}, ...]

    Non lancia eccezioni: restituisce lista vuota in caso di errore.
    """

    def __init__(self) -> None:
        self._last_ddgs_call: float = 0.0

    async def search(
        self,
        query: str,
        max_results: int = 8,
        provider: str = "duckduckgo",
    ) -> list[dict]:
        """Esegue una ricerca web. Restituisce lista vuota su errore."""
        if not query or not query.strip():
            return []

        if provider == "duckduckgo":
            return await self._search_ddgs(query.strip(), max_results)
        elif provider == "tavily":
            return await self._search_tavily(query.strip(), max_results)
        else:
            logger.warning("WebSearch: provider '%s' non supportato", provider)
            return []

    # ------------------------------------------------------------------
    # DuckDuckGo
    # ------------------------------------------------------------------

    async def _search_ddgs(self, query: str, max_results: int) -> list[dict]:
        """Ricerca DuckDuckGo con rate limiting e retry."""
        # Rate limiting: aspetta se necessario
        loop = asyncio.get_event_loop()
        now = loop.time()
        elapsed = now - self._last_ddgs_call
        if elapsed < _DDGS_MIN_INTERVAL:
            await asyncio.sleep(_DDGS_MIN_INTERVAL - elapsed)

        for attempt in range(_DDGS_MAX_RETRIES):
            try:
                results = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._ddgs_sync,
                    query,
                    max_results,
                )
                self._last_ddgs_call = asyncio.get_event_loop().time()
                return results
            except Exception as exc:
                exc_name = type(exc).__name__
                logger.warning(
                    "DuckDuckGo tentativo %d/%d fallito (%s): %s",
                    attempt + 1, _DDGS_MAX_RETRIES, exc_name, exc,
                )
                if attempt < _DDGS_MAX_RETRIES - 1:
                    await asyncio.sleep(_DDGS_RETRY_WAIT)

        logger.warning("DuckDuckGo: tutti i tentativi falliti per query '%s'", query[:60])
        return []

    @staticmethod
    def _ddgs_sync(query: str, max_results: int) -> list[dict]:
        """Esecuzione sincrona di DDGS (da run_in_executor)."""
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title":   r.get("title", ""),
                    "url":     r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        return results

    # ------------------------------------------------------------------
    # Tavily (invariato rispetto a tools/tavily.py — usato per Etsy)
    # ------------------------------------------------------------------

    async def _search_tavily(self, query: str, max_results: int) -> list[dict]:
        """Ricerca Tavily. Richiede TAVILY_API_KEY in settings."""
        try:
            from apps.backend.core.config import settings
            from apps.backend.tools.tavily import TavilySearch

            tavily = TavilySearch(api_key=settings.TAVILY_API_KEY)
            raw = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: tavily.search(query, max_results=max_results),
            )
            return [
                {
                    "title":   r.get("title", ""),
                    "url":     r.get("url", ""),
                    "snippet": r.get("content", ""),
                }
                for r in (raw.get("results") or [])
            ]
        except Exception as exc:
            logger.warning("Tavily search fallito: %s", exc)
            return []
