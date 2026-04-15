"""Wrapper Tavily API — search + extract per research di mercato."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from tavily import AsyncTavilyClient

from apps.backend.core.config import settings


def _get_client() -> AsyncTavilyClient:
    """Crea client Tavily con API key da config."""
    return AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)


async def search(
    query: str,
    *,
    max_results: int = 10,
    search_depth: str = "advanced",
    include_answer: bool = True,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> dict[str, Any]:
    """Esegue una ricerca Tavily e restituisce risultati strutturati.

    Returns:
        dict con chiavi: query, answer (se richiesta), results (list of dicts
        con title, url, content, score).
    """
    client = _get_client()

    kwargs: dict[str, Any] = {
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_answer": include_answer,
    }
    if include_domains:
        kwargs["include_domains"] = include_domains
    if exclude_domains:
        kwargs["exclude_domains"] = exclude_domains

    response = await client.search(**kwargs)
    return response


async def extract(urls: list[str]) -> list[dict[str, Any]]:
    """Estrae contenuto da una lista di URL tramite Tavily Extract.

    Returns:
        Lista di dict con chiavi: url, raw_content.
    """
    client = _get_client()
    response = await client.extract(urls=urls)
    return response.get("results", [])


async def search_etsy_niche(
    niche: str,
    *,
    max_results: int = 10,
) -> dict[str, Any]:
    """Ricerca specializzata per nicchia Etsy — include siti rilevanti."""
    query = f"Etsy {niche} digital products best sellers trends pricing 2025 2026"
    return await search(
        query,
        max_results=max_results,
        search_depth="advanced",
        include_answer=True,
        include_domains=[
            "etsy.com",
            "erank.com",
            "marmalead.com",
            "reddit.com",
            "salsamobi.com",
        ],
    )


async def search_etsy_direct(niche: str) -> dict[str, Any]:
    """
    Versione migliorata: combina extract Etsy diretto + eRank public data.
    """
    client = _get_client()

    etsy_search_query = f'"{niche}" digital download printable site:etsy.com'
    keyword_query = f'"{niche}" etsy keyword search volume erank marmalead 2025'

    etsy_results, keyword_results = await asyncio.gather(
        client.search(
            query=etsy_search_query,
            max_results=10,
            include_domains=["etsy.com"],
            search_depth="advanced",
        ),
        client.search(
            query=keyword_query,
            max_results=8,
            include_domains=["erank.com", "marmalead.com", "blog.erank.com"],
            search_depth="advanced",
        ),
        return_exceptions=True,
    )

    etsy_listings_raw = []
    if not isinstance(etsy_results, Exception):
        for r in etsy_results.get("results", []):
            if "etsy.com/listing" in r.get("url", ""):
                etsy_listings_raw.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("content", "")[:300],
                    "url": r.get("url", ""),
                })

    erank_keyword_data = []
    if not isinstance(keyword_results, Exception):
        for r in keyword_results.get("results", []):
            erank_keyword_data.append({
                "title": r.get("title", ""),
                "content": r.get("content", "")[:400],
                "source": r.get("url", ""),
            })

    return {
        "etsy_listings_raw": etsy_listings_raw,
        "erank_keyword_data": erank_keyword_data,
        "listings_count": len(etsy_listings_raw),
        "keyword_sources_count": len(erank_keyword_data),
    }


async def search_etsy_pricing(niche: str) -> dict[str, Any]:
    """
    Query mirata per prezzi reali da Etsy — usa site:etsy.com per forzare
    risultati dalla piattaforma, non da blog.
    """
    client = _get_client()
    try:
        result = await client.search(
            query=f'site:etsy.com "{niche}" digital download price',
            max_results=10,
            include_domains=["etsy.com"],
            search_depth="advanced",
        )

        prices: list[float] = []
        listings: list[dict] = []
        for r in result.get("results", []):
            content = r.get("content", "")
            title = r.get("title", "")
            url = r.get("url", "")

            price_matches = re.findall(r'\$(\d+\.?\d*)', content + title)
            price_matches += re.findall(r'€(\d+\.?\d*)', content + title)

            for p in price_matches:
                try:
                    price_val = float(p)
                    if 0.50 < price_val < 100.0:
                        prices.append(price_val)
                except ValueError:
                    pass

            if "etsy.com/listing" in url:
                listings.append({"title": title, "url": url, "raw_content": content[:300]})

        if prices:
            return {
                "prices_found": prices,
                "avg_price": round(sum(prices) / len(prices), 2),
                "min_price": min(prices),
                "max_price": max(prices),
                "sample_count": len(prices),
                "listings_found": listings[:5],
                "source": "etsy_extract",
            }

        return {"source": "etsy_extract_empty", "prices_found": [], "listings_found": listings}

    except Exception as e:
        return {"error": str(e), "source": "etsy_extract_failed"}


async def search_etsy_seo_community(niche: str) -> dict[str, Any]:
    """
    Cerca tag/keyword da community Etsy seller (Reddit, forum, eRank blog)
    dove i seller condividono strategie reali.
    """
    client = _get_client()
    queries = [
        f'Etsy "{niche}" best tags 2025 seller tips reddit',
        f'eRank "{niche}" printable keyword volume tags',
        f'"{niche}" etsy listing tags that work digital products',
    ]

    all_results: list[dict] = []
    for query in queries:
        try:
            result = await client.search(
                query=query,
                max_results=5,
                include_domains=[
                    "reddit.com",
                    "erank.com",
                    "marmalead.com",
                    "sellerhandbook.etsy.com",
                    "blog.etsy.com",
                    "printablecrusader.com",
                    "moniqueallen.com",
                ],
                search_depth="advanced",
            )
            all_results.extend(result.get("results", []))
        except Exception:
            continue

    return {
        "community_data": [
            {
                "title": r.get("title"),
                "content": r.get("content", "")[:400],
                "url": r.get("url"),
            }
            for r in all_results[:8]
        ],
        "source": "community_search",
    }


async def search_competitors(
    niche: str,
    *,
    max_results: int = 10,
) -> dict[str, Any]:
    """Cerca competitor e top seller su Etsy per una nicchia."""
    query = f"Etsy top sellers {niche} digital downloads shop revenue reviews"
    return await search(
        query,
        max_results=max_results,
        search_depth="advanced",
        include_answer=True,
    )


async def search_keywords(
    niche: str,
    *,
    max_results: int = 10,
) -> dict[str, Any]:
    """Cerca keyword e tag SEO per una nicchia Etsy."""
    query = f"Etsy SEO tags keywords {niche} digital products eRank best tags"
    return await search(
        query,
        max_results=max_results,
        search_depth="advanced",
        include_answer=True,
    )
