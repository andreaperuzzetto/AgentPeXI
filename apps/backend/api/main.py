"""FastAPI + WebSocket — API principale AgentPeXI."""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

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


# ------------------------------------------------------------------
# Lifespan
# ------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: MemoryManager, Pepe, workers, Telegram bot. Shutdown: graceful stop."""
    global memory, pepe, storage, etsy_api, scheduler, screen_watcher

    from apps.backend.core.pepe import Pepe
    from apps.backend.core.scheduler import Scheduler
    from apps.backend.core.storage import StorageManager
    from apps.backend.telegram.bot import TelegramBot
    from apps.backend.tools.etsy_api import EtsyAPI
    from apps.backend.agents.research import ResearchAgent
    from apps.backend.agents.design import DesignAgent
    from apps.backend.agents.publisher import PublisherAgent
    from apps.backend.agents.analytics import AnalyticsAgent
    from apps.backend.agents.finance import FinanceAgent
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
    logger.info("MemoryManager inizializzato")

    # 1c. Tools condivisi — istanziati una volta sola (DI negli agenti Personal)
    notion_calendar = NotionCalendar(token=getattr(settings, "NOTION_API_TOKEN", ""))
    try:
        await notion_calendar.ensure_database()
        logger.info("Notion Calendar database pronto")
    except Exception as exc:
        logger.warning("notion_calendar.ensure_database fallito (fail-safe): %s", exc)
    web_search = WebSearchTool()
    text_extractor = TextExtractor(max_chars=getattr(settings, "SUMMARIZE_MAX_CHARS", 20_000))

    # 1b. StorageManager (singleton)
    storage = StorageManager()
    storage.ensure_dirs()
    logger.info("StorageManager inizializzato")

    # 2. Pepe orchestratore
    pepe = Pepe(memory=memory, ws_broadcaster=ws_manager.broadcast)

    # 2b. Registra agenti disponibili
    research_agent = ResearchAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_manager.broadcast,
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

    # 2d. EtsyAPI
    etsy_api = EtsyAPI(memory=memory, pepe=pepe)
    logger.info("EtsyAPI inizializzato")

    # Funzione broadcast Telegram (usata dallo scheduler)
    async def telegram_broadcast(msg: str) -> None:
        if pepe and hasattr(pepe, "notify_telegram"):
            await pepe.notify_telegram(msg, priority=True)

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

    # 2f. Analytics Agent
    analytics_agent = AnalyticsAgent(
        anthropic_client=pepe.client,
        memory=memory,
        etsy_api=etsy_api,
        ws_broadcaster=ws_manager.broadcast,
        telegram_broadcaster=telegram_broadcast,
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
    )
    pepe.register_agent("remind", remind_agent)

    # 2h3. SummarizeAgent — riassume URL, file, testo (Haiku + Ollama fallback)
    summarize_agent = SummarizeAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_manager.broadcast,
        text_extractor=text_extractor,
    )
    pepe.register_agent("summarize", summarize_agent)

    # 2h4. ResearchPersonalAgent — ricerca web DuckDuckGo + sintesi Perplexity-style
    research_personal_agent = ResearchPersonalAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_manager.broadcast,
        web_search=web_search,
    )
    pepe.register_agent("research_personal", research_personal_agent)

    # 2i. ScreenWatcher — avviato solo se pyobjc/mss disponibili
    _screen_watcher_error: str | None = None
    screen_watcher = ScreenWatcher(
        memory=memory,
        ws_broadcaster=ws_manager.broadcast,
    )
    try:
        await screen_watcher.start()
        logger.info("ScreenWatcher avviato")
    except Exception as exc:
        logger.warning("ScreenWatcher non avviato: %s", exc)
        _screen_watcher_error = str(exc)
        screen_watcher = None

    # 3. Scheduler APScheduler
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
    )
    await scheduler.start()
    logger.info("Scheduler avviato")

    # 4. Bot Telegram (stesso event loop di FastAPI)
    telegram_bot = TelegramBot(pepe=pepe, scheduler=scheduler, screen_watcher=screen_watcher)
    await telegram_bot.start()

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

app = FastAPI(title="AgentPeXI", version="0.1.0", lifespan=lifespan)


# ------------------------------------------------------------------
# REST endpoints
# ------------------------------------------------------------------


@app.get("/api/status")
async def get_status() -> dict:
    """Stato generale del sistema."""
    agent_statuses = pepe.get_agent_statuses() if pepe else {}
    return {
        "status": "running",
        "timestamp": datetime.utcnow().isoformat(),
        "agents": agent_statuses,
        "queue_size": pepe._queue.qsize() if pepe else 0,
        "connected_clients": len(ws_manager._connections),
        "mock_mode": pepe.mock_mode if pepe else False,
    }


