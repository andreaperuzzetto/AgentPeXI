from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RunSummary(BaseModel):
    run_id: str
    deal_id: str | None
    status: str
    gate_type: str | None
    awaiting_gate_since: datetime | None
    current_phase: str | None
    current_agent: str | None
    started_at: datetime

    model_config = {"from_attributes": True}


class RunListResponse(BaseModel):
    items: list[RunSummary]
    total: int
    page: int
    per_page: int


class RunCreate(BaseModel):
    type: str  # "discovery" | "proposal" | "delivery" | "post_sale" | "support"
    payload: dict[str, Any] | None = None


class RunCreateResponse(BaseModel):
    run_id: str
    status: str
    created_at: datetime


class RunDetail(BaseModel):
    run_id: str
    deal_id: str | None
    status: str
    current_phase: str | None
    current_agent: str | None
    task_history: list[dict[str, Any]]
    awaiting_gate: bool
    gate_type: str | None
    error: str | None
    started_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}
