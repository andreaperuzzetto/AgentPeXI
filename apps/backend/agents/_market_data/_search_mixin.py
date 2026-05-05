"""MarketDataAgent — Etsy search and autocomplete mixin."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from apps.backend.core.config import settings
from .constants import ETSY_API_BASE, ETSY_AUTOCOMPLETE_URL, _HTTP_TIMEOUT

logger = logging.getLogger("agentpexi.market_data")


class _SearchMixin:

    async def _search_etsy_listings(self, keyword: str) -> dict[str, Any]:
        """
        Cerca listing attivi su Etsy per keyword.
        Endpoint: GET /v3/application/listings/active
        Autenticazione: solo x-api-key header (no OAuth).

        Estrae: count totale, avg price, avg num_favorers (proxy reviews).
        """
        api_key = settings.ETSY_API_KEY
        if not api_key:
            logger.warning("market_data: ETSY_API_KEY non configurato")
            return {"count": 0, "avg_reviews": 0.0, "avg_price_eur": 0.0}

        client = await self._get_client()

        params = {
            "keywords":   keyword,
            "limit":      100,
            "includes":   "Images",
            "fields":     "listing_id,price,num_favorers,title,quantity",
            "sort_on":    "score",
            "sort_order": "desc",
        }

        try:
            resp = await client.get(
                f"{ETSY_API_BASE}/listings/active",
                params=params,
                headers={"x-api-key": api_key},
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("market_data: Etsy API %d per '%s'", e.response.status_code, keyword)
            return {"count": 0, "avg_reviews": 0.0, "avg_price_eur": 0.0}
        except Exception as e:
            logger.error("market_data: Etsy search exception '%s': %s", keyword, e)
            return {"count": 0, "avg_reviews": 0.0, "avg_price_eur": 0.0}

        results = data.get("results", [])
        count   = data.get("count", len(results))

        if not results:
            return {"count": count, "avg_reviews": 0.0, "avg_price_eur": 0.0}

        prices   = []
        favorers = []

        for listing in results:
            price_obj = listing.get("price", {})
            if price_obj:
                try:
                    price_eur = price_obj["amount"] / price_obj["divisor"]
                    if price_obj.get("currency_code", "EUR") == "USD":
                        price_eur *= settings.USD_EUR_RATE
                    prices.append(price_eur)
                except (KeyError, ZeroDivisionError, TypeError):
                    pass

            fav = listing.get("num_favorers", 0)
            if isinstance(fav, int):
                favorers.append(fav)

        avg_price = round(sum(prices) / len(prices), 2) if prices else 0.0
        avg_favs  = round(sum(favorers) / len(favorers), 1) if favorers else 0.0

        return {
            "count":         count,
            "avg_reviews":   avg_favs,    # num_favorers è il proxy migliore per reviews
            "avg_price_eur": avg_price,
        }

    async def _get_autocomplete(self, keyword: str) -> list[str]:
        """
        Recupera suggerimenti autocomplete dalla ricerca pubblica Etsy.
        Endpoint non ufficiale ma stabile: restituisce fino a 10 suggestions.
        Fallisce silenziosamente (nessun account necessario).
        """
        client = await self._get_client()

        try:
            resp = await client.get(
                ETSY_AUTOCOMPLETE_URL,
                params={"query": keyword, "limit": 10, "locale": "en-US"},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept":  "application/json",
                    "Referer": "https://www.etsy.com/",
                },
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                suggestions = [str(s) for s in data]
            elif isinstance(data, dict):
                suggestions = data.get("suggestions", data.get("results", []))
                suggestions = [
                    s.get("value", s) if isinstance(s, dict) else str(s)
                    for s in suggestions
                ]
            else:
                suggestions = []

            logger.debug(
                "market_data: autocomplete '%s' → %d suggestions", keyword, len(suggestions)
            )
            return suggestions[:10]

        except Exception as e:
            logger.debug("market_data: autocomplete silently failed for '%s': %s", keyword, e)
            return []
