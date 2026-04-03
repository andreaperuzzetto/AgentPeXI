# BaseAgent — Specifica classe base

Ogni agente del sistema eredita da `BaseAgent` definita in `agents/base.py`.
Questo documento specifica contratto, lifecycle e pattern da rispettare.

---

## Struttura della classe

```python
# agents/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger()


class ServiceType(StrEnum):
    CONSULTING            = "consulting"
    WEB_DESIGN            = "web_design"
    DIGITAL_MAINTENANCE   = "digital_maintenance"


class TaskStatus(StrEnum):
    PENDING    = "pending"
    RUNNING    = "running"
    BLOCKED    = "blocked"
    RETRYING   = "retrying"
    COMPLETED  = "completed"
    FAILED     = "failed"
    CANCELLED  = "cancelled"


@dataclass
class AgentTask:
    id: UUID
    type: str
    agent: str
    payload: dict
    deal_id: UUID | None = None
    client_id: UUID | None = None
    status: TaskStatus = TaskStatus.PENDING
    blocked_reason: str | None = None
    retry_count: int = 0
    idempotency_key: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AgentResult:
    task_id: UUID
    success: bool
    output: dict
    error: str | None = None
    artifacts: list[str] = field(default_factory=list)
    next_tasks: list[str] = field(default_factory=list)
    requires_human_gate: bool = False
    gate_type: str | None = None  # "proposal_review" | "kickoff" | "delivery"


class AgentToolError(Exception):
    """Eccezione base per tutti gli errori dei tool."""
    pass


class GateNotApprovedError(Exception):
    """Gate flag non approvato nel deal — bloccare il task."""
    pass


class BaseAgent(ABC):
    """
    Classe base per tutti gli agenti AgentPeXI.

    Ogni agente concreto deve implementare `execute()`.
    Non override `run()` — contiene il lifecycle con logging e gestione errori.
    """

    # Attributo di classe: nome agente (obbligatorio nelle sottoclassi)
    agent_name: str

    def __init__(self) -> None:
        self.log = structlog.get_logger().bind(agent=self.agent_name)

    async def run(self, task: AgentTask) -> AgentResult:
        """
        Entry point chiamato dal Celery worker.
        Non fare override. Gestisce: logging, DB session, try/except.
        """
        self.log.info("task.started", task_id=str(task.id), task_type=task.type)

        async with get_db_session() as db:
            # Aggiorna task.status = running in DB
            await _mark_task_running(task.id, db)

            try:
                result = await self.execute(task, db)
            except GateNotApprovedError as e:
                self.log.warning("task.gate_blocked", task_id=str(task.id), reason=str(e))
                await _mark_task_blocked(task.id, str(e), db)
                return AgentResult(
                    task_id=task.id,
                    success=False,
                    output={},
                    error=str(e),
                    requires_human_gate=True,
                )
            except AgentToolError as e:
                self.log.error("task.tool_error", task_id=str(task.id), error=str(e))
                await _mark_task_failed(task.id, str(e), db)
                return AgentResult(task_id=task.id, success=False, output={}, error=str(e))
            except Exception as e:
                self.log.error("task.unexpected_error", task_id=str(task.id), error=str(e))
                await _mark_task_failed(task.id, str(e), db)
                raise  # ri-lancia per Celery retry

        if result.success:
            self.log.info("task.completed", task_id=str(task.id), output_keys=list(result.output.keys()))
            await _mark_task_completed(task.id, result.output)
        else:
            self.log.warning("task.failed", task_id=str(task.id), error=result.error)

        return result

    @abstractmethod
    async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
        """
        Logica specifica dell'agente.
        Implementare in ogni sottoclasse.

        Regole:
        - Verificare i gate flags leggendo SEMPRE da db (mai da task.payload)
        - Non loggare PII — solo ID dei record
        - In caso di blocco logico: restituire AgentResult(success=False, ...)
          con blocked_reason, non sollevare eccezioni
        - In caso di errore tool: sollevare AgentToolError (gestita dal wrapper)
        - Idempotenza: verificare idempotency_key prima di ogni scrittura esterna
        """
        ...
```

