import logging
from typing import Any

import apps.backend.api.state as state
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("agentpexi.api")
router = APIRouter(dependencies=[Depends(state.verify_personal_key)])


def _map_autopilot_status(raw: str) -> str:
    """Mappa lo stato interno AutopilotLoop ai 3 stati FE: running|paused|stopped."""
    if raw == "running":
        return "running"
    if raw.startswith("paused"):
        return "paused"
    return "stopped"  # idle, "" o qualsiasi altro valore


@router.get("/api/autopilot/status")
async def get_autopilot_status() -> dict[str, Any]:
    """
    Stato corrente dell'AutopilotLoop (FE-Blocco 0.5).

    Risposta: { status, current_niche, items_today, last_run_at }
    """
    if not state.autopilot_loop:
        return {"status": "stopped", "current_niche": None, "items_today": 0, "last_run_at": None}
    try:
        raw_status    = await state.autopilot_loop._get_status()
        current_niche = await state.autopilot_loop._state_get("loop.current_niche", "") or None
        last_run_raw  = await state.autopilot_loop._state_get("loop.last_run_at", "")

        # items pubblicati oggi
        items_today = 0
        if state.memory:
            from datetime import date as _date, datetime as _dt, timezone as _tz
            _today_start = _dt.combine(_date.today(), _dt.min.time()).replace(tzinfo=_tz.utc).timestamp()
            db = await state.memory.get_db()
            cur = await db.execute(
                "SELECT COUNT(*) AS cnt FROM production_queue WHERE status = 'published' AND published_at >= ?",
                (_today_start,),
            )
            row = await cur.fetchone()
            items_today = int(row["cnt"]) if row else 0

        return {
            "status":        _map_autopilot_status(raw_status),
            "current_niche": current_niche,
            "items_today":   items_today,
            "last_run_at":   float(last_run_raw) if last_run_raw else None,
        }
    except Exception:
        logger.exception("get_autopilot_status error")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/autopilot/start")
async def autopilot_start() -> dict[str, Any]:
    """Avvia o riprende l'AutopilotLoop."""
    if not state.autopilot_loop:
        raise HTTPException(status_code=503, detail="AutopilotLoop non inizializzato")
    try:
        await state.autopilot_loop.resume()
        return {"status": "running"}
    except Exception:
        logger.exception("autopilot_start error")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/autopilot/pause")
async def autopilot_pause() -> dict[str, Any]:
    """Mette in pausa l'AutopilotLoop (paused_manual)."""
    if not state.autopilot_loop:
        raise HTTPException(status_code=503, detail="AutopilotLoop non inizializzato")
    try:
        await state.autopilot_loop.stop()   # stop() → paused_manual
        return {"status": "paused"}
    except Exception:
        logger.exception("autopilot_pause error")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/autopilot/stop")
async def autopilot_stop() -> dict[str, Any]:
    """Ferma l'AutopilotLoop e imposta status=stopped."""
    if not state.autopilot_loop:
        raise HTTPException(status_code=503, detail="AutopilotLoop non inizializzato")
    try:
        await state.autopilot_loop.stop(final=True)   # atomico: idle + clear niche sotto _cmd_lock
        return {"status": "stopped"}
    except Exception:
        logger.exception("autopilot_stop error")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/run/analytics")
async def run_analytics_now(request: Request) -> dict[str, Any]:
    """Trigger manuale analytics (non aspetta le 08:00)."""
    if not state.pepe:
        raise HTTPException(status_code=503, detail="Pepe non inizializzato")
    from apps.backend.core.models import AgentTask
    task = AgentTask(agent_name="analytics", input_data={}, source="api_manual")
    state.pepe._fire(state.pepe.dispatch_task(task), name="analytics_manual")
    return {"status": "started"}
