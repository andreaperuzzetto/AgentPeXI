# Schemi dati

Tipi condivisi tra tutti gli agenti. Definiti in `agents/base.py`.

## ServiceType

```python
class ServiceType(StrEnum):
    CONSULTING         = "consulting"
    WEB_DESIGN         = "web_design"
    DIGITAL_MAINTENANCE = "digital_maintenance"
```

## TaskStatus

```python
class TaskStatus(StrEnum):
    PENDING    = "pending"
    RUNNING    = "running"
    BLOCKED    = "blocked"    # attende gate umano o sblocco
    RETRYING   = "retrying"
    COMPLETED  = "completed"
    FAILED     = "failed"
    CANCELLED  = "cancelled"
```

## AgentTask

```python
@dataclass
class AgentTask:
    id: UUID
    type: str            # "scout.discover" | "proposal.generate" | ecc.
    agent: str           # "scout" | "proposal" | ecc.
    deal_id: UUID | None
    client_id: UUID | None
    payload: dict
    status: TaskStatus = TaskStatus.PENDING
    blocked_reason: str | None = None   # obbligatorio se BLOCKED
    retry_count: int = 0
    idempotency_key: str | None = None  # f"{task.id}:{operation_name}"
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
```

## AgentResult

```python
@dataclass
class AgentResult:
    task_id: UUID
    success: bool
    output: dict
    error: str | None = None
    artifacts: list[str] = field(default_factory=list)   # path MinIO
    next_tasks: list[str] = field(default_factory=list)  # tipi task successivi
    requires_human_gate: bool = False
    gate_type: str | None = None  # "proposal_review" | "kickoff" | "delivery"
```

## DealStatus

```python
class DealStatus(StrEnum):
    LEAD_IDENTIFIED   = "lead_identified"
    ANALYSIS_COMPLETE = "analysis_complete"
    PROPOSAL_READY    = "proposal_ready"
    PROPOSAL_SENT     = "proposal_sent"
    NEGOTIATING       = "negotiating"
    CLIENT_APPROVED   = "client_approved"
    IN_DELIVERY       = "in_delivery"        # erogazione servizio in corso
    DELIVERED         = "delivered"
    ACTIVE            = "active"             # in post-vendita / manutenzione attiva
    LOST              = "lost"
    CANCELLED         = "cancelled"
```

> **Rimossi** rispetto alla versione precedente: `MOCKUP_READY`, `IN_DEVELOPMENT`, `IN_QA`.
> `IN_DELIVERY` sostituisce `IN_DEVELOPMENT` e `IN_QA` — il sistema ora eroga servizi, non sviluppa software.

## Deal — campi gate (leggere sempre da DB, mai da cache)

```python
# Gate flags — l'Orchestrator li verifica in checkpoint.py prima di ogni fase
deal.proposal_human_approved: bool   # GATE 1
deal.kickoff_confirmed: bool         # GATE 2
deal.delivery_approved: bool         # GATE 3 (per consulenza: consulting_approved)

# Tipo servizio
deal.service_type: ServiceType       # "consulting" | "web_design" | "digital_maintenance"

# Audit trail
deal.proposal_approved_at: datetime | None
deal.kickoff_confirmed_at: datetime | None
deal.delivery_approved_at: datetime | None

# Iterazione proposta
deal.proposal_rejection_count: int          # max 5, poi escalation manuale
deal.proposal_rejection_notes: str | None
```

## AgentState (LangGraph)

```python
class AgentState(TypedDict):
    run_id: str
    deal_id: str | None
    client_id: str | None
    service_type: str | None     # "consulting" | "web_design" | "digital_maintenance"
    current_phase: str           # "discovery" | "proposal" | "delivery" | "post_sale"
    current_agent: str
    messages: Annotated[list, add_messages]
    task_history: list[dict]
    # Accumulatori per fase
    leads: list[dict]
    selected_lead: dict | None
    analysis: dict | None
    artifact_paths: list[str]    # mockup, presentazioni, schemi, roadmap
    proposal_path: str | None
    # Delivery tracking
    delivery_milestones: list[dict]   # milestone per servizio
    delivery_progress_pct: int | None
    # Gate
    awaiting_gate: bool
    gate_type: str | None
    # Errori
    error: str | None
    retry_count: int
```
