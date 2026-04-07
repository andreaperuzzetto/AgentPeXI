from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TaskSummary(BaseModel):
    id: str
    type: str
    agent: str
    deal_id: str | None
    status: str
    retry_count: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TaskDetail(BaseModel):
    id: str
    type: str
    agent: str
    deal_id: str | None
    client_id: str | None
    status: str
    payload: dict[str, Any]
    output: dict[str, Any] | None
    error: str | None
    blocked_reason: str | None
    retry_count: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    items: list[TaskSummary]
    total: int
    page: int
    per_page: int
