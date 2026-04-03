# BaseAgent — Specifica classe base

Ogni agente del sistema eredita da `BaseAgent` definita in `src/agents/base.py`.
Le dataclass, enum e modelli condivisi sono in `src/agents/models.py`.
Questo documento specifica contratto, lifecycle e pattern da rispettare.

---

## Struttura file

```
src/agents/
├── base.py          ← Classe BaseAgent (ABC)
├── models.py        ← ServiceType, TaskStatus, AgentTask, AgentResult, errori
├── _sse.py          ← Helper _publish_sse(): pubblica eventi SSE su Redis per il frontend
└── {nome}/
    ├── agent.py     ← Classe concreta che eredita BaseAgent
    └── tasks.py     ← Task Celery che istanzia l'agente
```

---

## `agents/models.py` — modelli condivisi

> **Definizione canonica di `ServiceType`, `TaskStatus`, `AgentTask`, `AgentResult`:**
> vedi [`docs/data-models.md`](data-models.md) — sezioni corrispondenti.
> Non duplicare qui.

Import tipico negli agenti:

```python
from agents.models import (
    ServiceType, TaskStatus,
    AgentTask, AgentResult,
    AgentToolError, GateNotApprovedError, TransientError,
)
```

Le tre classi di eccezione sono definite in `agents/models.py` e descritte di seguito.


class AgentToolError(Exception):
    """
    Eccezione base per tutti gli errori dei tool.
    Trasporta un codice errore snake_case (vedi docs/error-codes.md).
    """
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


class GateNotApprovedError(Exception):
    """Gate flag non approvato nel deal — bloccare il task."""
    pass


class TransientError(Exception):
    """
    Errore temporaneo recuperabile — Celery riproverà automaticamente
    (max 3 volte con backoff esponenziale, configurato in agents/worker.py).

    Sollevare quando il fallimento è causato da indisponibilità momentanea
    di un servizio esterno (DB, Redis, API), non da un errore logico.

    Esempi:
        raise TransientError("Connessione PostgreSQL persa")
        raise TransientError("Google Maps API timeout")
    """
    pass
```

---

## `agents/base.py` — classe base

```python
# src/agents/base.py
from abc import ABC, abstractmethod
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agents.models import AgentTask, AgentResult, AgentToolError, GateNotApprovedError
from agents._sse import _publish_sse  # helper SSE: pubblica eventi real-time al frontend
from db.session import get_db_session
from tools.db_tools import (
    _mark_task_running, _mark_task_blocked,
    _mark_task_failed, _mark_task_completed,
)

log = structlog.get_logger()


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
        Non fare override. Gestisce: logging, DB session, try/except, SSE events.
        """
        run_id = task.payload.get("run_id", "")
        self.log.info("task.started", task_id=str(task.id), task_type=task.type)

        async with get_db_session() as db:
            await _mark_task_running(task.id, db)
            await _publish_sse(run_id, "task_started", self.agent_name, {"task_type": task.type})

            try:
                result = await self.execute(task, db)
            except GateNotApprovedError as e:
                self.log.warning("task.gate_blocked", task_id=str(task.id), reason=str(e))
                await _mark_task_blocked(task.id, str(e), db)
                await _publish_sse(run_id, "task_blocked", self.agent_name, {"reason": str(e)})
                return AgentResult(
                    task_id=task.id,
                    success=False,
                    output={},
                    error=str(e),
                    requires_human_gate=True,
                )
            except AgentToolError as e:
                self.log.error("task.tool_error", task_id=str(task.id), error_code=e.code)
                await _mark_task_failed(task.id, e.code, db)
                await _publish_sse(run_id, "task_failed", self.agent_name, {"error_code": e.code})
                return AgentResult(task_id=task.id, success=False, output={}, error=e.code)
            except Exception as e:
                self.log.error("task.unexpected_error", task_id=str(task.id), error=str(e))
                await _mark_task_failed(task.id, str(e), db)
                raise  # ri-lancia per Celery retry

            if result.success:
                self.log.info("task.completed", task_id=str(task.id), output_keys=list(result.output.keys()))
                await _mark_task_completed(task.id, result.output, db)
                await _publish_sse(run_id, "task_completed", self.agent_name, {"output_keys": list(result.output.keys())})
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
        raise AgentToolError(
            code="tool_db_deal_not_found",
            message=f"Deal {task.payload['deal_id']} non trovato",
        )

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
