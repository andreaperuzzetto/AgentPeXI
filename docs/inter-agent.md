# Protocollo inter-agente

Gli agenti **non si chiamano direttamente**. Tutto passa dall'Orchestrator via Redis.

---

## Flusso

```
Orchestrator → [Celery task] → Worker sync → asyncio.run() → Agente async → [Redis publish] → Orchestrator
```

---

## Pattern Celery corretto su macOS (ARM)

Celery non supporta `async def` task nativamente.
Il pattern adottato è un wrapper sync → async per ogni agente:

```python
# agents/worker.py — worker centralizzato
# src/agents/worker.py
import asyncio
from celery import Celery
from agents.models import AgentTask, AgentResult, TransientError
import os

REDIS_URL = os.environ["REDIS_URL"]

app = Celery("agentpexi", broker=REDIS_URL, backend=REDIS_URL)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,           # ack dopo completamento task (non dopo ricezione)
    worker_prefetch_multiplier=1,  # un task alla volta per worker (safe con acks_late)
    task_time_limit=600,           # hard kill dopo 10 minuti
    task_soft_time_limit=300,      # SIGTERM dopo 5 minuti (gestibile)
)

def _make_task(agent_name: str, agent_class):
    @app.task(
        name=f"agents.{agent_name}.run",
        autoretry_for=(TransientError,),
        retry_backoff=True,       # 2s → 4s → 8s
        retry_backoff_max=60,
        max_retries=3,
        bind=True,
    )
    def run(self, task_dict: dict) -> dict:
        """Wrapper sync → async. Non rendere async questa funzione."""
        task = AgentTask(**task_dict)
        agent = agent_class()
        result: AgentResult = asyncio.run(agent.run(task))
        # Due chiamate asyncio.run() separate: intenzionale.
        # La prima chiude il loop dopo agent.run(); la seconda ne apre uno nuovo
        # per la pubblicazione. Su macOS ARM (no uvloop) questo evita
        # "This event loop is already running" con asyncpg.
        asyncio.run(_publish_result(result))
        return result.model_dump()
    return run

# Registra tutti gli agenti
from agents.scout.agent                   import ScoutAgent
from agents.analyst.agent                 import AnalystAgent
from agents.lead_profiler.agent           import LeadProfilerAgent
from agents.design.agent                  import DesignAgent
from agents.proposal.agent                import ProposalAgent
from agents.sales.agent                   import SalesAgent
from agents.delivery_orchestrator.agent   import DeliveryOrchestratorAgent
from agents.doc_generator.agent           import DocGeneratorAgent
from agents.delivery_tracker.agent        import DeliveryTrackerAgent
from agents.account_manager.agent         import AccountManagerAgent
from agents.billing.agent                 import BillingAgent
from agents.support.agent                 import SupportAgent

scout_task                = _make_task("scout",                  ScoutAgent)
analyst_task              = _make_task("analyst",                AnalystAgent)
lead_profiler_task        = _make_task("lead_profiler",          LeadProfilerAgent)
design_task               = _make_task("design",                 DesignAgent)
proposal_task             = _make_task("proposal",               ProposalAgent)
sales_task                = _make_task("sales",                  SalesAgent)
delivery_orchestrator_task= _make_task("delivery_orchestrator",  DeliveryOrchestratorAgent)
doc_generator_task        = _make_task("doc_generator",          DocGeneratorAgent)
delivery_tracker_task     = _make_task("delivery_tracker",       DeliveryTrackerAgent)
account_manager_task      = _make_task("account_manager",        AccountManagerAgent)
billing_task              = _make_task("billing",                BillingAgent)
support_task              = _make_task("support",                SupportAgent)
```

> **Non usare** `async def` nel task Celery — i task non verranno mai eseguiti.
> `asyncio.run()` è la soluzione corretta su macOS ARM (no `uvloop`, no `gevent`).

---

## Pubblicazione risultati

```python
import redis.asyncio as aioredis
import json

async def _publish_result(result: AgentResult) -> None:
    r = aioredis.from_url(REDIS_URL)
    await r.publish(
        f"agent_results:{result.task_id}",
        json.dumps(result.model_dump(), default=str)
    )
    await r.aclose()
```

L'Orchestrator LangGraph è l'unico subscriber su questi canali.
I sotto-agenti non si ascoltano tra loro.

---

## Dispatch dall'Orchestrator

```python
# orchestrator/nodes/delegate.py
from agents.worker import app as celery_app

def dispatch_task(task: AgentTask) -> None:
    celery_app.send_task(
        f"agents.{task.agent}.run",
        args=[task.model_dump(mode="json")],
        task_id=str(task.id),
    )
```

---

## Timeout per chiamata esterna

| Operazione | Timeout | Retry max |
|-----------|---------|-----------|
| Anthropic API | 120s | — (LangGraph gestisce) |
| Google Maps | 10s | 3× |
| Gmail send | 30s | 2× |
| Puppeteer render | 60s | 2× |
| Task Celery totale | 600s | 3× con backoff |

---

## Idempotenza

Pattern canonico documentato in [`docs/base-agent.md` → Pattern idempotenza](base-agent.md).
Ogni operazione esterna ritentabile deve scrivere `idempotency_key = f"{task.id}:{operation_name}"`
su `tasks.idempotency_key` (colonna `UNIQUE`) prima di eseguire.

---

## Gestione run paralleli

Se più deal sono in pipeline contemporaneamente, i canali Redis sono isolati per `task_id`:
`agent_results:{task_id}` — non per `deal_id`. L'Orchestrator mantiene il mapping
`task_id → run_id` nell'`AgentState.task_history`.

---

## Rate limiting Google Maps

Usare **sempre** `tools/google_maps.py` — gestisce 100 req/s con token bucket.
Non instanziare `googlemaps.Client` direttamente.

---

## Avvio worker in sviluppo

```bash
source .venv/bin/activate
celery -A agents.worker worker --loglevel=info --concurrency=4
```

`--concurrency=4` è adeguato per Mac Mini M4 (16GB RAM).
Non usare `--pool=gevent` o `--pool=eventlet`.
