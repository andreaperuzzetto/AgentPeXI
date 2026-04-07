from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware import ErrorHandlerMiddleware, RequestLoggingMiddleware
from api.routers import (
    auth,
    clients,
    deals,
    leads,
    proposals,
    runs,
    stats,
    tasks,
    webhooks,
)
from orchestrator.graph import get_checkpointer

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    checkpointer = get_checkpointer()
    await checkpointer.setup()
    log.info("api.startup", checkpointer="postgres")
    yield
    log.info("api.shutdown")


app = FastAPI(title="AgentPeXI API", version="0.1.0", lifespan=lifespan)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Custom middleware ──────────────────────────────────────────────────────────
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(ErrorHandlerMiddleware)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(runs.router)
app.include_router(leads.router)
app.include_router(deals.router)
app.include_router(clients.router)
app.include_router(proposals.router)
app.include_router(tasks.router)
app.include_router(stats.router)
app.include_router(webhooks.router)


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}
