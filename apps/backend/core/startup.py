"""Startup helpers — async init functions extracted from api/main.py lifespan.

Each public function corresponds to one logical startup phase and is called
in order from lifespan().  All heavy imports live here; main.py only keeps
imports needed by routes at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("agentpexi.startup")


# ---------------------------------------------------------------------------
# Return-value bundles
# ---------------------------------------------------------------------------


@dataclass
class _PepeBundle:
    pepe: Any
    research_agent: Any
    design_agent: Any


@dataclass
class _AutonomyBundle:
    db: Any
    production_queue: Any
    budget_manager: Any
    publication_policy: Any


@dataclass
class AgentBundle:
    publisher_agent: Any
    analytics_agent: Any
    finance_agent: Any
    recall_agent: Any
    remind_agent: Any
    summarize_agent: Any
    research_personal_agent: Any
    learning_loop: Any
    bundle_strategy: Any
    shop_optimizer: Any
    etsy_ads_manager: Any
    finance_tracker: Any


# ---------------------------------------------------------------------------
# Phase 1 — Memory + shared tools + storage
# ---------------------------------------------------------------------------


async def init_memory(settings, ws_broadcast: Callable) -> Any:
    """Init MemoryManager, wire KnowledgeBridge and WS broadcaster."""
    from apps.backend.core.memory import MemoryManager
    from apps.backend.core.knowledge_bridge import KnowledgeBridge

    memory = MemoryManager()
    await memory.init()
    memory.set_ws_broadcaster(ws_broadcast)

    bridge = KnowledgeBridge(memory=memory)
    memory.set_bridge_callback(bridge.on_new_insight)
    bridge.set_ws_broadcaster(ws_broadcast)

    logger.info("MemoryManager inizializzato + KnowledgeBridge registrato")
    return memory


async def init_tools(settings) -> tuple[Any, Any, Any]:
    """Init shared tools: NotionCalendar, WebSearchTool, TextExtractor."""
    from apps.backend.tools.notion_calendar import NotionCalendar
    from apps.backend.tools.web_search import WebSearchTool
    from apps.backend.tools.text_extract import TextExtractor

    notion_calendar = NotionCalendar(token=getattr(settings, "NOTION_API_TOKEN", ""))
    try:
        await notion_calendar.ensure_database()
        logger.info("Notion Calendar database pronto")
    except Exception as exc:
        logger.warning("notion_calendar.ensure_database fallito (fail-safe): %s", exc)

    web_search = WebSearchTool()
    text_extractor = TextExtractor(max_chars=settings.SUMMARIZE_MAX_CHARS)
    return notion_calendar, web_search, text_extractor


async def init_storage() -> Any:
    """Init StorageManager."""
    from apps.backend.core.storage import StorageManager

    storage = StorageManager()
    storage.ensure_dirs()
    logger.info("StorageManager inizializzato")
    return storage


# ---------------------------------------------------------------------------
# Phase 2 — Pepe orchestrator + core agents
# ---------------------------------------------------------------------------


async def init_pepe(
    memory,
    storage,
    ws_broadcast: Callable,
    telegram_broadcast: Callable,
) -> _PepeBundle:
    """Init Pepe, register research + design agents, start Pepe."""
    from apps.backend.core.pepe import Pepe
    from apps.backend.agents.research import ResearchAgent
    from apps.backend.agents.design import DesignAgent

    pepe = Pepe(memory=memory, ws_broadcaster=ws_broadcast)

    research_agent = ResearchAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_broadcast,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("research", research_agent)

    design_agent = DesignAgent(
        anthropic_client=pepe.client,
        memory=memory,
        storage=storage,
        ws_broadcaster=ws_broadcast,
        get_mock_mode=pepe.get_mock_mode,
    )
    pepe.register_agent("design", design_agent)

    await pepe.start()
    logger.info("Pepe avviato")
    return _PepeBundle(pepe=pepe, research_agent=research_agent, design_agent=design_agent)


async def init_wiki(pepe, settings) -> None:
    """Init WikiManager and attach to pepe.wiki (fail-safe)."""
    from apps.backend.core.wiki import WikiManager

    wiki_base_raw = settings.WIKI_BASE_PATH
    # startup.py lives in core/; project root is parents[3]
    wiki_base = (
        Path(wiki_base_raw)
        if Path(wiki_base_raw).is_absolute()
        else Path(__file__).resolve().parents[3] / wiki_base_raw
    )
    try:
        wiki_manager = WikiManager(wiki_base)
        await wiki_manager.init()
        pepe.wiki = wiki_manager
        logger.info("WikiManager inizializzato — base: %s", wiki_base)
    except Exception as exc:
        logger.warning("WikiManager non avviato (fail-safe): %s", exc)
        pepe.wiki = None


# ---------------------------------------------------------------------------
# Phase 3 — Etsy + Autonomy Layer
# ---------------------------------------------------------------------------


async def init_etsy(memory, pepe) -> Any:
    """Init EtsyAPI."""
    from apps.backend.tools.etsy_api import EtsyAPI

    etsy_api = EtsyAPI(memory=memory, pepe=pepe)
    logger.info("EtsyAPI inizializzato")
    return etsy_api


async def init_autonomy_services(memory) -> _AutonomyBundle:
    """Init ProductionQueueService, BudgetManager, PublicationPolicy."""
    from apps.backend.core.production_queue import ProductionQueueService
    from apps.backend.core.budget_manager import BudgetManager
    from apps.backend.core.publication_policy import PublicationPolicy

    db = await memory.get_db()
    production_queue = ProductionQueueService(db)
    budget_manager = BudgetManager(db)
    publication_policy = PublicationPolicy(db)
    await budget_manager.ensure_defaults()
    await publication_policy.ensure_defaults()
    logger.info(
        "Autonomy Layer (B2): ProductionQueueService, BudgetManager, PublicationPolicy inizializzati"
    )
    return _AutonomyBundle(
        db=db,
        production_queue=production_queue,
        budget_manager=budget_manager,
        publication_policy=publication_policy,
    )


# ---------------------------------------------------------------------------
# Phase 4 — All remaining agents + intelligence layer
# ---------------------------------------------------------------------------


async def init_all_agents(
    pepe,
    memory,
    storage,
    etsy_api,
    ws_broadcast: Callable,
    telegram_broadcast: Callable,
    notion_calendar,
    web_search,
    text_extractor,
    production_queue,
    publication_policy,
) -> AgentBundle:
    """Register publisher/analytics/finance/personal agents; init learning & growth layer."""
    from apps.backend.agents.publisher import PublisherAgent
    from apps.backend.agents.analytics import AnalyticsAgent
    from apps.backend.agents.finance import FinanceAgent
    from apps.backend.agents.recall import RecallAgent
    from apps.backend.agents.remind import RemindAgent
    from apps.backend.agents.summarize import SummarizeAgent
    from apps.backend.agents.research_personal import ResearchPersonalAgent
    from apps.backend.core.learning_loop import LearningLoop
    from apps.backend.core.bundle_strategy import BundleStrategy
    from apps.backend.core.etsy_ads import EtsyAdsManager
    from apps.backend.core.finance_tracker import FinanceTracker
    from apps.backend.core.shop_optimizer import ShopProfileOptimizer

    publisher_agent = PublisherAgent(
        anthropic_client=pepe.client,
        memory=memory,
        storage=storage,
        etsy_api=etsy_api,
        ws_broadcaster=ws_broadcast,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("publisher", publisher_agent)

    learning_loop = LearningLoop(memory=memory)
    logger.info("LearningLoop istanziato")

    bundle_strategy = BundleStrategy(memory=memory, learning_loop=learning_loop)
    logger.info("BundleStrategy istanziato")

    mock_mode = getattr(pepe, "mock_mode", False)

    shop_optimizer = ShopProfileOptimizer(
        memory=memory,
        etsy_client=etsy_api,
        learning_loop=learning_loop,
        mock_mode=mock_mode,
    )
    logger.info("ShopProfileOptimizer istanziato (mock=%s)", mock_mode)

    etsy_ads_manager = EtsyAdsManager(
        etsy_client=etsy_api,
        production_queue=production_queue,
        publication_policy=publication_policy,
        telegram_broadcaster=telegram_broadcast,
        mock_mode=mock_mode,
    )
    logger.info("EtsyAdsManager istanziato (mock=%s)", mock_mode)

    analytics_agent = AnalyticsAgent(
        anthropic_client=pepe.client,
        memory=memory,
        etsy_api=etsy_api,
        ws_broadcaster=ws_broadcast,
        telegram_broadcaster=telegram_broadcast,
        production_queue=production_queue,
        learning_loop=learning_loop,
    )
    pepe.register_agent("analytics", analytics_agent)

    finance_agent = FinanceAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_broadcast,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("finance", finance_agent)

    finance_tracker = FinanceTracker(
        memory=memory,
        telegram_broadcaster=telegram_broadcast,
    )
    logger.info("FinanceTracker istanziato (B5/5.4)")

    recall_agent = RecallAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_broadcast,
    )
    pepe.register_agent("recall", recall_agent)

    remind_agent = RemindAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_broadcast,
        notion_calendar=notion_calendar,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("remind", remind_agent)

    summarize_agent = SummarizeAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_broadcast,
        text_extractor=text_extractor,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("summarize", summarize_agent)

    research_personal_agent = ResearchPersonalAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_broadcast,
        web_search=web_search,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("research_personal", research_personal_agent)

    return AgentBundle(
        publisher_agent=publisher_agent,
        analytics_agent=analytics_agent,
        finance_agent=finance_agent,
        recall_agent=recall_agent,
        remind_agent=remind_agent,
        summarize_agent=summarize_agent,
        research_personal_agent=research_personal_agent,
        learning_loop=learning_loop,
        bundle_strategy=bundle_strategy,
        shop_optimizer=shop_optimizer,
        etsy_ads_manager=etsy_ads_manager,
        finance_tracker=finance_tracker,
    )


# ---------------------------------------------------------------------------
# Phase 5 — Screen watcher
# ---------------------------------------------------------------------------


async def init_screen_watcher(memory, ws_broadcast: Callable) -> tuple[Any, str | None]:
    """Start ScreenWatcher (fail-safe). Returns (watcher_or_None, error_str_or_None)."""
    from apps.backend.screen.watcher import ScreenWatcher

    watcher = ScreenWatcher(memory=memory, ws_broadcaster=ws_broadcast)
    try:
        await watcher.start()
        logger.info("ScreenWatcher avviato")
        return watcher, None
    except Exception as exc:
        logger.warning("ScreenWatcher non avviato: %s", exc)
        return None, str(exc)


# ---------------------------------------------------------------------------
# Phase 6 — AutopilotLoop callables + AutopilotLoop
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Phase 7 — Scheduler
# ---------------------------------------------------------------------------


async def init_scheduler(
    memory,
    ws_broadcast: Callable,
    pepe,
    storage,
    research_agent,
    design_agent,
    publisher_agent,
    analytics_agent,
    finance_agent,
    telegram_broadcast: Callable,
    screen_watcher,
    production_queue,
    budget_manager,
    publication_policy,
    autopilot_loop,
    etsy_api,
    shop_optimizer,
    etsy_ads_manager,
    learning_loop,
) -> Any:
    """Init Scheduler (does NOT start it — caller calls await scheduler.start())."""
    from apps.backend.core.scheduler import Scheduler

    scheduler = Scheduler(
        memory=memory,
        ws_broadcaster=ws_broadcast,
        pepe=pepe,
        storage=storage,
        research_agent=research_agent,
        design_agent=design_agent,
        publisher_agent=publisher_agent,
        analytics_agent=analytics_agent,
        finance_agent=finance_agent,
        telegram_broadcaster=telegram_broadcast,
        screen_watcher=screen_watcher,
        production_queue=production_queue,
        budget_manager=budget_manager,
        publication_policy=publication_policy,
        autopilot_loop=autopilot_loop,
        etsy_client=etsy_api,
        shop_optimizer=shop_optimizer,
        etsy_ads_manager=etsy_ads_manager,
        learning_loop=learning_loop,
    )
    return scheduler


# ---------------------------------------------------------------------------
# Phase 8 — Telegram bot
# ---------------------------------------------------------------------------


async def init_telegram_bot(
    pepe,
    scheduler,
    screen_watcher,
    autopilot_loop,
    production_queue,
    budget_manager,
    publication_policy,
    etsy_api,
    analytics_agent,
    learning_loop,
    bundle_strategy,
    shop_optimizer,
    etsy_ads_manager,
    finance_tracker,
) -> Any:
    """Init and start TelegramBot."""
    from apps.backend.telegram.bot import TelegramBot
    from apps.backend.telegram.dependencies import BotDependencies

    deps = BotDependencies(
        pepe=pepe,
        scheduler=scheduler,
        screen_watcher=screen_watcher,
        autopilot_loop=autopilot_loop,
        production_queue=production_queue,
        budget_manager=budget_manager,
        publication_policy=publication_policy,
        etsy_api=etsy_api,
        analytics_agent=analytics_agent,
        learning_loop=learning_loop,
        bundle_strategy=bundle_strategy,
        shop_optimizer=shop_optimizer,
        etsy_ads_manager=etsy_ads_manager,
        finance_tracker=finance_tracker,
    )
    bot = TelegramBot(deps)
    await bot.start()
    return bot