---

## Lifecycle di un task

```
Celery worker riceve task_dict
    │
    ▼
asyncio.run(agent.run(task))
    │
    ├─ Aggiorna tasks.status = "running" in DB
    │
    ▼
agent.execute(task, db)
    │
    ├─ [GateNotApprovedError] → status = "blocked", requires_human_gate = True
    ├─ [AgentToolError]       → status = "failed", Celery non riprova
    ├─ [Exception generica]   → status = "failed", Celery riprova (max 3×)
    │
    └─ [OK] → AgentResult(success=True, output={...})
        │
        ▼
    tasks.status = "completed", tasks.output = result.output
        │
        ▼
    _publish_result() su Redis → Orchestrator riceve e procede
```

---

## Pattern idempotenza (obbligatorio per operazioni esterne)

```python
async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
    # Prima di qualsiasi scrittura su API esterna o DB critica:
    idem_key = f"{task.id}:send_email"

    existing = await get_task_by_idempotency_key(idem_key, db)
    if existing and existing.status == TaskStatus.COMPLETED:
        # Operazione già eseguita — restituire output precedente
        self.log.info("task.idempotent_skip", task_id=str(task.id), key=idem_key)
        return AgentResult(task_id=task.id, success=True, output=existing.output)

    # Salva la chiave prima di eseguire
    await create_task(..., idempotency_key=idem_key, db=db)

    # Esegui l'operazione
    result = await tools.gmail.send_email(...)
    ...
```

---

## Pattern verifica gate (obbligatorio prima di azioni irreversibili)

```python
async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
    # Leggere SEMPRE da DB, mai fidarsi di task.payload per i gate
    deal = await get_deal(UUID(task.payload["deal_id"]), db)
    if deal is None:
        raise AgentToolError(f"Deal {task.payload['deal_id']} non trovato")

    if not deal.proposal_human_approved:
        raise GateNotApprovedError("GATE 1 non approvato")
    ...
```

---

## Agente concreto — struttura minima

```python
# agents/scout/agent.py
from agents.base import BaseAgent, AgentTask, AgentResult
from sqlalchemy.ext.asyncio import AsyncSession

class ScoutAgent(BaseAgent):
    agent_name = "scout"

    async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
        payload = task.payload
        zone: str = payload["zone"]
        sector: str = payload["sector"]
        radius_km: int = payload.get("radius_km", 10)
        max_results: int = payload.get("max_results", 20)
        dry_run: bool = payload.get("dry_run", False)

        # ... logica ...

        return AgentResult(
            task_id=task.id,
            success=True,
            output={
                "leads_found": leads_found,
                "leads_written": leads_written,
                "skipped_duplicates": skipped,
                "zone_searched": zone,
                "radius_used_km": radius_km,
            },
            next_tasks=["analyst.score_lead"],
        )
```

---

## Modello Anthropic da usare

Definito in `docs/stack.md`. Da istanziare nel costruttore:

```python
import anthropic

class SomeAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__()
        self._client = anthropic.AsyncAnthropic()  # legge ANTHROPIC_API_KEY da env
        self._model = "claude-sonnet-4-6"          # default per tutti gli agenti
        # Solo Orchestrator e Delivery Orchestrator usano "claude-opus-4-6"

    async def _call_claude(self, system: str, user: str) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=8192,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text
```

---

## Sistema prompt

Il system prompt di ogni agente è nel file `agents/{nome}/prompts/system.md`.
Viene letto a runtime con `Path(__file__).parent / "prompts" / "system.md"`.

```python
from pathlib import Path

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text()
```

---

## Testing dell'agente

Ogni agente ha un test in `tests/agents/test_{nome}.py`.
Vedere `docs/testing.md` per le convenzioni di test.

L'agente può essere eseguito in standalone:

```bash
python -m agents.{nome}.run --help
```

Il modulo `agents/{nome}/run.py` deve accettare `--dry-run` che
imposta `task.payload["dry_run"] = True` e inibisce le scritture su DB e API esterne.
