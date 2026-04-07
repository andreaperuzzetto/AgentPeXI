from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_operator, get_db
from api.schemas.task import TaskDetail, TaskListResponse, TaskSummary
from db.models.task import Task

log = structlog.get_logger()

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _task_to_detail(t: Task) -> TaskDetail:
    return TaskDetail(
        id=str(t.id),
        type=t.type,
        agent=t.agent,
        deal_id=str(t.deal_id) if t.deal_id else None,
        client_id=str(t.client_id) if t.client_id else None,
        status=t.status,
        payload=t.payload,
        output=t.output,
        error=t.error,
        blocked_reason=t.blocked_reason,
        retry_count=t.retry_count,
        started_at=t.started_at,
        completed_at=t.completed_at,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    deal_id: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    task_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> TaskListResponse:
    q = select(Task).where(Task.deleted_at.is_(None))
    if deal_id:
        try:
            q = q.where(Task.deal_id == uuid.UUID(deal_id))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_uuid", "message": "deal_id non valido", "detail": {}},
            )
    if agent:
        q = q.where(Task.agent == agent)
    if task_status:
        q = q.where(Task.status == task_status)

    total_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total: int = total_result.scalar_one()

    q = q.order_by(Task.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(q)
    tasks = result.scalars().all()

    items = [
        TaskSummary(
            id=str(t.id),
            type=t.type,
            agent=t.agent,
            deal_id=str(t.deal_id) if t.deal_id else None,
            status=t.status,
            retry_count=t.retry_count,
            started_at=t.started_at,
            completed_at=t.completed_at,
            created_at=t.created_at,
        )
        for t in tasks
    ]
    return TaskListResponse(items=items, total=total, page=page, per_page=per_page)


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _operator: str = Depends(get_current_operator),
) -> TaskDetail:
    try:
        uid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_uuid", "message": "task_id non valido", "detail": {}},
        )

    result = await db.execute(
        select(Task).where(Task.id == uid, Task.deleted_at.is_(None))
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "task_not_found", "message": f"Task {task_id} non trovato", "detail": {}},
        )

    return _task_to_detail(task)