@app.get("/api/mock/status")
async def get_mock_status() -> dict:
    """Stato corrente del mock mode."""
    return {"mock_mode": pepe.mock_mode if pepe else False}


@app.post("/api/run/analytics")
async def run_analytics_now() -> dict:
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



@app.get("/api/listings")
async def get_listings() -> dict:
    """Lista dei listing Etsy dal DB locale."""
    if not memory:
        return {"listings": []}
    cursor = await memory._db.execute(
        "SELECT * FROM etsy_listings ORDER BY created_at DESC LIMIT 100"
    )
    rows = await cursor.fetchall()
    return {"listings": [dict(r) for r in rows]}


@app.get("/api/scheduler")
async def get_scheduler() -> dict:
    """Task schedulati: job APScheduler attivi + task da DB."""
    db_tasks: list[dict] = []
    if memory:
        cursor = await memory._db.execute(
            "SELECT * FROM scheduled_tasks ORDER BY next_run"
        )
        rows = await cursor.fetchall()
        db_tasks = [dict(r) for r in rows]

    apscheduler_jobs: list[dict] = []
    if scheduler:
        apscheduler_jobs = scheduler.get_jobs()

    return {"tasks": db_tasks, "jobs": apscheduler_jobs}


@app.get("/api/production-queue")
async def get_production_queue(status: str | None = None, limit: int = 50) -> dict:
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


@app.get("/api/agents/steps/recent")
async def get_recent_agent_steps(limit: int = 50) -> dict:
    """Ultimi N step per agente — usato per reidratare il ReasoningPanel al refresh."""
    if not memory:
        return {"steps": []}
    cursor = await memory._db.execute(
        """SELECT id, task_id, agent_name, step_number, step_type, description, duration_ms, timestamp
           FROM agent_steps
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    )
    rows = await cursor.fetchall()
    steps = [dict(r) for r in reversed(rows)]
    return {"steps": steps}


@app.get("/api/costs")
async def get_costs(days: int = 30) -> dict:
    """Cost breakdown per periodo."""
    if not memory:
        return {"breakdown": {}}
    breakdown = await memory.get_cost_breakdown(period_days=days)
    breakdown["budget_threshold_eur"] = settings.COST_ALERT_THRESHOLD_EUR
    return {"days": days, "breakdown": breakdown}


@app.get("/api/analytics/summary")
async def get_analytics_summary_endpoint(days: int = 14) -> dict:
    """Aggregati task (agent_logs + production_queue) per il pannello Analytics.

    Ritorna: total/completed/failed/running per periodo, per-day breakdown,
    per-agent stats, production_queue counters.
    Dati reali senza dipendenza da Etsy.
    """
    if not memory:
        return {"summary": {}}
    summary = await memory.get_agent_logs_summary(period_days=days)
    return {"summary": summary}


@app.get("/api/screen/status")
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


@app.get("/api/memory/stats")
async def get_memory_stats() -> dict:
    """Statistiche ChromaDB: collection count, disponibilità."""
    if not memory:
        return {"chroma": {"available": False, "count": 0}}
    chroma = await memory.get_chroma_stats()
    return {"chroma": chroma}



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
        return JSONResponse(status_code=401, content={"error": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})


@app.get("/api/etsy/listings")
async def get_etsy_listings(status: str = "all", limit: int = 50) -> dict:
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
async def get_finance_report(days: int = 30) -> dict:
    """Ultimo report finance da ChromaDB + trigger run se mai eseguito."""
    if not memory:
        return {"report": None}
    results = await memory.query_chromadb(
        query="finance report revenue cost margin ROI",
        n_results=1,
        where={"type": "finance_report"},
    )
    return {"report": results[0] if results else None, "days": days}


@app.post("/api/finance/run")
async def run_finance_agent(body: dict | None = None) -> dict:
    """Esegue il FinanceAgent manualmente (period_days dal body, default 30)."""
    if not pepe:
        return JSONResponse(status_code=503, content={"error": "Pepe non inizializzato"})
    period_days = (body or {}).get("period_days", 30)
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
async def get_analytics_failures(limit: int = 20) -> dict:
    """Ultime failure analysis dai listing."""
    if not memory:
        return {"failures": []}
    failures = await memory.get_all_listing_analyses(limit=limit)
    return {"failures": failures}


# ------------------------------------------------------------------
# WebSocket
# ------------------------------------------------------------------


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


# ------------------------------------------------------------------
# Static files (frontend build) — montati per ultimi
# ------------------------------------------------------------------

import os

_frontend_dist = os.path.join(
    os.path.dirname(__file__), "..", "..", "frontend", "dist"
)
if os.path.isdir(_frontend_dist):
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
