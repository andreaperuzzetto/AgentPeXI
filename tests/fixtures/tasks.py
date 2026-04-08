"""Fixtures per AgentTask."""

from __future__ import annotations

import uuid
from datetime import datetime

from agents.models import AgentTask, TaskStatus


def make_task(
    agent: str = "scout",
    payload: dict | None = None,
    task_type: str | None = None,
    deal_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    status: TaskStatus = TaskStatus.PENDING,
    idempotency_key: str | None = None,
) -> AgentTask:
    """
    Factory leggera per AgentTask.

    Args:
        agent:           nome dell'agente (es. "scout", "analyst")
        payload:         payload del task; se None usa dict vuoto
        task_type:       type stringa del task; default "{agent}.run"
        deal_id:         UUID del deal opzionale
        client_id:       UUID del client opzionale
        status:          stato iniziale del task
        idempotency_key: chiave idempotenza opzionale

    Returns:
        AgentTask configurato con id UUID casuale.
    """
    return AgentTask(
        id=uuid.uuid4(),
        type=task_type or f"{agent}.run",
        agent=agent,
        deal_id=deal_id,
        client_id=client_id,
        payload=payload or {},
        status=status,
        retry_count=0,
        idempotency_key=idempotency_key,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
