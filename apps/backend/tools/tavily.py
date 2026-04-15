"""Wrapper Tavily API — search + extract per research di mercato."""

from __future__ import annotations

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
    """Estrae dati reali dalla pagina di ricerca Etsy (prezzi, titoli, listing reali)."""
    from urllib.parse import quote_plus

    niche_encoded = quote_plus(niche)
    etsy_url = f"https://www.etsy.com/search?q={niche_encoded}&category=digital_downloads"
    extracted = await extract([etsy_url])

    erank_url = f"https://erank.com/keyword-explorer?term={niche_encoded}"
    erank_data = await extract([erank_url])

    return {
        "etsy_listings_raw": extracted,
        "erank_keyword_data": erank_data,
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
