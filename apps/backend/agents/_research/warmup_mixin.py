"""WarmupOrchestratorMixin — scaffold for A.4 cold-start bootstrap."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from apps.backend.tools import tavily as tavily_tool
from apps.backend.tools.trends import get_google_trends

logger = logging.getLogger("agentpexi.research.warmup")


def _infer_product_type(query: str) -> str:
    """Infer product type from audience-level query keywords."""
    q = query.lower()
    if any(w in q for w in ("wall art", "art print", "coloring", "abc learning", "educational worksheet")):
        return "digital_art_png"
    return "printable_pdf"


class WarmupOrchestratorMixin:
    """Scaffold for the A.4 WarmupOrchestrator.

    Provides section_sweep(section_key, top_k=5) — runs 6 audience-level
    queries per section in parallel (Semaphore 3), scores results and returns
    top_k candidates for ChromaDB storage + Telegram approval.

    Discovery stays separate: _ResearchDiscoveryMixin is NOT modified.
    """

    _DISCOVERY_CATEGORIES_BY_SECTION: dict[str, list[str]] = {
        "party_celebrations": [

            "wedding invitation printable for boho brides",
            "baby shower printable for modern minimalist moms",
            "birthday party printable for teenage girls",
            "graduation party printable for college seniors",
            "bachelorette party printable kit",
            "bridal shower games printable",
        ],
        "wellness_selfcare": [
            "affirmation cards for women with anxiety",
            "vision board kit for women in their 30s",
            "gratitude journal printable for burnout recovery",
            "self-care checklist for new moms",
            "goal tracker printable for entrepreneurs",
            "mental health check-in printable",
        ],
        "planners_organizers": [
            "ADHD daily planner printable for adults",
            "budget planner for couples printable",
            "meal planner for busy moms",
            "student planner for college seniors",
            "weekly planner for work from home",
            "habit tracker for anxiety sufferers",
        ],
        "kids_learning": [
            "Montessori activity printable for toddlers",
            "homeschool planner for kindergarten moms",
            "ABC learning printable for preschool parents",
            "bedtime routine chart for toddlers",
            "educational worksheet for ADHD kids",
            "coloring pages for toddler girls",
        ],
    }
    # Single-point-of-change: all valid section keys derived from the dict above.
    # Code that iterates sections (e.g. full warmup sweep) should use this constant.
    _WARMUP_SECTION_KEYS: tuple[str, ...] = tuple(_DISCOVERY_CATEGORIES_BY_SECTION)

    async def section_sweep(
        self,
        section_key: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Research all audience queries for a section in parallel (Semaphore 3).

        Returns up to `top_k` scored candidates for the section.
        Unknown `section_key` returns [].
        """
        queries = self._DISCOVERY_CATEGORIES_BY_SECTION.get(section_key, [])
        if not queries:
            logger.warning("warmup: unknown section_key=%r", section_key)
            return []

        semaphore = asyncio.Semaphore(3)

        async def _bounded(query: str) -> list[dict[str, Any]]:
            async with semaphore:
                try:
                    return await self._research_audience_query(query, section_key)
                except Exception as exc:
                    logger.warning(
                        "warmup: _research_audience_query failed query=%r section=%r: %s",
                        query, section_key, exc,
                    )
                    return []

        results = await asyncio.gather(*[_bounded(q) for q in queries])

        # Flatten + deduplicate by niche:product_type key
        seen: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for batch in results:
            for item in batch:
                key = f"{item.get('niche', '').lower().strip()}:{item.get('product_type', '')}"
                if key not in seen and item.get("niche", "").strip():
                    seen.add(key)
                    candidates.append(item)

        # Score with EntryPointScoring if available; otherwise return top_k raw
        if candidates and hasattr(self, "_get_entry_point_scorer"):
            try:
                scorer = await self._get_entry_point_scorer()
                scored = await scorer.rank_candidates(candidates, top_k=top_k)
                return [
                    {
                        "niche":        sc.niche,
                        "product_type": sc.product_type or "printable_pdf",
                        "score":        sc.final_score,
                        "section":      section_key,
                        "source":       f"warmup_{section_key}",
                    }
                    for sc in scored
                ]
            except Exception as exc:
                logger.warning("warmup: scoring failed for section=%r: %s", section_key, exc)

        return candidates[:top_k]

    async def _research_audience_query(
        self,
        query: str,
        section_key: str,
    ) -> list[dict[str, Any]]:
        """Research a single audience-level query via Tavily + Google Trends.

        Returns a flat list of candidate dicts:
        {"niche": str, "product_type": str, "source": str, "section": str}
        """
        product_type = _infer_product_type(query)
        etsy_query = f"etsy {query} best selling printable {datetime.now().year}"

        tavily_result, trends_result = await asyncio.gather(
            self._call_tool(
                tool_name="tavily",
                action="search",
                input_params={"query": etsy_query},
                fn=tavily_tool.search,
                query=etsy_query,
                max_results=5,
                search_depth="basic",
            ),
            self._call_tool(
                tool_name="google_trends",
                action="get_trends",
                input_params={"keyword": query},
                fn=get_google_trends,
                keyword=query,
            ),
            return_exceptions=True,
        )

        candidates: list[dict[str, Any]] = []
        source_tag = f"warmup_{section_key}"

        is_trending = (
            not isinstance(trends_result, Exception)
            and isinstance(trends_result, dict)
            and trends_result.get("percent_change", 0) > 10
        )

        # The audience query itself is always a candidate if Tavily didn't hard-fail.
        # When Google Trends shows growth, encode the trending signal in the source tag
        # (single entry per niche — avoids dedup in section_sweep silently dropping
        # the _trending tag if two entries share the same niche:product_type key).
        if not isinstance(tavily_result, Exception):
            candidates.append({
                "niche":        query,
                "product_type": product_type,
                "source":       f"{source_tag}_trending" if is_trending else source_tag,
                "section":      section_key,
            })

        return candidates
