# Schemi dati

Tipi condivisi tra tutti gli agenti. Definiti in `src/agents/models.py`.

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
class AgentTask(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

## AgentResult

```python
class AgentResult(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    task_id: UUID
    success: bool
    output: dict
    error: str | None = None
    artifacts: list[str] = Field(default_factory=list)   # path MinIO
    next_tasks: list[str] = Field(default_factory=list)  # tipi task successivi
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
deal.delivery_approved: bool         # GATE 3 web_design e digital_maintenance
deal.consulting_approved: bool       # GATE 3 per service_type = consulting

# Tipo servizio
deal.service_type: ServiceType       # "consulting" | "web_design" | "digital_maintenance"

# Audit trail
deal.proposal_approved_at: datetime | None
deal.kickoff_confirmed_at: datetime | None
deal.delivery_approved_at: datetime | None
deal.consulting_approved_at: datetime | None

# Iterazione proposta
deal.proposal_rejection_count: int          # max 5, poi escalation manuale
deal.proposal_rejection_notes: str | None

# Iterazione consegna
deal.delivery_rejection_count: int          # incrementato ad ogni rifiuto consegna
deal.delivery_rejection_notes: str | None
```

> **Nota:** Usare sempre `deal.consulting_approved` per il servizio consulenza —
> mai leggere `delivery_approved` per questo service_type.

## AgentState (LangGraph)

> **Fonte canonica:** la definizione completa e aggiornata di `AgentState` è in
> [`docs/orchestrator.md`](orchestrator.md) — sezione "AgentState".
> Non duplicare qui per evitare disallineamenti.
