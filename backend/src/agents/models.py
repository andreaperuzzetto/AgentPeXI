from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ServiceType(StrEnum):
    CONSULTING = "consulting"
    WEB_DESIGN = "web_design"
    DIGITAL_MAINTENANCE = "digital_maintenance"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DealStatus(StrEnum):
    LEAD_IDENTIFIED = "lead_identified"
    ANALYSIS_COMPLETE = "analysis_complete"
    PROPOSAL_READY = "proposal_ready"
    PROPOSAL_SENT = "proposal_sent"
    NEGOTIATING = "negotiating"
    CLIENT_APPROVED = "client_approved"
    IN_DELIVERY = "in_delivery"
    DELIVERED = "delivered"
    ACTIVE = "active"
    LOST = "lost"
    CANCELLED = "cancelled"


class AgentTask(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: UUID
    type: str
    agent: str
    deal_id: UUID | None
    client_id: UUID | None
    payload: dict
    status: TaskStatus = TaskStatus.PENDING
    blocked_reason: str | None = None
    retry_count: int = 0
    idempotency_key: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AgentResult(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    task_id: UUID
    success: bool
    output: dict
    error: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    next_tasks: list[str] = Field(default_factory=list)
    requires_human_gate: bool = False
    gate_type: str | None = None


class AgentToolError(Exception):
    """Errore di un tool — trasporta codice snake_case (vedi docs/error-codes.md)."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


class GateNotApprovedError(Exception):
    """Gate flag non approvato nel deal — blocca il task."""

    pass


class TransientError(Exception):
    """Errore temporaneo recuperabile — Celery riproverà automaticamente (max 3×)."""

    pass
