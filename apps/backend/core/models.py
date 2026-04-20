"""Modelli dati condivisi AgentPeXI."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Literal


class AgentStatus(str, Enum):
    """Stato corrente di un agente nel registry di Pepe."""

    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"


class TaskStatus(str, Enum):
    """Stato di un task nella coda di esecuzione."""

    PENDING = "pending"
    RUNNING = "running"
    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentTask:
    """Task inviato da Pepe a un agente."""

    agent_name: str
    input_data: dict[str, Any]
    source: str = "web"  # "web" | "telegram" | "scheduler"
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pending_input: dict | None = None


@dataclass
class AgentCard:
    """
    Auto-dichiarazione delle capabilities di un agente.
    Definita a livello di classe in ogni AgentBase subclass.
    Pepe la legge dal registry per costruire tool, routing e prompt.
    """

    name: str
    description: str
    input_schema: dict
    layer: Literal["personal", "business"]
    llm: Literal["ollama", "sonnet", "haiku"]
    requires_confirmation: bool = False
    requires_clarification: list[str] = field(default_factory=list)
    confidence_threshold: float = 0.85
    pipeline_position: int | None = None


@dataclass
class AgentResult:
    """Risultato restituito da un agente a Pepe."""

    task_id: str
    agent_name: str
    status: TaskStatus
    output_data: dict[str, Any] = field(default_factory=dict)
    tokens_used: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    confidence: float = 0.0
    missing_data: list[str] = field(default_factory=list)
