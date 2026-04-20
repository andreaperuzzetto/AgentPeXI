"""FastAPI + WebSocket — API principale AgentPeXI."""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import re
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
    text_extractor = TextExtractor(max_chars=settings.SUMMARIZE_MAX_CHARS)

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
    # 4. Bot Telegram (stesso event loop di FastAPI) — prima dello scheduler
    # così set_reminder_notifier è già collegato quando il checker spara il primo fire
    telegram_bot = TelegramBot(pepe=pepe, scheduler=scheduler, screen_watcher=screen_watcher)
    await telegram_bot.start()

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
async def get_recent_agent_steps(limit: Annotated[int, Query(ge=1, le=500)] = 50) -> dict:
    """Ultimi N step per agente — usato per reidratare il ReasoningPanel al refresh."""
    if not memory:
        return {"steps": []}
    steps = await memory.get_recent_agent_steps(limit)
    return {"steps": steps}


@app.get("/api/costs")
async def get_costs(days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict:
    """Cost breakdown per periodo."""
    if not memory:
        return {"breakdown": {}}
    breakdown = await memory.get_cost_breakdown(period_days=days)
    breakdown["budget_threshold_eur"] = settings.COST_ALERT_THRESHOLD_EUR
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
    """Prossimi reminder pending ordinati per trigger_at."""
    if not memory:
        return {"reminders": []}
    reminders = await memory.get_pending_reminders()
    return {"reminders": reminders[:limit]}


@personal_router.get("/api/personal/recalls")
async def get_personal_recalls(limit: Annotated[int, Query(ge=1, le=100)] = 10) -> dict:
    """Ultimi N recall completati: query + risposta troncata + timestamp."""
    if not memory:
        return {"recalls": []}
    recalls = await memory.get_personal_recalls(limit)
    return {"recalls": recalls}


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
