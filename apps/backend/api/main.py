"""FastAPI + WebSocket — API principale AgentPeXI."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from apps.backend.core.config import settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentTask

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
pepe = None  # apps.backend.core.pepe.Pepe — assegnato in lifespan
storage = None  # apps.backend.core.storage.StorageManager — assegnato in lifespan
etsy_api = None  # apps.backend.tools.etsy_api.EtsyAPI — assegnato in lifespan


# ------------------------------------------------------------------
# Lifespan
# ------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: MemoryManager, Pepe, workers, Telegram bot. Shutdown: graceful stop."""
    global memory, pepe, storage, etsy_api

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

    # 1. MemoryManager
    memory = MemoryManager()
    await memory.init()
    logger.info("MemoryManager inizializzato")

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
    )
    await scheduler.start()
    logger.info("Scheduler avviato")

    # 4. Bot Telegram (stesso event loop di FastAPI)
    telegram_bot = TelegramBot(pepe=pepe)
    await telegram_bot.start()

    yield

    # Shutdown (ordine inverso)
    await telegram_bot.stop()
    await scheduler.stop()
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
    """Task schedulati."""
    if not memory:
        return {"tasks": []}
    cursor = await memory._db.execute(
        "SELECT * FROM scheduled_tasks ORDER BY next_run"
    )
    rows = await cursor.fetchall()
    return {"tasks": [dict(r) for r in rows]}


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


@app.get("/api/memory/stats")
async def get_memory_stats() -> dict:
    """Statistiche ChromaDB: collection count, disponibilità."""
    if not memory:
        return {"chroma": {"available": False, "count": 0}}
    chroma = await memory.get_chroma_stats()
    return {"chroma": chroma}


@app.post("/api/chat")
async def post_chat(body: dict) -> dict:
    """Fallback HTTP per chat (alternativo al WebSocket)."""
    message = body.get("message", "")
    session_id = body.get("session_id", "default")
    if not message:
        return JSONResponse(status_code=400, content={"error": "message richiesto"})
    if not pepe:
        return JSONResponse(status_code=503, content={"error": "Pepe non inizializzato"})
    reply = await pepe.handle_user_message(message, source="web", session_id=session_id)
    return {"reply": reply}


# ------------------------------------------------------------------
# Session endpoints
# ------------------------------------------------------------------


@app.post("/api/sessions")
async def create_session() -> dict:
    """Crea una nuova sessione, ritorna session_id UUID."""
    import uuid

    session_id = str(uuid.uuid4())
    return {"session_id": session_id}


@app.get("/api/sessions")
async def list_sessions() -> dict:
    """Lista sessioni con ultimo messaggio e timestamp, limit 20."""
    if not memory:
        return {"sessions": []}
    sessions = await memory.get_sessions(limit=20)
    return {"sessions": sessions}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict:
    """Cancella tutti i messaggi di una sessione."""
    if not memory:
        return JSONResponse(status_code=503, content={"error": "MemoryManager non inizializzato"})
    await memory.clear_session(session_id)
    return {"status": "ok"}


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
    """WebSocket bidirezionale: messaggi utente ↔ eventi sistema."""
    await ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "user_message":
                message = data.get("content", "")
                session_id = data.get("session_id", "default")
                if not message or not pepe:
                    continue
                # Gestisci in background per non bloccare il WS receiver
                asyncio.create_task(_handle_ws_message(ws, message, session_id))

            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)


async def _handle_ws_message(ws: WebSocket, message: str, session_id: str) -> None:
    """Processa messaggio utente via WS e invia risposta."""
    try:
        # La risposta viene broadcastata da Pepe via ws_broadcaster a tutti i client
        await pepe.handle_user_message(message, source="web", session_id=session_id)
    except Exception as exc:
        logger.error("Errore WS message: %s", exc)
        await ws.send_json({
            "type": "error",
            "content": str(exc),
        })


# ------------------------------------------------------------------
# Static files (frontend build) — montati per ultimi
# ------------------------------------------------------------------

import os

_frontend_dist = os.path.join(
    os.path.dirname(__file__), "..", "..", "frontend", "dist"
)
if os.path.isdir(_frontend_dist):
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
