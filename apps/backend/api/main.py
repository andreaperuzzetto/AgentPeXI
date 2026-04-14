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


# ------------------------------------------------------------------
# Lifespan
# ------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: MemoryManager, Pepe, workers, Telegram bot. Shutdown: graceful stop."""
    global memory, pepe

    from apps.backend.core.pepe import Pepe
    from apps.backend.core.scheduler import Scheduler
    from apps.backend.telegram.bot import TelegramBot

    # 1. MemoryManager
    memory = MemoryManager()
    await memory.init()
    logger.info("MemoryManager inizializzato")

    # 2. Pepe orchestratore
    pepe = Pepe(memory=memory, ws_broadcaster=ws_manager.broadcast)
    await pepe.start()
    logger.info("Pepe avviato")

    # 3. Scheduler APScheduler
    scheduler = Scheduler(memory=memory, ws_broadcaster=ws_manager.broadcast, pepe=pepe)
    await scheduler.start()
    logger.info("Scheduler avviato")

    # 4. Bot Telegram (stesso event loop di FastAPI)
    telegram_bot = TelegramBot(pepe=pepe)
    await telegram_bot.start()

    yield

    # Shutdown (ordine inverso)
    await telegram_bot.stop()
    await scheduler.stop()
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
    }


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
    return {"days": days, "breakdown": breakdown}


@app.post("/api/chat")
async def post_chat(body: dict) -> dict:
    """Fallback HTTP per chat (alternativo al WebSocket)."""
    message = body.get("message", "")
    if not message:
        return JSONResponse(status_code=400, content={"error": "message richiesto"})
    if not pepe:
        return JSONResponse(status_code=503, content={"error": "Pepe non inizializzato"})
    reply = await pepe.handle_user_message(message, source="web")
    return {"reply": reply}


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
                if not message or not pepe:
                    continue
                # Gestisci in background per non bloccare il WS receiver
                asyncio.create_task(_handle_ws_message(ws, message))

            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)


async def _handle_ws_message(ws: WebSocket, message: str) -> None:
    """Processa messaggio utente via WS e invia risposta."""
    try:
        reply = await pepe.handle_user_message(message, source="web")
        # La risposta viene già broadcastata da Pepe via ws_broadcaster,
        # ma inviamo anche un ack diretto al client che ha inviato
        await ws.send_json({
            "type": "pepe_message",
            "content": reply,
            "source": "web",
        })
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
