"""FastAPI + WebSocket — API principale AgentPeXI."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import logging.handlers
import os
import re
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from apps.backend.core.config import settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentTask

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
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=5,
            encoding="utf-8",
        ),
    ],
)

# Silence noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # niente spam GET 200 OK
logging.getLogger("faster_whisper").setLevel(logging.WARNING)  # niente spam VAD/language detection

logger = logging.getLogger("agentpexi.api")


# ------------------------------------------------------------------
# WebSocket connection manager
# ------------------------------------------------------------------


class ConnectionManager:
    """Gestisce connessioni WebSocket attive e broadcast."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info("WS client connesso (%d totali)", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.remove(ws)
        logger.info("WS client disconnesso (%d rimasti)", len(self._connections))

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Invia evento JSON a tutti i client connessi."""
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                self._connections.remove(ws)
            except ValueError:
                pass


ws_manager = ConnectionManager()

# ------------------------------------------------------------------
# Singleton condivisi (inizializzati nel lifespan)
# ------------------------------------------------------------------

memory: MemoryManager | None = None
pepe = None          # apps.backend.core.pepe.Pepe — assegnato in lifespan
storage = None       # apps.backend.core.storage.StorageManager — assegnato in lifespan
etsy_api = None      # apps.backend.tools.etsy_api.EtsyAPI — assegnato in lifespan
scheduler = None     # apps.backend.core.scheduler.Scheduler — assegnato in lifespan
screen_watcher = None  # apps.backend.screen.watcher.ScreenWatcher — assegnato in lifespan
# Blocco 2 — Autonomy Layer
production_queue   = None   # apps.backend.core.production_queue.ProductionQueueService
budget_manager     = None   # apps.backend.core.budget_manager.BudgetManager
publication_policy = None   # apps.backend.core.publication_policy.PublicationPolicy
autopilot_loop     = None   # apps.backend.core.autopilot_loop.AutopilotLoop


# ------------------------------------------------------------------
# Lifespan
# ------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: MemoryManager, Pepe, workers, Telegram bot. Shutdown: graceful stop."""
    global memory, pepe, storage, etsy_api, scheduler, screen_watcher
    global production_queue, budget_manager, publication_policy, autopilot_loop

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
    from apps.backend.agents.recall import RecallAgent
    from apps.backend.agents.remind import RemindAgent
    from apps.backend.agents.summarize import SummarizeAgent
    from apps.backend.agents.research_personal import ResearchPersonalAgent
    from apps.backend.screen.watcher import ScreenWatcher
    from apps.backend.tools.notion_calendar import NotionCalendar
    from apps.backend.tools.web_search import WebSearchTool
    from apps.backend.tools.text_extract import TextExtractor

    # 1. MemoryManager
    memory = MemoryManager()
    await memory.init()
    # Inietta WS broadcaster per eventi memory_query (neural brain live activation)
    memory.set_ws_broadcaster(ws_manager.broadcast)
    # Inietta KnowledgeBridge per analisi cross-domain fire-and-forget
    from apps.backend.core.knowledge_bridge import KnowledgeBridge
    _bridge = KnowledgeBridge(memory=memory)
    memory.set_bridge_callback(_bridge.on_new_insight)
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
    storage = StorageManager()
    storage.ensure_dirs()
    logger.info("StorageManager inizializzato")

    # 2. Pepe orchestratore
    pepe = Pepe(memory=memory, ws_broadcaster=ws_manager.broadcast)

    # Funzione broadcast Telegram — definita subito dopo Pepe (usata da tutti gli agenti)
    async def telegram_broadcast(msg: str) -> None:
        if pepe and hasattr(pepe, "notify_telegram"):
            await pepe.notify_telegram(msg, priority=True)

    # 2b. Registra agenti disponibili
    research_agent = ResearchAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_manager.broadcast,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("research", research_agent)

    # 2c. Design Agent
    design_agent = DesignAgent(
        anthropic_client=pepe.client,
        memory=memory,
        storage=storage,
        ws_broadcaster=ws_manager.broadcast,
        get_mock_mode=pepe.get_mock_mode,
    )
    pepe.register_agent("design", design_agent)

    await pepe.start()
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
        pepe.wiki = wiki_manager
        logger.info("WikiManager inizializzato — base: %s", _wiki_base)
    except Exception as exc:
        logger.warning("WikiManager non avviato (fail-safe): %s", exc)
        pepe.wiki = None

    # 2d. EtsyAPI
    etsy_api = EtsyAPI(memory=memory, pepe=pepe)
    logger.info("EtsyAPI inizializzato")

    # 2d-b2. Autonomy Layer — Blocco 2
    from apps.backend.core.production_queue import ProductionQueueService
    from apps.backend.core.budget_manager import BudgetManager
    from apps.backend.core.publication_policy import PublicationPolicy
    from apps.backend.core.autopilot_loop import AutopilotLoop

    _db = await memory.get_db()
    production_queue   = ProductionQueueService(_db)
    budget_manager     = BudgetManager(_db)
    publication_policy = PublicationPolicy(_db)
    await budget_manager.ensure_defaults()
    await publication_policy.ensure_defaults()
    logger.info("Autonomy Layer (B2): ProductionQueueService, BudgetManager, PublicationPolicy inizializzati")

    # 2e. Publisher Agent
    publisher_agent = PublisherAgent(
        anthropic_client=pepe.client,
        memory=memory,
        storage=storage,
        etsy_api=etsy_api,
        ws_broadcaster=ws_manager.broadcast,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("publisher", publisher_agent)

    # 2f. LearningLoop — B4/4.5 (wired prima di AnalyticsAgent che lo usa)
    learning_loop = LearningLoop(memory=memory)
    logger.info("LearningLoop istanziato")

    # 2g. BundleStrategy — B4/4.6
    bundle_strategy = BundleStrategy(memory=memory, learning_loop=learning_loop)
    logger.info("BundleStrategy istanziato")

    # 2h. Analytics Agent
    analytics_agent = AnalyticsAgent(
        anthropic_client=pepe.client,
        memory=memory,
        etsy_api=etsy_api,
        ws_broadcaster=ws_manager.broadcast,
        telegram_broadcaster=telegram_broadcast,
        production_queue=production_queue,   # B4/4.2 — Ladder System + polling
        learning_loop=learning_loop,         # B4/4.5 — CTR attribution + score update
    )
    pepe.register_agent("analytics", analytics_agent)

    # 2g. Finance Agent (no Etsy dependency)
    finance_agent = FinanceAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_manager.broadcast,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("finance", finance_agent)

    # 2h. RecallAgent — Personal domain, tutto su Ollama
    recall_agent = RecallAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_manager.broadcast,
    )
    pepe.register_agent("recall", recall_agent)

    # 2h2. RemindAgent — gestione reminder + Notion Calendar (iniettato da lifespan)
    remind_agent = RemindAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_manager.broadcast,
        notion_calendar=notion_calendar,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("remind", remind_agent)

    # 2h3. SummarizeAgent — riassume URL, file, testo (Haiku + Ollama fallback)
    summarize_agent = SummarizeAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_manager.broadcast,
        text_extractor=text_extractor,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("summarize", summarize_agent)

    # 2h4. ResearchPersonalAgent — ricerca web DuckDuckGo + sintesi Perplexity-style
    research_personal_agent = ResearchPersonalAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_manager.broadcast,
        web_search=web_search,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("research_personal", research_personal_agent)

    # 2i. ScreenWatcher — TEMPORANEAMENTE DISABILITATO per debug event loop
    _screen_watcher_error: str | None = None
    screen_watcher = None
    # screen_watcher = ScreenWatcher(
    #     memory=memory,
    #     ws_broadcaster=ws_manager.broadcast,
    # )
    # try:
    #     await screen_watcher.start()
    #     logger.info("ScreenWatcher avviato")
    # except Exception as exc:
    #     logger.warning("ScreenWatcher non avviato: %s", exc)
    #     _screen_watcher_error = str(exc)
    #     screen_watcher = None

    # ---------------------------------------------------------------------------
    # 2j. Callable per AutopilotLoop — design_pipeline + niche_picker
    #
    # design_pipeline: riceve (item_id, niche_data), esegue DesignAgent,
    #   poi chiama production_queue.set_design_ready() per transitare
    #   l'item a pending_approval con thumbnail e dati listing di base.
    #   Il SEO completo (titolo lungo, descrizione) viene generato da
    #   PublisherAgent al momento della pubblicazione effettiva.
    #
    # niche_picker: prova prima niche_intelligence (score calcolato da
    #   LearningLoop — B4/4.5), poi fa fallback su ResearchAgent per
    #   scoperta autonoma di nuove nicchie.
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
        # Il SEO definitivo (ottimizzato per Etsy) viene generato da PublisherAgent
        # nella fase di pubblicazione (publish_checker → etsy_client.publish_listing).
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

        await production_queue.set_design_ready(
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
            db_conn    = await memory.get_db()
            cursor_rep = await db_conn.execute(
                """
                SELECT niche FROM production_queue
                WHERE status = 'published'
                ORDER BY published_at DESC LIMIT 1
                """
            )
            rep_row   = await cursor_rep.fetchone()
            last_niche = rep_row["niche"] if rep_row else ""
        except Exception:
            pass

        # 1. Multi-candidate scoring da niche_intelligence
        try:
            db_conn = await memory.get_db()
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

                scored.append({
                    "niche":        niche,
                    "product_type": product_type,
                    "entry_score":  round(score, 3),
                    "keywords":     [],
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
            result = await pepe.dispatch_task(research_task)
            out    = result.output_data or {}
            niches = out.get("niches", [])
            if niches and isinstance(niches[0], dict):
                best = niches[0]
                return {
                    "niche":         best.get("name") or best.get("niche") or "",
                    "product_type":  best.get("product_type", "digital_print"),
                    "keywords":      best.get("keywords", []),
                    "entry_score":   float(best.get("final_score") or best.get("score") or 0.5),
                    "color_schemes": best.get("color_schemes", []),
                    "pricing":       best.get("pricing", {}),
                }
        except Exception as exc:
            logger.error("niche_picker: ResearchAgent fallito: %s", exc)

        return None

    async def _autopilot_bundle_checker() -> dict | None:
        """
        Controlla se esiste una niche bundle-ready e ritorna la spec
        come niche_data da passare alla design pipeline. — B4/4.7

        Priorità: bundle con score più alto tra quelli pronti.
        Ritorna None se nessuna niche soddisfa i criteri (BundleStrategy.should_create_bundle).
        """
        try:
            candidates = await bundle_strategy.check_all_niches()
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
    #    così bot_send è il telegram_broadcast già definito sopra
    autopilot_loop = AutopilotLoop(
        db               = _db,
        queue            = production_queue,
        budget           = budget_manager,
        policy           = publication_policy,
        bot_send         = telegram_broadcast,
        design_pipeline  = _autopilot_design_pipeline,
        niche_picker     = _autopilot_niche_picker,
        bundle_checker   = _autopilot_bundle_checker,    # B4/4.7
    )
    logger.info("AutopilotLoop istanziato")

    # 4. Scheduler APScheduler
    scheduler = Scheduler(
        memory=memory,
        ws_broadcaster=ws_manager.broadcast,
        pepe=pepe,
        storage=storage,
        research_agent=research_agent,
        design_agent=design_agent,
        publisher_agent=publisher_agent,
        analytics_agent=analytics_agent,
        finance_agent=finance_agent,
        telegram_broadcaster=telegram_broadcast,
        screen_watcher=screen_watcher,
        # Blocco 2
        production_queue   = production_queue,
        budget_manager     = budget_manager,
        publication_policy = publication_policy,
        autopilot_loop     = autopilot_loop,
        etsy_client        = etsy_api,
    )
    # 5. Bot Telegram (stesso event loop di FastAPI)
    _bot_deps = BotDependencies(
        pepe=pepe,
        scheduler=scheduler,
        screen_watcher=screen_watcher,
        autopilot_loop=autopilot_loop,
        production_queue=production_queue,
        budget_manager=budget_manager,
        publication_policy=publication_policy,
        etsy_api=etsy_api,
        analytics_agent=analytics_agent,     # B4/4.3 — /ladder command
        learning_loop=learning_loop,         # B4/4.5 — /learn command
        bundle_strategy=bundle_strategy,     # B4/4.6 — /bundle command
    )
    telegram_bot = TelegramBot(_bot_deps)
    await telegram_bot.start()

    # 6. Avvia AutopilotLoop (dopo il bot, così le notifiche arrivano subito)
    await autopilot_loop.start()
    logger.info("AutopilotLoop avviato")

    await scheduler.start()
    logger.info("Scheduler avviato")

    # Collega notifier Telegram al ScreenWatcher (ora che il bot è attivo)
    if screen_watcher is not None:
        screen_watcher.set_error_notifier(telegram_broadcast)

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
    await telegram_bot.stop()
    if autopilot_loop is not None:
        await autopilot_loop.stop()
        logger.info("AutopilotLoop fermato")
    await scheduler.stop()
    if screen_watcher is not None:
        await screen_watcher.stop()
        logger.info("ScreenWatcher fermato")
    if etsy_api is not None:
        await etsy_api.close()
        logger.info("EtsyAPI chiuso")
    if pepe is not None:
        await pepe.stop()
        logger.info("Pepe fermato")
    if memory is not None:
        await memory.close()
        logger.info("MemoryManager chiuso")


# ------------------------------------------------------------------
# App FastAPI
# ------------------------------------------------------------------

# Rate limiter — IP-based, in-memory
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

app = FastAPI(title="AgentPeXI", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — solo origini esplicitamente configurate
_cors_origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["X-Personal-Key", "Content-Type"],
)


# ------------------------------------------------------------------
# Sicurezza — endpoint /api/personal/* e /api/screen/*
# ------------------------------------------------------------------


async def verify_personal_key(request: Request) -> None:
    """Verifica header X-Personal-Key per endpoint personal e screen.

    Fail-closed: se PERSONAL_API_KEY non è configurata in .env, tutti gli
    endpoint personal/screen restituiscono 403. Impostare la chiave in .env
    per abilitare l'accesso.
    """
    api_key = settings.PERSONAL_API_KEY
    if not api_key:
        raise HTTPException(status_code=403, detail="PERSONAL_API_KEY non configurata")
    key = request.headers.get("X-Personal-Key", "")
    if key != api_key:
        raise HTTPException(status_code=403, detail="Unauthorized")


# Router per tutti gli endpoint che richiedono X-Personal-Key
personal_router = APIRouter(dependencies=[Depends(verify_personal_key)])


# ------------------------------------------------------------------
# REST endpoints
# ------------------------------------------------------------------


@app.get("/api/status")
async def get_status() -> dict:
    """Stato generale del sistema."""
    agent_statuses = pepe.get_agent_statuses() if pepe else {}
    return {
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agents": agent_statuses,
        "queue_size": pepe._queue.qsize() if pepe else 0,
        "connected_clients": len(ws_manager._connections),
        "mock_mode": pepe.mock_mode if pepe else False,
    }


@app.get("/api/mock/status")
async def get_mock_status() -> dict:
    """Stato corrente del mock mode."""
    return {"mock_mode": pepe.mock_mode if pepe else False}


@app.post("/api/run/analytics", dependencies=[Depends(verify_personal_key)])
@limiter.limit("5/minute")
async def run_analytics_now(request: Request) -> dict:
    """Trigger manuale analytics (non aspetta le 08:00)."""
    if not pepe:
        return JSONResponse(status_code=503, content={"error": "Pepe non inizializzato"})
    from apps.backend.core.models import AgentTask
    task = AgentTask(agent_name="analytics", input_data={}, source="api_manual")
    asyncio.create_task(pepe.dispatch_task(task))
    return {"status": "started"}


@app.get("/api/agents")
async def get_agents() -> dict:
    """Stato dettagliato degli agenti registrati."""
    if not pepe:
        return {"agents": {}}
    return {"agents": pepe.get_agent_statuses()}


@app.get("/api/domains/config")
async def get_domains_config() -> dict:
    """Configurazione domini: lista agenti per dominio, dalla source of truth in domains.py."""
    from apps.backend.core.domains import DOMAIN_ETSY, PERSONAL_LAYER
    return {
        "etsy": {
            "name":   DOMAIN_ETSY.name,
            "agents": list(DOMAIN_ETSY.agents.keys()),
        },
        "personal": {
            "name":   "personal",
            "agents": list(PERSONAL_LAYER.agents.keys()) + ["watcher"],
        },
    }



@app.get("/api/listings")
async def get_listings() -> dict:
    """Lista dei listing Etsy dal DB locale."""
    if not memory:
        return {"listings": []}
    listings = await memory.get_etsy_listings(limit=100)
    return {"listings": listings}


@app.get("/api/scheduler")
async def get_scheduler() -> dict:
    """Task schedulati: job APScheduler attivi + task da DB."""
    db_tasks: list[dict] = []
    if memory:
        db_tasks = await memory.get_scheduled_tasks()

    apscheduler_jobs: list[dict] = []
    if scheduler:
        apscheduler_jobs = scheduler.get_jobs()

    return {"tasks": db_tasks, "jobs": apscheduler_jobs}


@app.get("/api/production-queue")
async def get_production_queue(status: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 50) -> dict:
    """Lista items dalla production_queue, filtrabili per status."""
    if not memory:
        return {"items": []}
    filter_status = None if status == "all" else status
    items = await memory.get_production_queue(status=filter_status, limit=limit)
    return {"items": items}


@app.get("/api/tasks/{task_id}/timeline")
async def get_task_timeline(task_id: str) -> dict:
    """Timeline completa step/llm/tool per un task (Task Detail View)."""
    if not memory:
        return {"timeline": []}
    timeline = await memory.get_task_timeline(task_id)
    return {"task_id": task_id, "timeline": timeline}


@app.get("/api/tasks/pending-input")
async def get_pending_input_tasks() -> dict:
    """Lista task in stato INPUT_REQUIRED — sospesi in attesa di risposta utente."""
    if not memory:
        return {"tasks": []}
    try:
        tasks = await memory.get_pending_input_tasks()
        return {"tasks": tasks}
    except Exception:
        logger.exception("pending-input error")
        return JSONResponse(status_code=500, content={"error": "Errore interno"})


@app.get("/api/agents/steps/recent")
async def get_recent_agent_steps(
    limit:      Annotated[int, Query(ge=1, le=500)] = 50,
    agent_name: Annotated[str | None, Query()] = None,
) -> dict:
    """Ultimi N step — opzionale filtro per agent_name.
    Usato per reidratare ReasoningPanel e AgentDetailPanel."""
    if not memory:
        return {"steps": []}
    steps = await memory.get_recent_agent_steps(limit, agent_name=agent_name)
    return {"steps": steps}


@app.get("/api/costs")
async def get_costs(days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict:
    """Cost breakdown per periodo."""
    if not memory:
        return {"breakdown": {}}
    breakdown = await memory.get_cost_breakdown(period_days=days)
    breakdown["budget_threshold_eur"] = settings.COST_ALERT_THRESHOLD_EUR
    breakdown["usd_eur_rate"] = settings.USD_EUR_RATE
    return {"days": days, "breakdown": breakdown}


@app.get("/api/analytics/summary")
async def get_analytics_summary_endpoint(days: Annotated[int, Query(ge=1, le=365)] = 14) -> dict:
    """Aggregati task (agent_logs + production_queue) per il pannello Analytics.

    Ritorna: total/completed/failed/running per periodo, per-day breakdown,
    per-agent stats, production_queue counters.
    Dati reali senza dipendenza da Etsy.
    """
    if not memory:
        return {"summary": {}}
    summary = await memory.get_agent_logs_summary(period_days=days)
    return {"summary": summary}


@personal_router.get("/api/screen/status")
async def get_screen_status() -> dict:
    """Stato corrente del ScreenWatcher — usato per idratazione al WS connect."""
    if screen_watcher is None:
        return {
            "available": False,
            "active": False,
            "paused": False,
            "captures_today": 0,
            "last_capture_time": "",
            "last_capture_app": "",
        }
    st = screen_watcher.get_status()
    return {
        "available": True,
        **st,
    }


# ------------------------------------------------------------------
# Personal endpoints (protetti da personal_router)
# ------------------------------------------------------------------


@personal_router.get("/api/personal/reminders")
async def get_personal_reminders(limit: Annotated[int, Query(ge=1, le=100)] = 10) -> dict:
    """Prossimi reminder pending ordinati per trigger_at.

    Restituisce `items` con shape attesa dal PersonalPanel:
    {id, message, when (ISO8601), status}
    """
    if not memory:
        return {"items": []}
    raw = await memory.get_pending_reminders() or []
    items = [
        {
            "id":      r.get("id"),
            "message": r.get("text", ""),
            "when":    r.get("trigger_at", ""),
            "status":  r.get("status", "pending"),
        }
        for r in raw[:limit]
    ]
    return {"items": items}


@personal_router.get("/api/personal/recalls")
async def get_personal_recalls(limit: Annotated[int, Query(ge=1, le=100)] = 10) -> dict:
    """Ultimi N recall completati.

    Restituisce `items` con shape attesa dal PersonalPanel:
    {timestamp, agent, query, status}
    """
    if not memory:
        return {"items": []}
    raw = await memory.get_personal_recalls(limit) or []
    items = [
        {
            "timestamp": r.get("created_at") or r.get("timestamp", ""),
            "agent":     r.get("agent", "recall"),
            "query":     r.get("query") or r.get("text", ""),
            "status":    "ok" if r.get("status") != "failed" else "error",
        }
        for r in raw
    ]
    return {"items": items}


@personal_router.get("/api/personal/mcp/status")
async def get_mcp_status() -> dict:
    """Stato connessioni MCP: Notion, Gmail, Calendar.
    Notion: ping leggero all'API se token configurato.
    Gmail/Calendar: verifica presenza token OAuth (agenti non ancora implementati).
    """
    import aiohttp

    result: dict[str, str] = {}

    # Notion
    notion_token = getattr(settings, "NOTION_API_TOKEN", "")
    if not notion_token:
        result["notion"] = "not_configured"
    else:
        try:
            timeout = aiohttp.ClientTimeout(total=4)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    "https://api.notion.com/v1/users/me",
                    headers={
                        "Authorization": f"Bearer {notion_token}",
                        "Notion-Version": "2022-06-28",
                    },
                ) as resp:
                    result["notion"] = "ok" if resp.status == 200 else f"error_{resp.status}"
        except Exception:
            result["notion"] = "error"

    # Gmail / Calendar — stesso OAuth; verifica presenza token
    google_token = getattr(settings, "GOOGLE_REFRESH_TOKEN", "")
    if not google_token:
        result["gmail"] = "not_configured"
        result["calendar"] = "not_configured"
    else:
        # Token presente — agenti non ancora implementati, stato "configured"
        result["gmail"] = "configured"
        result["calendar"] = "configured"

    return result


@personal_router.get("/api/personal/stats")
async def get_personal_stats(days: Annotated[int, Query(ge=1, le=365)] = 14) -> dict:
    """Aggregati agenti Personal: task completati/falliti per agente, ultimi N giorni."""
    if not memory:
        return {"stats": {}}
    stats = await memory.get_domain_agent_stats(domain="personal", days=days)
    return {"stats": stats, "days": days}


@personal_router.get("/api/ollama/status")
async def get_ollama_status() -> dict:
    """Stato Ollama: modello caricato, latenza ultima chiamata, keep_alive."""
    import time
    import aiohttp
    from urllib.parse import urlparse

    parsed = urlparse(settings.OLLAMA_BASE_URL)
    ollama_base = f"{parsed.scheme}://{parsed.netloc}"  # es. http://localhost:11434

    result = {
        "model": settings.OLLAMA_MODEL,
        "loaded": False,
        "latency_ms": None,
        "keep_alive": getattr(settings, "OLLAMA_KEEP_ALIVE", "-1"),
    }

    try:
        timeout = aiohttp.ClientTimeout(total=4)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            t0 = time.monotonic()
            async with session.get(f"{ollama_base}/api/ps") as resp:
                latency = int((time.monotonic() - t0) * 1000)
                result["latency_ms"] = latency
                if resp.status == 200:
                    data = await resp.json()
                    running = [m.get("name", "") for m in data.get("models", [])]
                    result["loaded"] = any(
                        settings.OLLAMA_MODEL in m for m in running
                    )
    except Exception:
        pass

    return result


@personal_router.post("/api/personal/voice/collect")
async def set_collect_mode(body: dict) -> dict:
    """Attiva/disattiva modalità raccolta campioni wake word.

    Body: {"mode": "positive" | "negative" | "off"}
    - positive: salva ogni blob WebM in training_data/positive/real_*.wav
    - negative: salva ogni blob WebM in training_data/negative/real_*.wav
    - off: disattiva la raccolta

    Dopo aver raccolto abbastanza campioni (>=20 per classe):
      python scripts/train_wake_word.py
    """
    from apps.backend.voice import collector
    mode = (body or {}).get("mode", "off")
    if mode not in ("positive", "negative", "off"):
        return JSONResponse(status_code=400, content={"error": "mode deve essere positive | negative | off"})
    collector.set_mode(mode)
    return collector.get_status()


@personal_router.get("/api/personal/voice/collect/status")
async def get_collect_status() -> dict:
    """Stato corrente raccolta campioni: modalità attiva + conteggi per classe."""
    from apps.backend.voice import collector
    return collector.get_status()


@personal_router.post("/api/personal/ask")
@limiter.limit("30/minute")
async def personal_ask(request: Request, body: dict) -> dict:
    """Endpoint voce: riceve testo trascritto, risponde via Pepe in dominio Personal.
    Usato dal PepeOrb nel frontend — nessuna pipeline, risposta diretta.
    """
    if not pepe:
        return JSONResponse(status_code=503, content={"error": "Pepe non inizializzato"})
    text = (body or {}).get("text", "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"error": "Campo 'text' mancante o vuoto"})
    response = await pepe.handle_user_message(
        text,
        source="dashboard_voice",
        session_id="dashboard",
    )
    return {"response": response}


# ------------------------------------------------------------------
# Wiki endpoints — Step 5.2.6 (read-only, no auth)
# ------------------------------------------------------------------


def _get_wiki_llms():
    """Ritorna (llm_etsy, llm_personal) da pepe, oppure (None, None) se non disponibile."""
    if not pepe:
        return None, None
    return getattr(pepe, "client", None), getattr(pepe, "_local_client", None)


@app.get("/api/wiki/stats")
async def get_wiki_stats() -> dict:
    """Statistiche wiki: file per dominio, raw pending, nicchie Etsy."""
    if not pepe or not getattr(pepe, "wiki", None):
        return JSONResponse(status_code=503, content={"error": "WikiManager non inizializzato"})
    try:
        stats = await pepe.wiki.get_stats()
        return stats
    except Exception as exc:
        logger.exception("wiki stats error")
        return JSONResponse(status_code=500, content={"error": "Errore interno"})


@app.get("/api/wiki/query")
async def wiki_query(domain: str = "etsy", q: str = "") -> dict:
    """Query tiered sulla wiki (Pass 1 frontmatter, Pass 2 body se necessario).

    Params: domain=etsy|personal, q=testo della query.
    """
    if not pepe or not getattr(pepe, "wiki", None):
        return JSONResponse(status_code=503, content={"error": "WikiManager non inizializzato"})
    if not q:
        return JSONResponse(status_code=400, content={"error": "Parametro 'q' obbligatorio"})
    llm_etsy, llm_personal = _get_wiki_llms()
    llm = llm_personal if domain == "personal" else llm_etsy
    if not llm:
        return JSONResponse(status_code=503, content={"error": "LLM non disponibile"})
    try:
        result = await pepe.wiki.query(domain, q, llm)
        return {"domain": domain, "query": q, "result": result}
    except Exception as exc:
        logger.exception("wiki query error")
        return JSONResponse(status_code=500, content={"error": "Errore interno"})


_NICHE_SAFE_RE = re.compile(r'^[A-Za-z0-9 _\-]{1,80}$')


@app.get("/api/wiki/niche/{niche}")
async def get_wiki_niche(niche: str) -> dict:
    """Contesto wiki per una nicchia Etsy specifica (lettura diretta, no LLM)."""
    if not _NICHE_SAFE_RE.match(niche):
        return JSONResponse(status_code=400, content={"error": "Parametro 'niche' non valido"})
    if not pepe or not getattr(pepe, "wiki", None):
        return JSONResponse(status_code=503, content={"error": "WikiManager non inizializzato"})
    try:
        content = await pepe.wiki.get_niche_context(niche)
        if content is None:
            return JSONResponse(status_code=404, content={"error": "Niche non trovata"})
        return {"niche": niche, "content": content}
    except Exception as exc:
        logger.exception("wiki niche error")
        return JSONResponse(status_code=500, content={"error": "Errore interno"})


@app.post("/api/wiki/lint", dependencies=[Depends(verify_personal_key)])
async def wiki_lint(body: dict | None = None) -> dict:
    """Lint wiki: wikilinks rotti + raw pending + suggerimenti.

    Body: {domain: 'etsy'|'personal'} (default: etsy).
    """
    if not pepe or not getattr(pepe, "wiki", None):
        return JSONResponse(status_code=503, content={"error": "WikiManager non inizializzato"})
    domain = (body or {}).get("domain", "etsy")
    llm_etsy, llm_personal = _get_wiki_llms()
    llm = llm_personal if domain == "personal" else llm_etsy
    if not llm:
        return JSONResponse(status_code=503, content={"error": "LLM non disponibile"})
    try:
        report = await pepe.wiki.lint(domain, llm)
        return {"domain": domain, "report": report}
    except Exception as exc:
        logger.exception("wiki lint error")
        return JSONResponse(status_code=500, content={"error": "Errore interno"})


# ------------------------------------------------------------------
# Domain switch endpoint (non protetto — controllo UI locale)
# ------------------------------------------------------------------


@app.post("/api/domain", dependencies=[Depends(verify_personal_key)])
async def switch_domain(body: dict) -> dict:
    """Cambia dominio attivo. Body: {domain: 'etsy'|'personal'}."""
    if not pepe:
        return JSONResponse(status_code=503, content={"error": "Pepe non inizializzato"})
    from apps.backend.core.domains import DOMAIN_ETSY
    domain_name = (body or {}).get("domain", "")
    if domain_name == "personal":
        pepe.set_active_domain(None)
    elif domain_name == "etsy":
        pepe.set_active_domain(DOMAIN_ETSY)
    else:
        return JSONResponse(status_code=400, content={"error": f"Dominio sconosciuto: {domain_name}"})
    await ws_manager.broadcast({"type": "domain_switched", "domain": domain_name})
    return {"domain": domain_name}


@app.get("/api/memory/stats")
async def get_memory_stats() -> dict:
    """Statistiche ChromaDB: collection count, disponibilità."""
    if not memory:
        return {"chroma": {"available": False, "count": 0}}
    chroma = await memory.get_chroma_stats()
    return {"chroma": chroma}


@app.get("/api/memory/graph")
async def get_memory_graph(
    threshold: float = Query(default=0.72, ge=0.0, le=1.0),
) -> dict:
    """Grafo semantico della memoria: nodi dai documenti ChromaDB, archi da similarità coseno.

    Restituisce:
        {
          nodes: [{id, label, collection, zone, metadata}],
          edges: [{source, target, weight}],
        }

    Params:
        threshold: soglia coseno minima per creare un arco (default 0.72).

    Le quattro collection sono fetched in parallelo:
        pepe_memory     → zone "etsy"
        screen_memory   → zone "memory" (OCR watcher)
        personal_memory → zone "personal" (structured insights Personal)
        shared_memory   → zone "shared"  (bridge cross-domain)
    """
    import numpy as np

    if not memory:
        return JSONResponse(status_code=503, content={"error": "MemoryManager non disponibile"})

    async def _fetch_collection(collection) -> tuple[list[str], list[str], list[dict], list[list[float]]]:
        """Fetch (ids, documents, metadatas, embeddings) da una collection ChromaDB."""
        if collection is None:
            return [], [], [], []
        try:
            result = await asyncio.to_thread(
                collection.get,
                include=["documents", "metadatas", "embeddings"],
            )
            ids = result.get("ids") or []
            docs = result.get("documents") or []
            metas = result.get("metadatas") or []
            embeds = result.get("embeddings") or []
            return ids, docs, metas, embeds
        except Exception as exc:
            logger.warning("memory graph fetch fallito: %s", exc)
            return [], [], [], []

    # Fetch parallelo tutte e quattro le collection
    (
        (etsy_ids,     etsy_docs,     etsy_metas,     etsy_embeds),
        (screen_ids,   screen_docs,   screen_metas,   screen_embeds),
        (personal_ids, personal_docs, personal_metas, personal_embeds),
        (shared_ids,   shared_docs,   shared_metas,   shared_embeds),
    ) = await asyncio.gather(
        _fetch_collection(memory._chroma_collection),
        _fetch_collection(memory._screen_memory_collection),
        _fetch_collection(memory._personal_memory_collection),
        _fetch_collection(memory._shared_memory_collection),
    )

    # Helper: aggiungi una lista di nodi alla struttura unificata
    nodes: list[dict] = []
    all_ids: list[str] = []
    all_embeds: list[list[float]] = []

    def _add_nodes(
        ids: list[str],
        docs: list[str],
        metas: list[dict],
        embeds: list,
        collection_name: str,
        default_zone: str,
    ) -> None:
        for i, doc_id in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            label = meta.get("title") or meta.get("type") or meta.get("tag") or doc_id[:40]
            # screen_memory: zone determinata dall'app_name
            if collection_name == "screen_memory":
                app_name = meta.get("app", "")
                zone = "personal" if any(
                    k in app_name.lower() for k in ("code", "terminal", "vim", "vscode")
                ) else "memory"
            else:
                zone = default_zone
            nodes.append({
                "id":         doc_id,
                "label":      label,
                "collection": collection_name,
                "zone":       zone,
                "document":   (docs[i] if i < len(docs) else "")[:300],
                "metadata":   meta,
            })
            all_ids.append(doc_id)
            all_embeds.append(embeds[i] if i < len(embeds) and embeds[i] else None)

    _add_nodes(etsy_ids,     etsy_docs,     etsy_metas,     etsy_embeds,     "pepe_memory",     "etsy")
    _add_nodes(screen_ids,   screen_docs,   screen_metas,   screen_embeds,   "screen_memory",   "memory")
    _add_nodes(personal_ids, personal_docs, personal_metas, personal_embeds, "personal_memory", "personal")
    _add_nodes(shared_ids,   shared_docs,   shared_metas,   shared_embeds,   "shared_memory",   "shared")

    # Calcola archi tramite similarità coseno (solo nodi con embedding)
    edges: list[dict] = []
    valid_idx = [i for i, e in enumerate(all_embeds) if e is not None]

    if len(valid_idx) >= 2:
        try:
            matrix = np.array([all_embeds[i] for i in valid_idx], dtype=np.float32)
            # Normalizza righe per ottenere vettori unitari
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            matrix = matrix / norms
            # Similarità coseno = prodotto scalare di vettori normalizzati
            sim_matrix = matrix @ matrix.T

            n = len(valid_idx)
            for a in range(n):
                for b in range(a + 1, n):
                    sim = float(sim_matrix[a, b])
                    if sim >= threshold:
                        edges.append({
                            "source": all_ids[valid_idx[a]],
                            "target": all_ids[valid_idx[b]],
                            "weight": round(sim, 4),
                        })
        except Exception as exc:
            logger.warning("Calcolo similarità coseno fallito: %s", exc)

    # Arricchisci nodi con connection_count per dimensionare i nodi nel grafico
    conn_count: dict[str, int] = {}
    for edge in edges:
        conn_count[edge["source"]] = conn_count.get(edge["source"], 0) + 1
        conn_count[edge["target"]] = conn_count.get(edge["target"], 0) + 1
    for node in nodes:
        node["connections"] = conn_count.get(node["id"], 0)

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "threshold":       threshold,
            "total_nodes":     len(nodes),
            "total_edges":     len(edges),
            "etsy_count":      len(etsy_ids),
            "screen_count":    len(screen_ids),
            "personal_count":  len(personal_ids),
            "shared_count":    len(shared_ids),
        },
    }


@app.get("/api/memory/node/{doc_id:path}")
async def get_memory_node(
    doc_id: str,
    collection: str = Query(default="pepe_memory"),
) -> dict:
    """Dettaglio di un singolo nodo memoria: documento completo + metadati + storia accessi.

    Params:
        doc_id:     ID documento ChromaDB.
        collection: 'pepe_memory' | 'screen_memory' | 'personal_memory' | 'shared_memory'.

    Restituisce:
        {id, document, metadata, collection, access_history: [{agent, query_text, queried_at}]}
    """
    if not memory:
        return JSONResponse(status_code=503, content={"error": "MemoryManager non disponibile"})

    # Fetch documento dalla collection corretta
    _col_map = {
        "pepe_memory":     memory._chroma_collection,
        "screen_memory":   memory._screen_memory_collection,
        "personal_memory": memory._personal_memory_collection,
        "shared_memory":   memory._shared_memory_collection,
    }
    chroma_col = _col_map.get(collection)
    if chroma_col is None:
        return JSONResponse(status_code=503, content={"error": f"Collection '{collection}' non disponibile"})

    try:
        result = await asyncio.to_thread(
            chroma_col.get,
            ids=[doc_id],
            include=["documents", "metadatas"],
        )
        ids = result.get("ids") or []
        if not ids or ids[0] != doc_id:
            return JSONResponse(status_code=404, content={"error": f"Nodo '{doc_id}' non trovato"})

        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        document = docs[0] if docs else ""
        metadata = metas[0] if metas else {}
    except Exception as exc:
        logger.exception("Errore fetch nodo %s: %s", doc_id, exc)
        return JSONResponse(status_code=500, content={"error": "Errore interno fetch documento"})

    # Storico accessi da SQLite
    access_history = await memory.get_node_access_history(doc_id, collection, limit=20)

    return {
        "id": doc_id,
        "document": document,
        "metadata": metadata,
        "collection": collection,
        "access_history": access_history,
    }



# ------------------------------------------------------------------
# Etsy endpoints
# ------------------------------------------------------------------


@app.post("/api/etsy/auth/status")
async def etsy_auth_status() -> dict:
    """Verifica se i token Etsy sono validi."""
    if not etsy_api:
        return JSONResponse(status_code=503, content={"error": "EtsyAPI non inizializzato"})
    return await etsy_api.check_auth_status()


@app.get("/api/etsy/shop")
async def etsy_shop_info() -> dict:
    """Info shop Etsy (test connessione)."""
    if not etsy_api:
        return JSONResponse(status_code=503, content={"error": "EtsyAPI non inizializzato"})
    try:
        shop = await etsy_api.get_shop()
        return {"shop": shop}
    except RuntimeError as exc:
        logger.warning("etsy shop auth error: %s", exc)
        return JSONResponse(status_code=401, content={"error": "Token Etsy non valido o scaduto"})
    except Exception as exc:
        logger.exception("etsy shop error")
        return JSONResponse(status_code=502, content={"error": "Errore comunicazione Etsy"})


@app.get("/api/etsy/listings")
async def get_etsy_listings(status: str = "all", limit: Annotated[int, Query(ge=1, le=500)] = 50) -> dict:
    """Lista listing Etsy con filtro status (draft|active|all)."""
    if not memory:
        return {"listings": []}
    filter_status = None if status == "all" else status
    listings = await memory.get_etsy_listings(status=filter_status, limit=limit)
    return {"listings": listings}


# ------------------------------------------------------------------
# Analytics endpoints
# ------------------------------------------------------------------


@app.get("/api/finance/report")
async def get_finance_report(days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict:
    """Ultimo report finance da ChromaDB + trigger run se mai eseguito."""
    if not memory:
        return {"report": None}
    results = await memory.query_chromadb(
        query="finance report revenue cost margin ROI",
        n_results=1,
        where={"type": "finance_report"},
    )
    return {"report": results[0] if results else None, "days": days}


@app.post("/api/finance/run", dependencies=[Depends(verify_personal_key)])
@limiter.limit("5/minute")
async def run_finance_agent(request: Request, body: dict | None = None) -> dict:
    """Esegue il FinanceAgent manualmente (period_days dal body, default 30)."""
    if not pepe:
        return JSONResponse(status_code=503, content={"error": "Pepe non inizializzato"})
    period_days = max(1, min(int((body or {}).get("period_days", 30)), 365))
    import uuid
    task_id = str(uuid.uuid4())
    task = AgentTask(
        task_id=task_id,
        agent_name="finance",
        input_data={"period_days": period_days},
        source="web",
    )
    await pepe.dispatch_task(task)
    return {"status": "dispatched", "task_id": task_id, "period_days": period_days}


@app.get("/api/analytics/latest")
async def get_analytics_latest() -> dict:
    """Ultimo report analytics da ChromaDB."""
    if not memory:
        return {"report": None}
    results = await memory.query_chromadb(
        query="daily analytics report",
        n_results=1,
        where={"type": "analytics_report"},
    )
    return {"report": results[0] if results else None}


@app.get("/api/analytics/failures")
async def get_analytics_failures(limit: Annotated[int, Query(ge=1, le=500)] = 20) -> dict:
    """Ultime failure analysis dai listing."""
    if not memory:
        return {"failures": []}
    failures = await memory.get_all_listing_analyses(limit=limit)
    return {"failures": failures}


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
            # Il frontend avvia un nuovo MediaRecorder per ogni finestra da 3s
            # → ogni blob ha l'header EBML e può essere decodificato da Whisper.
            if phase == "wakeword":
                try:
                    # ── Raccolta campioni (se attiva) ────────────────────────
                    # Salva il blob PRIMA del classifier — non blocca il flusso normale.
                    if voice_collector.is_active():
                        await asyncio.get_running_loop().run_in_executor(
                            None, voice_collector.save_sample, data
                        )

                    # ── Wake word detection ──────────────────────────────────
                    # Prova modello ML custom; se non disponibile o errore → Whisper.
                    wake_detected = False

                    _use_whisper = True
                    try:
                        oww_score = await wake_oww.predict(data)
                        if oww_score is not None:
                            # Modello ML attivo — Whisper NON viene usato
                            _use_whisper = False
                            wake_detected = wake_oww.is_wake_word(oww_score)
                        else:
                            # predict() ha ritornato None: ffmpeg fallito o altro errore
                            # già loggato in wake_oww con WARNING
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
                        # ── Wake ack via ElevenLabs ──────────────────────────
                        # Riproduce l'ack PRIMA di mandare {"type": "wake"} al
                        # frontend. Così quando il frontend riceve "wake" e manda
                        # subito "utterance_ready", il backend è già nel drain loop.
                        # L'ack è bloccante → zero echo sul microfono.
                        import random
                        _WAKE_ACKS = ["Dimmi.", "Sì?", "Ti ascolto.", "Dimmi pure.", "Eccomi."]
                        await play_via_say(random.choice(_WAKE_ACKS))
                        # Notifica frontend solo dopo che l'ack è terminato
                        await websocket.send_json({"type": "wake"})
                        # ── Drain handshake ─────────────────────────────────
                        # Race condition: il frontend può aver già inviato il
                        # blob successivo del loop wake PRIMA di ricevere il
                        # messaggio "wake" e fermarsi. Dreniamo quei blob stale
                        # finché il frontend non manda {"type": "utterance_ready"}.
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
                                except Exception:
                                    pass
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
                        # ── Step 5: Thinking ack (condizionale) ──────────────
                        # Avvia handle_user_message in background. Se non risponde
                        # entro _ACK_AFTER_S secondi, suona la frase di attesa.
                        # Per agenti veloci (remind ~1s) l'ack non parte mai.
                        # Per agenti lenti (research, finance) riempie il silenzio.
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
                            pepe.handle_user_message(
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
                        # In quel caso rimaniamo in fase "utterance" — il mic si riapre
                        # subito dopo la risposta, senza tornare al wake word.
                        is_clarification = await pepe.has_pending_voice_clarification()

                        await websocket.send_json({"type": "speaking", "text": reply})
                        await play_via_say(reply)

                        if is_clarification:
                            # Rimane in utterance — manda "clarify" invece di "done"
                            await websocket.send_json({"type": "clarify"})
                            logger.info("Voice: Pepe in attesa di risposta, fase utterance mantenuta")
                            # phase rimane "utterance", nessun timeout
                        else:
                            # ── Step 6: post-reply listen window ─────────────
                            # Apre una finestra di 8s senza wake word: l'utente può
                            # rispondere direttamente a Pepe. Se non parla entro il
                            # timeout il loop torna in ascolto wake word.
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
                        "detail": str(stt_exc),          # full detail → green card (Step 3)
                        "agent": "stt/pepe",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })
                    # In caso di errore: azzera il post-reply window e torna al wake word
                    _post_reply_timeout = None
                    phase = "wakeword"
                finally:
                    try:
                        os.unlink(tmp_utt)
                    except OSError:
                        pass
                # NOTA: NON c'è più un "phase = 'wakeword'" incondizionale qui.
                # La fase viene gestita esplicitamente nei branch above (clarify /
                # post_reply_listen / silent utterance / exception).

    except WebSocketDisconnect:
        logger.info("WebSocket /ws/voice disconnesso")
    except Exception:
        logger.exception("Errore imprevisto in /ws/voice")


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    """WebSocket unidirezionale: broadcast eventi sistema → client (dashboard).
    Il frontend non invia messaggi — usa solo Telegram per interagire con Pepe.
    """
    await ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)


app.include_router(personal_router)

# ------------------------------------------------------------------
# Static files (frontend build) — montati per ultimi
# ------------------------------------------------------------------

import os

_frontend_dist = os.path.join(
    os.path.dirname(__file__), "..", "..", "frontend", "dist"
)
if os.path.isdir(_frontend_dist):
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
