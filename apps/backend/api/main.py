"""FastAPI + WebSocket — API principale AgentPeXI."""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import apps.backend.api.state as state
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from apps.backend.core.config import settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentTask

from apps.backend.api.routers import (
    autopilot,
    etsy,
    finance,
    memory_routes,
    personal,
    screen,
    system,
    wiki,
)

# ------------------------------------------------------------------
# Logging — console + file rotante in logs/
# ------------------------------------------------------------------

_LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
_LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            _LOG_DIR / "agentpexi.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
    ],
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("faster_whisper").setLevel(logging.WARNING)

logger = logging.getLogger("agentpexi.api")


# ------------------------------------------------------------------
# Lifespan
# ------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: MemoryManager, Pepe, workers, Telegram bot. Shutdown: graceful stop."""

    from apps.backend.core.pepe import Pepe
    from apps.backend.core.scheduler import Scheduler
    from apps.backend.core.storage import StorageManager
    from apps.backend.telegram.bot import TelegramBot
    from apps.backend.telegram.dependencies import BotDependencies
    from apps.backend.tools.etsy_api import EtsyAPI
    from apps.backend.agents.research import ResearchAgent
    from apps.backend.agents.design import DesignAgent
    from apps.backend.agents.publisher import PublisherAgent
    from apps.backend.agents.analytics import AnalyticsAgent
    from apps.backend.agents.finance import FinanceAgent
    from apps.backend.core.learning_loop import LearningLoop
    from apps.backend.core.bundle_strategy import BundleStrategy
    from apps.backend.core.etsy_ads import EtsyAdsManager
    from apps.backend.core.finance_tracker import FinanceTracker
    from apps.backend.core.shop_optimizer import ShopProfileOptimizer
    from apps.backend.agents.recall import RecallAgent
    from apps.backend.agents.remind import RemindAgent
    from apps.backend.agents.summarize import SummarizeAgent
    from apps.backend.agents.research_personal import ResearchPersonalAgent
    from apps.backend.screen.watcher import ScreenWatcher
    from apps.backend.tools.notion_calendar import NotionCalendar
    from apps.backend.tools.web_search import WebSearchTool
    from apps.backend.tools.text_extract import TextExtractor

    # 1. MemoryManager
    state.memory = MemoryManager()
    await state.memory.init()
    # Inietta WS broadcaster per eventi memory_query (neural brain live activation)
    state.memory.set_ws_broadcaster(state.ws_manager.broadcast)
    # Inietta KnowledgeBridge per analisi cross-domain fire-and-forget
    from apps.backend.core.knowledge_bridge import KnowledgeBridge
    _bridge = KnowledgeBridge(memory=state.memory)
    state.memory.set_bridge_callback(_bridge.on_new_insight)
    _bridge.set_ws_broadcaster(state.ws_manager.broadcast)  # eventi knowledge_bridge → BridgeActivity HUD (FE-0.7)
    logger.info("MemoryManager inizializzato + KnowledgeBridge registrato")

    # 1c. Tools condivisi — istanziati una volta sola (DI negli agenti Personal)
    notion_calendar = NotionCalendar(token=getattr(settings, "NOTION_API_TOKEN", ""))
    try:
        await notion_calendar.ensure_database()
        logger.info("Notion Calendar database pronto")
    except Exception as exc:
        logger.warning("notion_calendar.ensure_database fallito (fail-safe): %s", exc)
    web_search = WebSearchTool()
    text_extractor = TextExtractor(max_chars=settings.SUMMARIZE_MAX_CHARS)

    # 1b. StorageManager (singleton)
    state.storage = StorageManager()
    state.storage.ensure_dirs()
    logger.info("StorageManager inizializzato")

    # 2. Pepe orchestratore
    state.pepe = Pepe(memory=state.memory, ws_broadcaster=state.ws_manager.broadcast)

    # Funzione broadcast Telegram — definita subito dopo Pepe (usata da tutti gli agenti)
    async def telegram_broadcast(msg: str) -> None:
        if state.pepe and hasattr(state.pepe, "notify_telegram"):
            await state.pepe.notify_telegram(msg, priority=True)

    # Funzione broadcast con inline keyboard (lazy — telegram_bot istanziato dopo)
    async def telegram_broadcast_markup(msg: str, reply_markup) -> None:
        """Invia messaggio con InlineKeyboardMarkup — usata da AutopilotLoop per approve/skip."""
        if not state.telegram_bot or not settings.TELEGRAM_CHAT_ID:
            return
        try:
            await state.telegram_bot._app.bot.send_message(
                chat_id=int(settings.TELEGRAM_CHAT_ID),
                text=msg,
                reply_markup=reply_markup,
            )
        except Exception as exc:
            logger.warning("telegram_broadcast_markup fallito: %s", exc)

    # 2b. Registra agenti disponibili
    research_agent = ResearchAgent(
        anthropic_client=state.pepe.client,
        memory=state.memory,
        ws_broadcaster=state.ws_manager.broadcast,
        telegram_broadcaster=telegram_broadcast,
    )
    state.pepe.register_agent("research", research_agent)

    # 2c. Design Agent
    design_agent = DesignAgent(
        anthropic_client=state.pepe.client,
        memory=state.memory,
        storage=state.storage,
        ws_broadcaster=state.ws_manager.broadcast,
        get_mock_mode=state.pepe.get_mock_mode,
    )
    state.pepe.register_agent("design", design_agent)

    await state.pepe.start()
    logger.info("Pepe avviato")

    # 2c-wiki. WikiManager — Step 5.2.5
    # WIKI_BASE_PATH può essere relativo (es. "knowledge_base") o assoluto
    # (es. vault Obsidian). Path resolution: relativo → radice progetto.
    from apps.backend.core.wiki import WikiManager
    _wiki_base_raw = settings.WIKI_BASE_PATH
    _wiki_base = (
        Path(_wiki_base_raw)
        if Path(_wiki_base_raw).is_absolute()
        else Path(__file__).resolve().parents[3] / _wiki_base_raw
    )
    try:
        wiki_manager = WikiManager(_wiki_base)
        await wiki_manager.init()
        state.pepe.wiki = wiki_manager
        logger.info("WikiManager inizializzato — base: %s", _wiki_base)
    except Exception as exc:
        logger.warning("WikiManager non avviato (fail-safe): %s", exc)
        state.pepe.wiki = None

    # 2d. EtsyAPI
    state.etsy_api = EtsyAPI(memory=state.memory, pepe=state.pepe)
    logger.info("EtsyAPI inizializzato")

    # 2d-b2. Autonomy Layer — Blocco 2
    from apps.backend.core.production_queue import ProductionQueueService
    from apps.backend.core.budget_manager import BudgetManager
    from apps.backend.core.publication_policy import PublicationPolicy
    from apps.backend.core.autopilot_loop import AutopilotLoop

    _db = await state.memory.get_db()
    state.production_queue   = ProductionQueueService(_db)
    state.budget_manager     = BudgetManager(_db)
    state.publication_policy = PublicationPolicy(_db)
    await state.budget_manager.ensure_defaults()
    await state.publication_policy.ensure_defaults()
    logger.info("Autonomy Layer (B2): ProductionQueueService, BudgetManager, PublicationPolicy inizializzati")

    # 2e. Publisher Agent
    publisher_agent = PublisherAgent(
        anthropic_client=state.pepe.client,
        memory=state.memory,
        storage=state.storage,
        etsy_api=state.etsy_api,
        ws_broadcaster=state.ws_manager.broadcast,
        telegram_broadcaster=telegram_broadcast,
    )
    state.pepe.register_agent("publisher", publisher_agent)

    # 2f. LearningLoop — B4/4.5 (wired prima di AnalyticsAgent che lo usa)
    learning_loop = LearningLoop(memory=state.memory)
    logger.info("LearningLoop istanziato")

    # 2g. BundleStrategy — B4/4.6
    state.bundle_strategy = BundleStrategy(memory=state.memory, learning_loop=learning_loop)
    logger.info("BundleStrategy istanziato")

    # 2h-pre. ShopProfileOptimizer — B5/5.1
    _mock = getattr(state.pepe, "mock_mode", False)
    state.shop_optimizer = ShopProfileOptimizer(
        memory=state.memory,
        etsy_client=state.etsy_api,
        learning_loop=learning_loop,
        mock_mode=_mock,
    )
    logger.info("ShopProfileOptimizer istanziato (mock=%s)", _mock)

    # 2h-pre2. EtsyAdsManager — B5/5.2
    state.etsy_ads_manager = EtsyAdsManager(
        etsy_client=state.etsy_api,
        production_queue=state.production_queue,
        publication_policy=state.publication_policy,
        telegram_broadcaster=telegram_broadcast,
        mock_mode=_mock,
    )
    logger.info("EtsyAdsManager istanziato (mock=%s)", _mock)

    # 2h. Analytics Agent
    analytics_agent = AnalyticsAgent(
        anthropic_client=state.pepe.client,
        memory=state.memory,
        etsy_api=state.etsy_api,
        ws_broadcaster=state.ws_manager.broadcast,
        telegram_broadcaster=telegram_broadcast,
        production_queue=state.production_queue,   # B4/4.2 — Ladder System + polling
        learning_loop=learning_loop,               # B4/4.5 — CTR attribution + score update
    )
    state.pepe.register_agent("analytics", analytics_agent)

    # 2g. Finance Agent (no Etsy dependency)
    finance_agent = FinanceAgent(
        anthropic_client=state.pepe.client,
        memory=state.memory,
        ws_broadcaster=state.ws_manager.broadcast,
        telegram_broadcaster=telegram_broadcast,
    )
    state.pepe.register_agent("finance", finance_agent)

    # 2g-post. FinanceTracker — B5/5.4 review notification
    state.finance_tracker = FinanceTracker(
        memory=state.memory,
        telegram_broadcaster=telegram_broadcast,
    )
    logger.info("FinanceTracker istanziato (B5/5.4)")

    # 2h. RecallAgent — Personal domain, tutto su Ollama
    recall_agent = RecallAgent(
        anthropic_client=state.pepe.client,
        memory=state.memory,
        ws_broadcaster=state.ws_manager.broadcast,
    )
    state.pepe.register_agent("recall", recall_agent)

    # 2h2. RemindAgent — gestione reminder + Notion Calendar (iniettato da lifespan)
    remind_agent = RemindAgent(
        anthropic_client=state.pepe.client,
        memory=state.memory,
        ws_broadcaster=state.ws_manager.broadcast,
        notion_calendar=notion_calendar,
        telegram_broadcaster=telegram_broadcast,
    )
    state.pepe.register_agent("remind", remind_agent)

    # 2h3. SummarizeAgent — riassume URL, file, testo (Haiku + Ollama fallback)
    summarize_agent = SummarizeAgent(
        anthropic_client=state.pepe.client,
        memory=state.memory,
        ws_broadcaster=state.ws_manager.broadcast,
        text_extractor=text_extractor,
        telegram_broadcaster=telegram_broadcast,
    )
    state.pepe.register_agent("summarize", summarize_agent)

    # 2h4. ResearchPersonalAgent — ricerca web DuckDuckGo + sintesi Perplexity-style
    research_personal_agent = ResearchPersonalAgent(
        anthropic_client=state.pepe.client,
        memory=state.memory,
        ws_broadcaster=state.ws_manager.broadcast,
        web_search=web_search,
        telegram_broadcaster=telegram_broadcast,
    )
    state.pepe.register_agent("research_personal", research_personal_agent)

    # 2i. ScreenWatcher
    _screen_watcher_error: str | None = None
    state.screen_watcher = ScreenWatcher(
        memory=state.memory,
        ws_broadcaster=state.ws_manager.broadcast,
    )
    try:
        await state.screen_watcher.start()
        logger.info("ScreenWatcher avviato")
    except Exception as exc:
        logger.warning("ScreenWatcher non avviato: %s", exc)
        _screen_watcher_error = str(exc)
        state.screen_watcher = None

    # ---------------------------------------------------------------------------
    # 2j. Callable per AutopilotLoop — design_pipeline + niche_picker
    # ---------------------------------------------------------------------------

    from apps.backend.core.models import AgentTask as _AgentTask, TaskStatus as _TaskStatus

    async def _autopilot_design_pipeline(item_id: int, niche_data: dict) -> None:
        """Esegue DesignAgent e salva output in production_queue."""
        niche        = niche_data.get("niche", "")
        product_type = niche_data.get("product_type", "digital_print")
        keywords     = niche_data.get("keywords", [])

        design_task = _AgentTask(
            agent_name="design",
            input_data={
                "niche":         niche,
                "product_type":  product_type,
                "keywords":      keywords,
                "color_schemes": niche_data.get("color_schemes", []),
                "source":        "autopilot",
            },
            source="autopilot",
        )
        try:
            result = await state.pepe.dispatch_task(design_task)
        except Exception as exc:
            logger.error("design_pipeline: DesignAgent fallito item=%d: %s", item_id, exc)
            return

        if result.status != _TaskStatus.COMPLETED:
            logger.warning(
                "design_pipeline: DesignAgent non completato item=%d status=%s",
                item_id, result.status,
            )
            return

        out      = result.output_data or {}
        variants = out.get("variants", [])

        # Thumbnail: primo variant con output_path disponibile
        thumbnail_path = ""
        image_url      = ""
        if variants:
            first          = variants[0]
            thumbnail_path = first.get("thumbnail_path") or first.get("output_path") or ""
            image_url      = first.get("image_url") or ""

        # SEO placeholder — titolo leggibile da mostrare nell'approvazione Telegram.
        title = (
            f"{niche.replace('_', ' ').title()} — {product_type.replace('_', ' ').title()}"
        )
        tags  = keywords[:13]

        pricing    = niche_data.get("pricing") or {}
        price: float
        if isinstance(pricing, dict) and pricing.get("price"):
            price = float(pricing["price"])
        else:
            price = float(niche_data.get("price") or 4.99)

        await state.production_queue.set_design_ready(
            item_id       = item_id,
            design_prompt = out.get("cover_title") or out.get("template") or niche,
            image_url     = image_url,
            thumbnail_path= thumbnail_path,
            title         = title,
            description   = "",   # generato da PublisherAgent al publish
            tags          = tags,
            price         = price,
            llm_cost      = result.cost_usd or 0.0,
            image_cost    = float(out.get("image_cost_usd") or 0.0),
        )
        logger.info(
            "design_pipeline: item=%d → pending_approval (niche=%s, thumbnail=%s)",
            item_id, niche, thumbnail_path or "nessuna",
        )

    async def _autopilot_niche_picker() -> dict | None:
        """
        Sceglie la prossima niche con rotazione data-driven. — B4/4.7

        Strategia a cascata:
          1. niche_intelligence — multi-candidate scoring:
               - legge top 10 per performance_score
               - filtra niche "perdenti certificate" (score < 0.3 + confidence=high)
               - evita la niche dell'ultimo listing pubblicato (anti-repetition)
               - final_score = performance_score  (boost implicito: già pesa CTR+conv+rev)
          2. Unexplored candidates (LearningLoop) — niches con score ma 0 listing recenti
          3. ResearchAgent discovery autonoma — solo se non c'è niente nei dati locali
        """
        # Leggi l'ultima niche pubblicata per anti-repetition
        last_niche = ""
        try:
            db_conn    = await state.memory.get_db()
            cursor_rep = await db_conn.execute(
                """
                SELECT niche FROM production_queue
                WHERE status = 'published'
                ORDER BY published_at DESC LIMIT 1
                """
            )
            rep_row   = await cursor_rep.fetchone()
            last_niche = rep_row["niche"] if rep_row else ""
        except Exception as exc:
            logger.debug("niche_picker: lettura last_niche fallita (non bloccante): %s", exc)

        # B5/5.3 — Rileva niches con ladder_level='ctr_low' recenti (< 14 giorni)
        ctr_low_niches: set[str] = set()
        try:
            db_conn = await state.memory.get_db()
            cursor  = await db_conn.execute(
                """
                SELECT DISTINCT pq.niche
                FROM listing_performance lp
                JOIN production_queue pq ON lp.production_queue_id = pq.id
                WHERE lp.ladder_level = 'ctr_low'
                  AND lp.snapshot_at > unixepoch() - 14 * 86400
                """
            )
            ctr_rows     = await cursor.fetchall()
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
            db_conn = await state.memory.get_db()
            cursor  = await db_conn.execute(
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
                niche        = row["niche"]
                product_type = row["product_type"]
                score        = float(row["performance_score"])
                confidence   = row["confidence_level"] or "low"

                # Filtra niche perdenti certificate
                if score < 0.3 and confidence == "high":
                    logger.debug("niche_picker: skip perdente [%s] score=%.3f conf=%s",
                                 niche, score, confidence)
                    continue

                # Penalità leggera alla niche dell'ultimo listing (evita ripetizione)
                if niche == last_niche:
                    score *= 0.7

                # B5/5.3 — Boost niche CTR_LOW: prioritizza regen thumbnail
                regen_thumbnail = False
                if niche in ctr_low_niches:
                    score          *= 1.3
                    regen_thumbnail = True
                    logger.debug("niche_picker: boost CTR_LOW [%s] score→%.3f", niche, score)

                scored.append({
                    "niche":            niche,
                    "product_type":     product_type,
                    "entry_score":      round(score, 3),
                    "keywords":         [],
                    "regen_thumbnail":  regen_thumbnail,
                })

            if scored:
                # Ordina per final_score (dopo eventuali penalità)
                scored.sort(key=lambda x: x["entry_score"], reverse=True)
                winner = scored[0]
                logger.info(
                    "niche_picker: selezionata [%s/%s] score=%.3f",
                    winner["niche"], winner["product_type"], winner["entry_score"],
                )
                return winner

        except Exception as exc:
            logger.warning("niche_picker: lettura niche_intelligence fallita: %s", exc)

        # 2. Unexplored candidates — niches con score ma 0 listing recenti
        try:
            unexplored = await learning_loop.get_unexplored_candidates()
            if unexplored:
                best = unexplored[0]
                logger.info(
                    "niche_picker: unexplored [%s/%s] score=%.3f",
                    best["niche"], best["product_type"], best["performance_score"],
                )
                return {
                    "niche":        best["niche"],
                    "product_type": best["product_type"],
                    "entry_score":  best["performance_score"],
                    "keywords":     [],
                }
        except Exception as exc:
            logger.warning("niche_picker: get_unexplored_candidates fallito: %s", exc)

        # 3. Ultimate fallback: ResearchAgent discovery autonoma (LLM cost)
        logger.info("niche_picker: nessun dato locale — avvio ResearchAgent")
        research_task = _AgentTask(
            agent_name="research",
            input_data={"mode": "autonomous_discovery", "source": "autopilot"},
            source="autopilot",
        )
        try:
            result = await state.pepe.dispatch_task(research_task)
            out    = result.output_data or {}
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

            # Preferisce il campo "winner" (scelto da Sonnet), fallback su niches[0]
            winner = out.get("winner")
            if winner and isinstance(winner, dict) and (winner.get("niche") or winner.get("name")):
                logger.info(
                    "niche_picker: winner='%s' product_type='%s' confidence=%s",
                    winner.get("niche") or winner.get("name"),
                    winner.get("product_type", "printable_pdf"),
                    winner.get("confidence", "?"),
                )
                brief    = winner.get("brief", {}) or {}
                pricing  = brief.get("pricing") or winner.get("pricing") or {}
                keywords = brief.get("keywords") or winner.get("keywords") or []
                return {
                    "niche":        winner.get("niche") or winner.get("name") or "",
                    "product_type": winner.get("product_type", "printable_pdf"),
                    "keywords":     keywords,
                    "entry_score":  float(winner.get("confidence") or 0.5),
                    "pricing":      pricing,
                }

            niches = out.get("niches", [])
            if niches and isinstance(niches[0], dict):
                best = niches[0]
                logger.info(
                    "niche_picker: fallback niches[0]='%s'",
                    best.get("name") or best.get("niche"),
                )
                return {
                    "niche":         best.get("name") or best.get("niche") or "",
                    "product_type":  best.get("recommended_product_type") or best.get("product_type", "printable_pdf"),
                    "keywords":      best.get("keywords", []),
                    "entry_score":   float(best.get("final_score") or best.get("confidence") or 0.5),
                    "pricing":       best.get("pricing", {}),
                }
            logger.warning("niche_picker: ResearchAgent completato ma nessuna niche usabile nell'output")
        except Exception as exc:
            logger.error("niche_picker: ResearchAgent eccezione: %s", exc)

        return None

    async def _autopilot_bundle_checker() -> dict | None:
        """
        Controlla se esiste una niche bundle-ready e ritorna la spec
        come niche_data da passare alla design pipeline. — B4/4.7
        """
        try:
            candidates = await state.bundle_strategy.check_all_niches()
        except Exception as exc:
            logger.warning("bundle_checker: check_all_niches fallito: %s", exc)
            return None

        if not candidates:
            return None

        # Ordina per score e prendi il migliore
        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]
        spec = best["spec"]

        logger.info(
            "bundle_checker: bundle-ready [%s] score=%.3f (%d componenti)",
            spec["niche"], best["score"], spec["n_components"],
        )

        # Formatta come niche_data compatibile con design_pipeline
        return {
            "niche":            spec["niche"],
            "product_type":     "bundle",
            "keywords":         spec.get("keywords", []),
            "entry_score":      spec.get("entry_score", best["score"]),
            "suggested_price":  spec.get("suggested_price"),
            "component_titles": spec.get("component_titles", []),
            "component_images": spec.get("component_images", []),
            "is_bundle":        True,
        }

    # 3. AutopilotLoop — instanziato prima dello Scheduler e del bot
    state.autopilot_loop = AutopilotLoop(
        db               = _db,
        queue            = state.production_queue,
        budget           = state.budget_manager,
        policy           = state.publication_policy,
        bot_send         = telegram_broadcast,
        bot_send_markup  = telegram_broadcast_markup,
        design_pipeline  = _autopilot_design_pipeline,
        niche_picker     = _autopilot_niche_picker,
        bundle_checker   = _autopilot_bundle_checker,    # B4/4.7
    )
    logger.info("AutopilotLoop istanziato")

    # 4. Scheduler APScheduler
    state.scheduler = Scheduler(
        memory=state.memory,
        ws_broadcaster=state.ws_manager.broadcast,
        pepe=state.pepe,
        storage=state.storage,
        research_agent=research_agent,
        design_agent=design_agent,
        publisher_agent=publisher_agent,
        analytics_agent=analytics_agent,
        finance_agent=finance_agent,
        telegram_broadcaster=telegram_broadcast,
        screen_watcher=state.screen_watcher,
        # Blocco 2
        production_queue   = state.production_queue,
        budget_manager     = state.budget_manager,
        publication_policy = state.publication_policy,
        autopilot_loop     = state.autopilot_loop,
        etsy_client        = state.etsy_api,
        # Blocco 5
        shop_optimizer     = state.shop_optimizer,
        etsy_ads_manager   = state.etsy_ads_manager,
        # Blocco 4 / 5.3
        learning_loop      = learning_loop,
    )
    # 5. Bot Telegram (stesso event loop di FastAPI)
    _bot_deps = BotDependencies(
        pepe=state.pepe,
        scheduler=state.scheduler,
        screen_watcher=state.screen_watcher,
        autopilot_loop=state.autopilot_loop,
        production_queue=state.production_queue,
        budget_manager=state.budget_manager,
        publication_policy=state.publication_policy,
        etsy_api=state.etsy_api,
        analytics_agent=analytics_agent,     # B4/4.3 — /ladder command
        learning_loop=learning_loop,         # B4/4.5 — /learn command
        bundle_strategy=state.bundle_strategy,     # B4/4.6 — /bundle command
        shop_optimizer=state.shop_optimizer,        # B5/5.1 — /shopsetup command
        etsy_ads_manager=state.etsy_ads_manager,   # B5/5.2 — auto ads management
        finance_tracker=state.finance_tracker,      # B5/5.4 — review notification
    )
    state.telegram_bot = TelegramBot(_bot_deps)
    await state.telegram_bot.start()

    # 6. AutopilotLoop — ripristina stato precedente invece di partire sempre
    _ap_prev_status = await state.autopilot_loop._get_status()
    if _ap_prev_status == "running":
        await state.autopilot_loop.start()
        logger.info("AutopilotLoop ripreso (stato precedente: running)")
    else:
        # Normalizza a paused_manual così /run sa da dove ripartire
        await state.autopilot_loop._set_status("paused_manual")
        logger.info("AutopilotLoop in attesa di /run (stato precedente: %s)", _ap_prev_status)

    await state.scheduler.start()
    logger.info("Scheduler avviato")

    # Collega notifier Telegram al ScreenWatcher (ora che il bot è attivo)
    if state.screen_watcher is not None:
        state.screen_watcher.set_error_notifier(telegram_broadcast)

    # Notifica startup deferred — inviata solo ora che il bot è attivo
    if _screen_watcher_error:
        await telegram_broadcast(
            f"⚠️ ScreenWatcher non avviato all'avvio del server.\n"
            f"Errore: {_screen_watcher_error}\n\n"
            "Controlla che mss, pyobjc e Vision siano installati. "
            "Il resto del sistema funziona normalmente."
        )

    yield

    # Shutdown (ordine inverso)
    await state.telegram_bot.stop()
    if state.autopilot_loop is not None:
        await state.autopilot_loop.stop()
        logger.info("AutopilotLoop fermato")
    await state.scheduler.stop()
    if state.screen_watcher is not None:
        await state.screen_watcher.stop()
        logger.info("ScreenWatcher fermato")
    if state.etsy_api is not None:
        await state.etsy_api.close()
        logger.info("EtsyAPI chiuso")
    if state.pepe is not None:
        await state.pepe.stop()
        logger.info("Pepe fermato")
    if state.memory is not None:
        await state.memory.close()
        logger.info("MemoryManager chiuso")


