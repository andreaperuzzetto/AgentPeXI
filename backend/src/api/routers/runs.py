from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_operator, get_db
from api.schemas.run import RunCreate, RunCreateResponse, RunDetail, RunListResponse, RunSummary
from db.models.run import Run
from db.models.task import Task
from orchestrator.state import AgentState

log = structlog.get_logger()

router = APIRouter(prefix="/runs", tags=["runs"])


def _run_to_summary(run: Run) -> RunSummary:
    return RunSummary(
        run_id=run.run_id,
        deal_id=str(run.deal_id) if run.deal_id else None,
        status=run.status,
        gate_type=run.gate_type,
        awaiting_gate_since=run.awaiting_gate_since,
        current_phase=run.current_phase,
        current_agent=run.current_agent,
        started_at=run.started_at,
    )


@router.get("", response_model=RunListResponse)
async def list_runs(
    run_status: str | None = Query(default=None, alias="status"),
    deal_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> RunListResponse:
    q = select(Run).where(Run.deleted_at.is_(None))
    if run_status:
        q = q.where(Run.status == run_status)
    if deal_id:
        try:
            q = q.where(Run.deal_id == uuid.UUID(deal_id))
        except ValueError:
            raise HTTPException(status_code=400, detail={"error": "invalid_uuid", "message": "deal_id non valido", "detail": {}})

    count_q = select(Run.run_id).where(Run.deleted_at.is_(None))
    if run_status:
        count_q = count_q.where(Run.status == run_status)
    if deal_id:
        count_q = count_q.where(Run.deal_id == uuid.UUID(deal_id))

    from sqlalchemy import func
    total_result = await db.execute(
        select(func.count()).select_from(q.subquery())
    )
    total: int = total_result.scalar_one()

    q = q.order_by(Run.started_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(q)
    runs = result.scalars().all()

    return RunListResponse(
        items=[_run_to_summary(r) for r in runs],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post("", response_model=RunCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_run(
    request: Request,
    body: RunCreate,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> RunCreateResponse:
    """
    Crea un nuovo run e avvia il grafo LangGraph in background.
    Tipi supportati: discovery | proposal | delivery | post_sale | support
    """
    run_id = str(uuid.uuid4())
    now = datetime.utcnow()

    # Determina fase e deal_id dal payload
    phase_map: dict[str, str] = {
        "discovery": "discovery",
        "proposal": "proposal",
        "delivery": "delivery",
        "post_sale": "post_sale",
        "support": "post_sale",
    }
    current_phase = phase_map.get(body.type, "discovery")
    deal_id: str | None = body.payload.get("deal_id") if body.payload else None
    client_id: str | None = body.payload.get("client_id") if body.payload else None
    service_type: str | None = body.payload.get("service_type") if body.payload else None

    # Salva riga in runs
    run_row = Run(
        run_id=run_id,
        deal_id=uuid.UUID(deal_id) if deal_id else None,
        status="running",
        current_phase=current_phase,
        started_at=now,
    )
    db.add(run_row)
    await db.flush()

    # Costruisci stato iniziale LangGraph
    initial_state: AgentState = {  # type: ignore[assignment]
        "run_id": run_id,
        "deal_id": deal_id,
        "client_id": client_id,
        "service_type": service_type,
        "current_phase": current_phase,
        "current_agent": "",
        "messages": [],
        "task_history": [],
        "leads": [],
        "selected_lead": None,
        "analysis": None,
        "discovery_payload": body.payload or {},
        "artifact_paths": [],
        "proposal_path": None,
        "proposal_version": 0,
        "delivery_milestones": [],
        "delivery_progress_pct": None,
        "awaiting_gate": False,
        "gate_type": None,
        "proposal_rejection_count": 0,
        "negotiation_round": 0,
        "error": None,
        "retry_count": 0,
    }

    # Avvia il grafo in modo asincrono (fire-and-forget) —
    # il grafo persiste il proprio stato tramite checkpointer PostgreSQL
    graph = request.app.state.graph
    import asyncio
    asyncio.create_task(
        graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": run_id}},
        )
    )

    log.info("api.runs.created", run_id=run_id, type=body.type)
    await db.commit()

    return RunCreateResponse(
        run_id=run_id,
        status="started",
        created_at=now,
    )


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> RunDetail:
    result = await db.execute(
        select(Run).where(Run.run_id == run_id, Run.deleted_at.is_(None))
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "run_not_found", "message": f"Run {run_id} non trovato", "detail": {}},
        )

    # Task history per questo run
    tasks_result = await db.execute(
        select(Task)
        .where(Task.deleted_at.is_(None))
        .order_by(Task.created_at.asc())
    )
    # Filtra per deal_id se presente, altrimenti per run non c'è join diretto
    # Il run_id non è su tasks — usiamo deal_id se disponibile
    task_history: list[dict[str, Any]] = []
    if run.deal_id:
        tasks_result2 = await db.execute(
            select(Task)
            .where(Task.deal_id == run.deal_id, Task.deleted_at.is_(None))
            .order_by(Task.created_at.asc())
        )
        task_history = [
            {
                "task_id": str(t.id),
                "type": t.type,
                "status": t.status,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            }
            for t in tasks_result2.scalars().all()
        ]

    return RunDetail(
        run_id=run.run_id,
        deal_id=str(run.deal_id) if run.deal_id else None,
        status=run.status,
        current_phase=run.current_phase,
        current_agent=run.current_agent,
        task_history=task_history,
        awaiting_gate=run.gate_type is not None and run.status == "awaiting_gate",
        gate_type=run.gate_type,
        error=run.error,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


@router.post("/{run_id}/cancel", response_model=dict[str, str])
async def cancel_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> dict[str, str]:
    result = await db.execute(
        select(Run).where(Run.run_id == run_id, Run.deleted_at.is_(None))
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "run_not_found", "message": f"Run {run_id} non trovato", "detail": {}},
        )
    await db.execute(
        update(Run)
        .where(Run.run_id == run_id)
        .values(status="cancelled", updated_at=datetime.utcnow())
    )
    log.info("api.runs.cancelled", run_id=run_id)
    return {"run_id": run_id, "status": "cancelled"}
