"""MarketDataAgent — shop-level competitive analysis mixin (C.3)."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from apps.backend.core.config import MODEL_HAIKU, MODEL_SONNET, settings
from apps.backend.tools import tavily as tavily_tool

logger = logging.getLogger("agentpexi.market_data")


class _ShopAnalysisMixin:
    """Mixin: analisi competitiva a livello shop per nicchia (C.3).

    Flow di _get_competitor_shop_analysis:
      0. Cache check ChromaDB (30 giorni)
      1. Estrai shop names dai top_sellers già in ChromaDB (research_report)
      2. Se < 3 shop trovati → Tavily discovery per shop names aggiuntivi
      3. Haiku analysis per max 5 shop (per-shop Tavily + structured JSON)
      4. Sonnet cross-shop synthesis → gap_to_exploit
      5. Salva result in ChromaDB con cache_until = now + 30gg
    """

    async def _get_competitor_shop_analysis(
        self,
        niche: str,
        section_key: str,
    ) -> dict | None:
        """Analisi competitiva a livello shop per una nicchia.

        Returns None in mock_mode or if no shop data found.
        """
        if getattr(self, "_mock", False):
            return None

        # Step 0 — Cache check
        cached = await self._memory.query_chromadb(
            query=f"competitor shop analysis {niche}",
            n_results=1,
            where={"type": "competitor_shop_analysis", "niche": niche},
        )
        if cached:
            meta = cached[0].get("metadata", {})
            cache_until_str = meta.get("cache_until", "")
            if cache_until_str:
                try:
                    cache_until = datetime.fromisoformat(cache_until_str)
                    if cache_until.tzinfo is None:
                        cache_until = cache_until.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) < cache_until:
                        logger.info("market_data: cache hit competitor_shop_analysis '%s'", niche)
                        try:
                            return json.loads(cached[0].get("document", "{}"))
                        except (ValueError, TypeError):
                            pass  # cache corrotta — ri-fetch
                except (ValueError, TypeError):
                    pass

        # Step 1 — Estrai shop names da ChromaDB research_report
        research_cached = await self._memory.query_chromadb(
            query=f"Research report per nicchia '{niche}'",
            n_results=1,
            where={"type": "research_report", "niche": niche},
        )
        shop_names: list[str] = []
        if research_cached:
            try:
                doc = json.loads(research_cached[0].get("document", "{}"))
            except (ValueError, TypeError):
                doc = {}
            for n_data in doc.get("niches", []):
                for shop in n_data.get("competition", {}).get("top_sellers", []):
                    if shop and shop not in shop_names:
                        shop_names.append(shop)

        # Step 2 — Discovery Tavily se < 3 shop unici
        if len(shop_names) < 3:
            try:
                tavily_result = await tavily_tool.search(
                    query=f"etsy top sellers digital printables {niche} shop",
                    max_results=5,
                )
                urls = [r.get("url", "") for r in (tavily_result or {}).get("results", [])]
                for url in urls:
                    match = re.search(r"etsy\.com/shop/([^/?#]+)", url)
                    if match:
                        sname = match.group(1)
                        if sname not in shop_names:
                            shop_names.append(sname)
            except Exception as e:
                logger.warning("market_data: Tavily discovery fallito per C.3 '%s': %s", niche, e)

        if not shop_names:
            return None

        shop_names = shop_names[:5]  # max 5 shop

        # Step 3 — Haiku analysis per ogni shop (per-shop Tavily + structured JSON)
        shop_analyses: list[dict] = []
        for shop_name in shop_names:
            try:
                shop_data = await tavily_tool.search(
                    query=f"etsy shop {shop_name} digital products reviews listing",
                    max_results=3,
                )
                analysis = await self._call_haiku_shop_analysis(shop_name, shop_data, niche)
                if analysis:
                    shop_analyses.append(analysis)
            except Exception as e:
                logger.warning("market_data: shop analysis fallita per '%s': %s", shop_name, e)

        if not shop_analyses:
            return None

        # Step 4 — Sonnet cross-shop synthesis → gap_to_exploit
        gap_to_exploit = await self._synthesize_shop_gaps(shop_analyses, niche)

        result = {
            "niche": niche,
            "section_key": section_key,
            "shops_analyzed": len(shop_analyses),
            "shops": shop_analyses,
            "gap_to_exploit": gap_to_exploit,
            "gap_summary": gap_to_exploit[:200] if gap_to_exploit else "",
        }

        # Step 5 — Salva in ChromaDB (cache 30gg)
        now = datetime.now(timezone.utc)
        cache_until = now + timedelta(days=30)
        await self._memory.store_insight(
            json.dumps(result),
            metadata={
                "type": "competitor_shop_analysis",
                "niche": niche,
                "section_key": section_key,
                "shops_analyzed": str(len(shop_analyses)),
                "gap_summary": result["gap_summary"],
                "created_at": now.isoformat(),
                "cache_until": cache_until.isoformat(),
            },
        )

        return result

    async def _call_haiku_shop_analysis(
        self,
        shop_name: str,
        shop_data: dict,
        niche: str,
    ) -> dict | None:
        """Haiku analizza 1 shop Etsy → structured JSON."""
        prompt = (
            f"Analizza questo shop Etsy: '{shop_name}' nel contesto della nicchia '{niche}'.\n\n"
            f"## Dati shop da web\n{str(shop_data)[:2000]}\n\n"
            f"Produce JSON valido con schema:\n"
            f'{{"shop_name": "", "estimated_listing_count": 0, '
            f'"primary_niches": [], "section_structure": "", '
            f'"estimated_aov_usd": 0.0, "audience_served": "", '
            f'"what_they_do_well": "", "what_they_dont_do": "", '
            f'"threat_level": "high|medium|low"}}\n'
            f"Rispondi SOLO con JSON valido, senza markdown."
        )
        if not settings.ANTHROPIC_API_KEY:
            logger.warning("market_data: ANTHROPIC_API_KEY non configurata — skip shop analysis")
            return None
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        try:
            msg = await client.messages.create(
                model=MODEL_HAIKU,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            return json.loads(raw)
        except Exception as e:
            logger.warning("market_data: Haiku shop analysis fallita per '%s': %s", shop_name, e)
            return None
        finally:
            await client.close()

    async def _synthesize_shop_gaps(
        self,
        shop_analyses: list[dict],
        niche: str,
    ) -> str:
        """Sonnet cross-shop synthesis → gap_to_exploit aggregato (max 300 chars)."""
        prompt = (
            f"Analizza questi {len(shop_analyses)} shop Etsy competitor per '{niche}'.\n\n"
            f"## Shop analyses\n{str(shop_analyses)[:3000]}\n\n"
            f"Produce UN SOLO testo (max 300 chars) che descrive "
            f"il GAP principale non coperto dai competitor che PexiomStudio può sfruttare.\n"
            f"Esempio: 'Nessuno offre bundle audience-specific per mamme ADHD. "
            f"Gap price point €2-3 su tripwire entry. Opportunity: formato A5 non utilizzato.'"
        )
        if not settings.ANTHROPIC_API_KEY:
            logger.warning("market_data: ANTHROPIC_API_KEY non configurata — skip gap synthesis")
            return ""
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        try:
            msg = await client.messages.create(
                model=MODEL_SONNET,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()[:300]
        except Exception as e:
            logger.warning("market_data: Sonnet gap synthesis fallita: %s", e)
            return ""
        finally:
            await client.close()