# ------------------------------------------------------------------
# App FastAPI
# ------------------------------------------------------------------

app = FastAPI(title="AgentPeXI", version="0.1.0", lifespan=lifespan)
app.state.limiter = state.limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_cors_origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["X-Personal-Key", "Content-Type"],
)

# ------------------------------------------------------------------
# Router includes
# ------------------------------------------------------------------

app.include_router(system.router)
app.include_router(autopilot.router)
app.include_router(screen.router)
app.include_router(personal.router)
app.include_router(wiki.router)
app.include_router(memory_routes.router)
app.include_router(etsy.router)
app.include_router(finance.router)


# ------------------------------------------------------------------
# WebSocket
# ------------------------------------------------------------------


@app.websocket("/ws/voice")
async def ws_voice(websocket: WebSocket) -> None:
    """WebSocket dedicato al canale voce Orb — wake word "Jarvis" via Whisper.

    Protocollo a due fasi:

    Fase 1 — Wake word (Whisper-based keyword spotting):
      Client → Server: binario (blob WebM 3s completo, da MediaRecorder monouso)
      Ogni blob è un WebM auto-contenuto → Whisper trascrive → cerca "jarvis".
      Se trovato:
        Server → Client: {"type": "wake"}

    Fase 2 — Utterance (STT + Pepe + TTS):
      Client → Server: binario (blob WebM completo, max 8s)
      Server → Client: {"type": "response", "text": "...", "audio_b64": "..."|null}
        audio_b64: M4A/AAC base64 (macOS say+afconvert), null se TTS non disponibile
        In assenza di audio_b64 il frontend usa il browser SpeechSynthesis come fallback.

    Dopo la risposta il ciclo riparte dalla Fase 1.
    Canale separato da /ws/chat — non interferisce con gli eventi UI.
    """
    from apps.backend.voice.stt import transcribe
    from apps.backend.voice.tts import play_via_say
    from apps.backend.voice.wake import detect_wake_word_in_text
    from apps.backend.voice import wake_oww
    from apps.backend.voice import collector as voice_collector

    await websocket.accept()
    logger.info("WebSocket /ws/voice connesso")

    phase = "wakeword"              # "wakeword" | "utterance"
    _post_reply_timeout: float | None = None  # secondi, None = nessun timeout

    # Durata della finestra di ascolto post-risposta (Step 6)
    _POST_REPLY_S: float = 20.0

    try:
        while True:
            # ── Receive con timeout opzionale (post-reply window) ────────────
            try:
                if _post_reply_timeout is not None:
                    data = await asyncio.wait_for(
                        websocket.receive_bytes(), timeout=_post_reply_timeout
                    )
                    _post_reply_timeout = None
                else:
                    data = await websocket.receive_bytes()
            except asyncio.TimeoutError:
                # L'utente non ha parlato nella finestra post-reply → torna al wake word
                logger.info("Voice: post-reply window scaduto → ritorno in ascolto wake word")
                await websocket.send_json({"type": "done"})
                phase = "wakeword"
                _post_reply_timeout = None
                continue

            # ── Fase 1: ogni messaggio è un blob WebM completo da 3s ──────────
            if phase == "wakeword":
                try:
                    # ── Raccolta campioni (se attiva) ────────────────────────
                    if voice_collector.is_active():
                        await asyncio.get_running_loop().run_in_executor(
                            None, voice_collector.save_sample, data
                        )

                    # ── Wake word detection ──────────────────────────────────
                    wake_detected = False

                    _use_whisper = True
                    try:
                        oww_score = await wake_oww.predict(data)
                        if oww_score is not None:
                            _use_whisper = False
                            wake_detected = wake_oww.is_wake_word(oww_score)
                        else:
                            logger.warning("wake_oww: predict() → None, uso Whisper (emergenza)")
                    except Exception as oww_exc:
                        logger.warning("wake_oww eccezione (%s) — uso Whisper (emergenza)", oww_exc)

                    if _use_whisper:
                        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
                            f.write(data)
                            tmp_wake = f.name
                        try:
                            wake_text = await transcribe(tmp_wake, language=settings.WHISPER_LANGUAGE, vad_filter=True)
                            if wake_text:
                                logger.info("Wake Whisper fallback: '%s'", wake_text[:80])
                            wake_detected = detect_wake_word_in_text(wake_text)
                        finally:
                            try:
                                os.unlink(tmp_wake)
                            except OSError:
                                pass

                    if wake_detected:
                        import random
                        _WAKE_ACKS = ["Dimmi.", "Sì?", "Ti ascolto.", "Dimmi pure.", "Eccomi."]
                        await play_via_say(random.choice(_WAKE_ACKS))
                        await websocket.send_json({"type": "wake"})
                        # ── Drain handshake ─────────────────────────────────
                        drained = 0
                        while True:
                            raw = await websocket.receive()
                            if raw.get("bytes"):
                                drained += 1
                                logger.debug(
                                    "Drenato blob stale #%d (%d bytes)",
                                    drained, len(raw["bytes"]),
                                )
                            elif raw.get("text"):
                                try:
                                    ctrl = json.loads(raw["text"])
                                    if ctrl.get("type") == "utterance_ready":
                                        logger.debug(
                                            "Frontend pronto per utterance (drenati %d blob stale)",
                                            drained,
                                        )
                                        break
                                except Exception as exc:
                                    logger.debug("ws/voice: JSON parse ctrl msg fallito: %s", exc)
                        phase = "utterance"
                except Exception as exc:
                    logger.warning("Errore wake word detection: %s", exc)

            # ── Fase 2: trascrivi utterance → Pepe → TTS → risposta ──
            elif phase == "utterance":
                with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
                    f.write(data)
                    tmp_utt = f.name
                try:
                    # Utterance: forza lingua italiana per massima accuratezza
                    text = await transcribe(tmp_utt, language=settings.WHISPER_LANGUAGE, vad_filter=True)
                    logger.info("Voice utterance: '%s'", text[:120])

                    if text.strip():
                        import random
                        _THINK_ACKS = [
                            "Vediamo.",
                            "Un attimo.",
                            "Ci penso.",
                            "Dammi un secondo.",
                            "Mmh, vediamo.",
                        ]
                        _ACK_AFTER_S = 1.5   # secondi di attesa prima di suonare l'ack

                        handle_task = asyncio.create_task(
                            state.pepe.handle_user_message(
                                message=text,
                                session_id="voice_orb",
                                source="orb_voice",
                            )
                        )

                        # Aspetta _ACK_AFTER_S — se ancora in corso, suona l'ack
                        _VOICE_TIMEOUT_S = 45.0  # timeout massimo per handle_user_message
                        done, _ = await asyncio.wait({handle_task}, timeout=_ACK_AFTER_S)
                        if not done:
                            # Pepe sta ancora elaborando → ack mentre aspettiamo
                            await play_via_say(random.choice(_THINK_ACKS))

                        # Timeout globale: evita Think perenne se LLM/Ollama non risponde
                        try:
                            reply = await asyncio.wait_for(
                                asyncio.shield(handle_task), timeout=_VOICE_TIMEOUT_S
                            )
                        except asyncio.TimeoutError:
                            handle_task.cancel()
                            reply = "Ci ho messo troppo, riprova."
                            logger.warning("Voice: handle_user_message timeout (%ss)", _VOICE_TIMEOUT_S)
                        logger.info("Voice response → '%s'", reply[:120] if reply else '<VUOTO>')

                        if not reply or not reply.strip():
                            reply = "Scusa, puoi ripetere?"
                            logger.warning("Voice: Pepe ha restituito risposta vuota — fallback attivo")

                        # Controlla se Pepe ha una domanda in sospeso (clarification)
                        is_clarification = await state.pepe.has_pending_voice_clarification()

                        await websocket.send_json({"type": "speaking", "text": reply})
                        await play_via_say(reply)

                        if is_clarification:
                            # Rimane in utterance — manda "clarify" invece di "done"
                            await websocket.send_json({"type": "clarify"})
                            logger.info("Voice: Pepe in attesa di risposta, fase utterance mantenuta")
                            # phase rimane "utterance"
                        else:
                            await websocket.send_json({
                                "type": "post_reply_listen",
                                "timeout_ms": int(_POST_REPLY_S * 1000),
                            })
                            _post_reply_timeout = _POST_REPLY_S
                            logger.info("Voice: post-reply window aperto (%.0fs)", _POST_REPLY_S)
                            # phase rimane "utterance"
                    else:
                        # Nessun testo rilevato — torna in ascolto
                        await websocket.send_json({"type": "done"})
                        phase = "wakeword"

                except Exception as stt_exc:
                    logger.exception("Errore STT/Pepe in /ws/voice: %s", stt_exc)
                    await websocket.send_json({
                        "type": "error",
                        "message": "Errore elaborazione",
                        "detail": str(stt_exc),
                        "agent": "stt/pepe",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })
                    _post_reply_timeout = None
                    phase = "wakeword"
                finally:
                    try:
                        os.unlink(tmp_utt)
                    except OSError:
                        pass

    except WebSocketDisconnect:
        logger.info("WebSocket /ws/voice disconnesso")
    except Exception:
        logger.exception("Errore imprevisto in /ws/voice")


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    """WebSocket unidirezionale: broadcast eventi sistema → client (dashboard).
    Il frontend non invia messaggi — usa solo Telegram per interagire con Pepe.
    """
    await state.ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        state.ws_manager.disconnect(ws)
    except Exception:
        state.ws_manager.disconnect(ws)


# ------------------------------------------------------------------
# Static files (frontend build) — montati per ultimi
# ------------------------------------------------------------------

_frontend_dist = os.path.join(
    os.path.dirname(__file__), "..", "..", "frontend", "dist"
)
if os.path.isdir(_frontend_dist):
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
