import logging
from datetime import datetime, timezone
from typing import Annotated, Any

import apps.backend.api.state as state
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.api")
router = APIRouter()


class ProductionQueueItemResponse(BaseModel):
    id: int
    task_id: str
    niche: str
    product_type: str
    brief: dict | None
    status: str
    entry_score: float | None
    listing_price: float | None
    listing_title: str | None
    file_paths: list[str] | None
    etsy_listing_id: str | None
    ads_activated: int | None
    created_at: str
    updated_at: str
    product_tier: str | None = None
    section_name: str | None = None
    cluster_id: str | None = None


class ProductionQueueResponse(BaseModel):
    items: list[ProductionQueueItemResponse]


@router.get("/api/health")
async def health_check() -> JSONResponse:
    """Readiness probe — verifica salute dei componenti principali e restituisce 503 se down."""
    checks: dict[str, str] = {}
    try:
        if state.memory is None:
            checks["db"] = "error: MemoryManager not initialized"
        else:
            db = await state.memory.get_db()
            await db.execute("SELECT 1")
            checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"
    status_code = 200 if all(v == "ok" for v in checks.values()) else 503
    return JSONResponse(content=checks, status_code=status_code)


@router.get("/api/status", dependencies=[Depends(state.verify_personal_key)])
async def get_status() -> dict[str, Any]:
    """Stato generale del sistema."""
    agent_statuses = state.pepe.get_agent_statuses() if state.pepe else {}
    return {
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agents": agent_statuses,
        "queue_size": state.pepe._queue.qsize() if state.pepe else 0,
        "connected_clients": len(state.ws_manager._connections),
        "mock_mode": state.pepe.mock_mode if state.pepe else False,
    }


@router.get("/api/mock/status", dependencies=[Depends(state.verify_personal_key)])
async def get_mock_status() -> dict[str, Any]:
    """Stato corrente del mock mode."""
    return {"mock_mode": state.pepe.mock_mode if state.pepe else False}


@router.get("/api/agents", dependencies=[Depends(state.verify_personal_key)])
async def get_agents() -> dict[str, Any]:
    """Stato dettagliato degli agenti registrati."""
    if not state.pepe:
        return {"agents": {}}
    return {"agents": state.pepe.get_agent_statuses()}


@router.get("/api/domains/config", dependencies=[Depends(state.verify_personal_key)])
async def get_domains_config() -> dict[str, Any]:
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


@router.get("/api/listings", dependencies=[Depends(state.verify_personal_key)])
async def get_listings() -> dict[str, Any]:
    """Lista dei listing Etsy dal DB locale."""
    if not state.memory:
        return {"listings": []}
    listings = await state.memory.get_etsy_listings(limit=100)
    return {"listings": listings}


@router.get("/api/scheduler", dependencies=[Depends(state.verify_personal_key)])
async def get_scheduler() -> dict[str, Any]:
    """Task schedulati: job APScheduler attivi + task da DB."""
    db_tasks: list[dict] = []
    if state.memory:
        db_tasks = await state.memory.get_scheduled_tasks()

    apscheduler_jobs: list[dict] = []
    if state.scheduler:
        apscheduler_jobs = state.scheduler.get_jobs()

    return {"tasks": db_tasks, "jobs": apscheduler_jobs}


@router.get("/api/scheduler/jobs", dependencies=[Depends(state.verify_personal_key)])
async def get_scheduler_jobs() -> dict[str, Any]:
    """
    Job APScheduler attivi (FE-Blocco 0.4).

    Risposta pulita per il frontend — solo job APScheduler, senza task DB.
    Risposta: { jobs: [{ id, name, trigger, next_run, last_run, status }] }
    """
    if not state.scheduler:
        return {"jobs": []}
    try:
        return {"jobs": state.scheduler.get_jobs()}
    except Exception:
        logger.exception("get_scheduler_jobs error")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/production-queue", dependencies=[Depends(state.verify_personal_key)])
async def get_production_queue(status: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 50) -> ProductionQueueResponse:
    """Lista items dalla production_queue, filtrabili per status."""
    _VALID_STATUSES = {
        "all",
        "pending_design",
        "pending_approval",
        "approved",
        "skipped",
        "scheduled",
        "published",
        "failed",
        "discarded",
    }
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status non valido: {status}")
    if not state.memory:
        return ProductionQueueResponse(items=[])
    filter_status = None if status == "all" else status
    raw = await state.memory.get_production_queue(status=filter_status, limit=limit)
    items = [ProductionQueueItemResponse(**d) for d in raw]
    return ProductionQueueResponse(items=items)


@router.get("/api/tasks/{task_id}/timeline", dependencies=[Depends(state.verify_personal_key)])
async def get_task_timeline(task_id: str) -> dict[str, Any]:
    """Timeline completa step/llm/tool per un task (Task Detail View)."""
    if not state.memory:
        return {"timeline": []}
    timeline = await state.memory.get_task_timeline(task_id)
    return {"task_id": task_id, "timeline": timeline}


@router.get("/api/tasks/pending-input", dependencies=[Depends(state.verify_personal_key)])
async def get_pending_input_tasks() -> dict[str, Any]:
    """Lista task in stato INPUT_REQUIRED — sospesi in attesa di risposta utente."""
    if not state.memory:
        return {"tasks": []}
    try:
        tasks = await state.memory.get_pending_input_tasks()
        return {"tasks": tasks}
    except Exception:
        logger.exception("pending-input error")
        raise HTTPException(status_code=500, detail="Errore interno")


@router.get("/api/agents/steps/recent", dependencies=[Depends(state.verify_personal_key)])
async def get_recent_agent_steps(
    limit:      Annotated[int, Query(ge=1, le=500)] = 50,
    agent_name: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Ultimi N step — opzionale filtro per agent_name.
    Usato per reidratare ReasoningPanel e AgentDetailPanel."""
    if not state.memory:
        return {"steps": []}
    steps = await state.memory.get_recent_agent_steps(limit, agent_name=agent_name)
    return {"steps": steps}


@router.get("/api/costs", dependencies=[Depends(state.verify_personal_key)])
async def get_costs(days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict[str, Any]:
    """Cost breakdown per periodo."""
    if not state.memory:
        return {"breakdown": {}}
    breakdown = await state.memory.get_cost_breakdown(period_days=days)
    breakdown["budget_threshold_eur"] = settings.COST_ALERT_THRESHOLD_EUR
    breakdown["usd_eur_rate"] = settings.USD_EUR_RATE
    return {"days": days, "breakdown": breakdown}


@router.get("/api/analytics/summary", dependencies=[Depends(state.verify_personal_key)])
async def get_analytics_summary_endpoint(days: Annotated[int, Query(ge=1, le=365)] = 14) -> dict[str, Any]:
    """Aggregati task (agent_logs + production_queue) per il pannello Analytics.

    Ritorna: total/completed/failed/running per periodo, per-day breakdown,
    per-agent stats, production_queue counters.
    Dati reali senza dipendenza da Etsy.
    """
    if not state.memory:
        return {"summary": {}}
    summary = await state.memory.get_agent_logs_summary(period_days=days)
    return {"summary": summary}
