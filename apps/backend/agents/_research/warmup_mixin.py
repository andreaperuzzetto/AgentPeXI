"""WarmupOrchestratorMixin — scaffold for A.4 cold-start bootstrap."""
from __future__ import annotations

import asyncio
import json
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

    async def _broadcast(self, event: dict) -> None:
        """WebSocket broadcast — real impl injected by AgentBase mixin."""
        if hasattr(self, "_ws_broadcast") and self._ws_broadcast is not None:
            try:
                await self._ws_broadcast(event)
            except Exception as exc:
                logger.warning("warmup: _broadcast failed: %s", exc)

    async def run_full_warmup(self) -> dict[str, Any]:
        """Full warmup run: sweep all 4 sections in parallel, store in ChromaDB,
        synthesize cross-section report via Sonnet, emit WebSocket events.

        Returns:
            {
                "all_candidates": dict[section_key, list[candidate_dict]],
                "total": int,
                "report": {"recommended": [...], "report_text": str},
            }
        """
        all_candidates: dict[str, list[dict[str, Any]]] = {}

        async def _sweep_and_store(section_key: str) -> list[dict[str, Any]]:
            candidates = await self.section_sweep(section_key, top_k=5)
            await self._store_warmup_candidates(section_key, candidates)
            await self._broadcast({
                "type": "warmup_progress",
                "section": section_key,
                "candidates_count": len(candidates),
            })
            return candidates

        results = await asyncio.gather(
            *[_sweep_and_store(s) for s in self._WARMUP_SECTION_KEYS],
            return_exceptions=True,
        )

        for section_key, result in zip(self._WARMUP_SECTION_KEYS, results):
            if isinstance(result, Exception):
                logger.error(
                    "warmup: section_sweep failed section=%r: %s",
                    section_key, result,
                )
                all_candidates[section_key] = []
            else:
                all_candidates[section_key] = result  # type: ignore[assignment]

        total = sum(len(v) for v in all_candidates.values())

        report = await self._synthesize_warmup_report(all_candidates)

        await self._broadcast({
            "type": "warmup_completed",
            "candidates_count": total,
            "recommended_count": len(report.get("recommended", [])),
            "sections": {k: len(v) for k, v in all_candidates.items()},
        })

        return {
            "all_candidates": all_candidates,
            "total": total,
            "report": report,
        }

    async def _store_warmup_candidates(
        self,
        section_key: str,
        candidates: list[dict[str, Any]],
    ) -> list[str]:
        """Store warmup candidates in ChromaDB with type='warmup_candidate'.

        Returns list of stored doc IDs (empty strings skipped).
        """
        doc_ids: list[str] = []
        for candidate in candidates:
            niche = candidate.get("niche", "").strip()
            if not niche:
                continue
            text = (
                f"warmup candidate: {niche} "
                f"({candidate.get('product_type', 'printable_pdf')}) — "
                f"section: {section_key}"
            )
            metadata: dict[str, str] = {
                "type":         "warmup_candidate",
                "section":      section_key,
                "niche":        niche,
                "product_type": candidate.get("product_type", "printable_pdf"),
                "score":        str(round(float(candidate.get("score") or 0.0), 4)),
                "status":       "pending",
                "source":       candidate.get("source", f"warmup_{section_key}"),
            }
            doc_id = await self.memory.store_insight(text=text, metadata=metadata)
            if doc_id:
                doc_ids.append(doc_id)
        return doc_ids

    async def _synthesize_warmup_report(
        self,
        all_candidates: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Call Sonnet once to synthesize cross-section warmup candidates.

        Returns:
            {
                "recommended": list[candidate_dict],  # top 6–8 for approval
                "report_text": str,                   # formatted Telegram message
            }
        Falls back to top-scored candidates from input if Sonnet output is malformed.
        """
        from apps.backend.core.config import MODEL_SONNET

        # Build a compact summary of candidates for the prompt
        lines: list[str] = []
        for section, candidates in all_candidates.items():
            for c in candidates:
                lines.append(
                    f"- [{section}] {c.get('niche', 'N/A')} "
                    f"({c.get('product_type', 'printable_pdf')}) "
                    f"score={(c.get('score') or 0.0):.2f}"
                )

        candidates_text = "\n".join(lines) if lines else "(nessun candidato trovato)"

        prompt = (
            "Sei un esperto di nicchie Etsy per printable digitali.\n\n"
            "Di seguito trovi i candidati emersi dal warmup, organizzati per sezione Etsy:\n\n"
            f"{candidates_text}\n\n"
            "Seleziona i migliori 6-8 candidati per il batch iniziale. Considera:\n"
            "- diversity tra sezioni (almeno 1 per sezione se disponibile)\n"
            "- score ≥0.65 preferito\n"
            "- audience ben definita (es. 'ADHD adult', 'bride on a budget')\n\n"
            "Rispondi SOLO con JSON valido in questo formato:\n"
            "{\n"
            '  "recommended": [\n'
            '    {"niche": "...", "product_type": "...", "score": 0.0, "section": "...", "rationale": "..."},\n'
            "    ...\n"
            "  ],\n"
            '  "report_text": "Warmup completato — N niche raccomandate. [Breve sintesi 2-3 righe]"\n'
            "}"
        )

        # Call Sonnet and parse JSON; fallback to top-scored on any error
        try:
            raw = await self._call_llm(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="Sei un analista di mercato Etsy specializzato in digital products.",
                model_override=MODEL_SONNET,
                max_tokens=2048,
            )
            data = json.loads(raw)
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("recommended"), list)
                or not all(isinstance(item, dict) for item in data.get("recommended", []))
                or not isinstance(data.get("report_text"), str)
            ):
                raise AssertionError("invalid structure")
            return {"recommended": data["recommended"], "report_text": data["report_text"]}
        except (json.JSONDecodeError, AssertionError, TypeError, AttributeError):
            logger.warning("warmup: Sonnet synthesis returned malformed JSON — using fallback")
        except Exception:
            logger.warning("warmup: LLM API failure — using fallback")

        # Fallback: collect all candidates, sort by score desc, take top 8
        flat: list[dict[str, Any]] = []
        for candidates in all_candidates.values():
            flat.extend(candidates)
        flat.sort(key=lambda c: float(c.get("score") or 0.0), reverse=True)
        top = flat[:8]
        return {
            "recommended": top,
            "report_text": (
                f"⚠️ Sintesi Sonnet non disponibile — {len(top)} candidati scelti per score."
            ),
        }
