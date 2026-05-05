"""AutopilotLoop callables builder and init."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("agentpexi.startup")


def build_autopilot_callables(
    memory,
    pepe,
    production_queue,
    bundle_strategy,
    learning_loop,
) -> tuple[Callable, Callable, Callable]:
    """Build and return the three AutopilotLoop callables as closures."""
    from apps.backend.core.models import AgentTask as _AgentTask, TaskStatus as _TaskStatus

    async def _design_pipeline(item_id: int, niche_data: dict) -> None:
        niche = niche_data.get("niche", "")
        product_type = niche_data.get("product_type", "digital_print")
        keywords = niche_data.get("keywords", [])

        design_task = _AgentTask(
            agent_name="design",
            input_data={
                "niche": niche,
                "product_type": product_type,
                "keywords": keywords,
                "color_schemes": niche_data.get("color_schemes", []),
                "source": "autopilot",
            },
            source="autopilot",
        )
        try:
            result = await pepe.dispatch_task(design_task)
        except Exception as exc:
            logger.error("design_pipeline: DesignAgent fallito item=%d: %s", item_id, exc)
            return

        if result.status != _TaskStatus.COMPLETED:
            logger.warning(
                "design_pipeline: DesignAgent non completato item=%d status=%s",
                item_id, result.status,
            )
            return

        out = result.output_data or {}
        variants = out.get("variants", [])

        thumbnail_path = ""
        image_url = ""
        if variants:
            first = variants[0]
            thumbnail_path = first.get("thumbnail_path") or first.get("output_path") or ""
            image_url = first.get("image_url") or ""

        title = (
            f"{niche.replace('_', ' ').title()} — {product_type.replace('_', ' ').title()}"
        )
        tags = keywords[:13]

        pricing = niche_data.get("pricing") or {}
        if isinstance(pricing, dict) and pricing.get("price"):
            price = float(pricing["price"])
        else:
            price = float(niche_data.get("price") or 4.99)

        await production_queue.set_design_ready(
            item_id=item_id,
            design_prompt=out.get("cover_title") or out.get("template") or niche,
            image_url=image_url,
            thumbnail_path=thumbnail_path,
            title=title,
            description="",
            tags=tags,
            price=price,
            llm_cost=result.cost_usd or 0.0,
            image_cost=float(out.get("image_cost_usd") or 0.0),
        )
        logger.info(
            "design_pipeline: item=%d → pending_approval (niche=%s, thumbnail=%s)",
            item_id, niche, thumbnail_path or "nessuna",
        )

    async def _niche_picker() -> dict | None:
        """
        Sceglie la prossima niche con rotazione data-driven. — B4/4.7

        Strategia a cascata:
          1. niche_intelligence — multi-candidate scoring
          2. Unexplored candidates (LearningLoop)
          3. ResearchAgent discovery autonoma
        """
        last_niche = ""
        try:
            db_conn = await memory.get_db()
            cursor_rep = await db_conn.execute(
                """
                SELECT niche FROM production_queue
                WHERE status = 'published'
                ORDER BY published_at DESC LIMIT 1
                """
            )
            rep_row = await cursor_rep.fetchone()
            last_niche = rep_row["niche"] if rep_row else ""
        except Exception as exc:
            logger.debug("niche_picker: lettura last_niche fallita (non bloccante): %s", exc)

        ctr_low_niches: set[str] = set()
        try:
            db_conn = await memory.get_db()
            cursor = await db_conn.execute(
                """
                SELECT DISTINCT pq.niche
                FROM listing_performance lp
                JOIN production_queue pq ON lp.production_queue_id = pq.id
                WHERE lp.ladder_level = 'ctr_low'
                  AND lp.snapshot_at > unixepoch() - 14 * 86400
                """
            )
            ctr_rows = await cursor.fetchall()
            ctr_low_niches = {r["niche"] for r in ctr_rows}
            if ctr_low_niches:
                logger.info(
                    "niche_picker: %d niche CTR_LOW rilevate → regen_thumbnail boost: %s",
                    len(ctr_low_niches), list(ctr_low_niches)[:5],
                )
        except Exception as exc:
            logger.debug("niche_picker: lettura ctr_low niches fallita: %s", exc)

        # 1. Multi-candidate scoring da niche_intelligence
        try:
            db_conn = await memory.get_db()
            cursor = await db_conn.execute(
                """
                SELECT niche, product_type, performance_score, confidence_level
                FROM niche_intelligence
                WHERE performance_score IS NOT NULL AND performance_score > 0
                ORDER BY performance_score DESC
                LIMIT 10
                """
            )
            rows = await cursor.fetchall()

            scored = []
            for row in rows:
                niche = row["niche"]
                product_type = row["product_type"]
                score = float(row["performance_score"])
                confidence = row["confidence_level"] or "low"

                if score < 0.3 and confidence == "high":
                    logger.debug(
                        "niche_picker: skip perdente [%s] score=%.3f conf=%s",
                        niche, score, confidence,
                    )
                    continue

                if niche == last_niche:
                    score *= 0.7

                regen_thumbnail = False
                if niche in ctr_low_niches:
                    score *= 1.3
                    regen_thumbnail = True
                    logger.debug("niche_picker: boost CTR_LOW [%s] score→%.3f", niche, score)

                scored.append({
                    "niche": niche,
                    "product_type": product_type,
                    "entry_score": round(score, 3),
                    "keywords": [],
                    "regen_thumbnail": regen_thumbnail,
                })

            if scored:
                scored.sort(key=lambda x: x["entry_score"], reverse=True)
                winner = scored[0]
                logger.info(
                    "niche_picker: selezionata [%s/%s] score=%.3f",
                    winner["niche"], winner["product_type"], winner["entry_score"],
                )
                return winner

        except Exception as exc:
            logger.warning("niche_picker: lettura niche_intelligence fallita: %s", exc)

        # 2. Unexplored candidates
        try:
            unexplored = await learning_loop.get_unexplored_candidates()
            if unexplored:
                best = unexplored[0]
                logger.info(
                    "niche_picker: unexplored [%s/%s] score=%.3f",
                    best["niche"], best["product_type"], best["performance_score"],
                )
                return {
                    "niche": best["niche"],
                    "product_type": best["product_type"],
                    "entry_score": best["performance_score"],
                    "keywords": [],
                }
        except Exception as exc:
            logger.warning("niche_picker: get_unexplored_candidates fallito: %s", exc)

        # 3. Ultimate fallback: ResearchAgent discovery autonoma
        logger.info("niche_picker: nessun dato locale — avvio ResearchAgent")
        research_task = _AgentTask(
            agent_name="research",
            input_data={"mode": "autonomous_discovery", "source": "autopilot"},
            source="autopilot",
        )
        try:
            result = await pepe.dispatch_task(research_task)
            out = result.output_data or {}
            logger.info(
                "niche_picker: ResearchAgent status=%s candidates_analyzed=%s candidates_viable=%s",
                result.status,
                out.get("candidates_analyzed", "?"),
                out.get("candidates_viable", "?"),
            )
            if result.status.value not in ("completed",):
                err = out.get("error", "nessun dettaglio")
                logger.warning("niche_picker: ResearchAgent FAILED — %s", err)
                return None

            winner = out.get("winner")
            if winner and isinstance(winner, dict) and (winner.get("niche") or winner.get("name")):
                logger.info(
                    "niche_picker: winner='%s' product_type='%s' confidence=%s",
                    winner.get("niche") or winner.get("name"),
                    winner.get("product_type", "printable_pdf"),
                    winner.get("confidence", "?"),
                )
                brief = winner.get("brief", {}) or {}
                pricing = brief.get("pricing") or winner.get("pricing") or {}
                keywords = brief.get("keywords") or winner.get("keywords") or []
                return {
                    "niche": winner.get("niche") or winner.get("name") or "",
                    "product_type": winner.get("product_type", "printable_pdf"),
                    "keywords": keywords,
                    "entry_score": float(winner.get("confidence") or 0.5),
                    "pricing": pricing,
                }

            niches = out.get("niches", [])
            if niches and isinstance(niches[0], dict):
                best = niches[0]
                logger.info(
                    "niche_picker: fallback niches[0]='%s'",
                    best.get("name") or best.get("niche"),
                )
                return {
                    "niche": best.get("name") or best.get("niche") or "",
                    "product_type": (
                        best.get("recommended_product_type")
                        or best.get("product_type", "printable_pdf")
                    ),
                    "keywords": best.get("keywords", []),
                    "entry_score": float(
                        best.get("final_score") or best.get("confidence") or 0.5
                    ),
                    "pricing": best.get("pricing", {}),
                }
            logger.warning(
                "niche_picker: ResearchAgent completato ma nessuna niche usabile nell'output"
            )
        except Exception as exc:
            logger.error("niche_picker: ResearchAgent eccezione: %s", exc)

        return None

    async def _bundle_checker() -> dict | None:
        """Controlla bundle-ready niches. — B4/4.7"""
        try:
            candidates = await bundle_strategy.check_all_niches()
        except Exception as exc:
            logger.warning("bundle_checker: check_all_niches fallito: %s", exc)
            return None

        if not candidates:
            return None

        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]
        spec = best["spec"]

        logger.info(
            "bundle_checker: bundle-ready [%s] score=%.3f (%d componenti)",
            spec["niche"], best["score"], spec["n_components"],
        )
        return {
            "niche": spec["niche"],
            "product_type": "bundle",
            "keywords": spec.get("keywords", []),
            "entry_score": spec.get("entry_score", best["score"]),
            "suggested_price": spec.get("suggested_price"),
            "component_titles": spec.get("component_titles", []),
            "component_images": spec.get("component_images", []),
            "is_bundle": True,
        }

    return _design_pipeline, _niche_picker, _bundle_checker


async def init_autopilot_loop(
    db,
    production_queue,
    budget_manager,
    publication_policy,
    bot_send: Callable,
    bot_send_markup: Callable,
    design_pipeline: Callable,
    niche_picker: Callable,
    bundle_checker: Callable,
) -> Any:
    """Init AutopilotLoop."""
    from apps.backend.core.autopilot_loop import AutopilotLoop

    loop = AutopilotLoop(
        db=db,
        queue=production_queue,
        budget=budget_manager,
        policy=publication_policy,
        bot_send=bot_send,
        bot_send_markup=bot_send_markup,
        design_pipeline=design_pipeline,
        niche_picker=niche_picker,
        bundle_checker=bundle_checker,
    )
    logger.info("AutopilotLoop istanziato")
    return loop
