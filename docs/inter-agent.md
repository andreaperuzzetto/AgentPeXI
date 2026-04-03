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
import asyncio
from celery import Celery
from agents.base import AgentTask, AgentResult

app = Celery("agentpexi", broker=REDIS_URL, backend=REDIS_URL)

def _make_task(agent_name: str, agent_class):
    @app.task(
        name=f"agents.{agent_name}.run",
        autoretry_for=(TransientError,),
        retry_backoff=True,       # 2s → 4s → 8s
        retry_backoff_max=60,
        max_retries=3,
        time_limit=600,           # killed dopo 10 minuti
        bind=True,
    )
    def run(self, task_dict: dict) -> dict:
        """Wrapper sync → async. Non rendere async questa funzione."""
        task = AgentTask(**task_dict)
        agent = agent_class()
        result: AgentResult = asyncio.run(agent.run(task))
        # Pubblica risultato su Redis
        asyncio.run(_publish_result(result))
        return result.model_dump()
    return run

# Registra tutti gli agenti
from agents.scout.agent import ScoutAgent
from agents.analyst.agent import AnalystAgent
# ... tutti gli altri

scout_task      = _make_task("scout",    ScoutAgent)
analyst_task    = _make_task("analyst",  AnalystAgent)
# ...
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

Ogni scrittura su DB o API esterna che può essere ritentata deve usare:

```python
idempotency_key = f"{task.id}:{operation_name}"
# es. "550e8400-...:send_proposal_email"
```

Salvare su `tasks.idempotency_key` (colonna `UNIQUE`) prima di eseguire l'operazione.
Se il task viene ritentato e la chiave esiste già → l'operazione è già avvenuta, skip.

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
