# Orchestrator — Specifica LangGraph

Sistema di orchestrazione principale in `orchestrator/`. Coordina tutti gli agenti
tramite un grafo LangGraph, gestisce i gate umani e mantiene lo stato del run.

---

## Struttura directory

```
orchestrator/
├── graph.py            ← definizione grafo LangGraph (entry point)
├── state.py            ← AgentState TypedDict
├── nodes/
│   ├── __init__.py
│   ├── checkpoint.py   ← verifica gate flags da DB
│   ├── delegate.py     ← dispatch task Celery agli agenti
│   ├── router.py       ← conditional edges (routing decisionale)
│   └── gates.py        ← logica attesa gate umano + resume
└── __init__.py
```

---

## AgentState

Definito in `orchestrator/state.py`. È lo stato condiviso che attraversa tutti i nodi.

```python
from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    # Identificatori run
    run_id: str
    deal_id: str | None
    client_id: str | None
    service_type: str | None          # "consulting" | "web_design" | "digital_maintenance"

    # Posizione nella pipeline
    current_phase: str                # "discovery" | "proposal" | "delivery" | "post_sale"
    current_agent: str

    # Messaggi LangGraph (append-only)
    messages: Annotated[list, add_messages]

    # Storico task eseguiti in questo run
    task_history: list[dict]          # [{task_id, type, agent, status, completed_at}]

    # Accumulatori fase discovery
    leads: list[dict]
    selected_lead: dict | None
    analysis: dict | None

    # Artefatti (mockup, presentazioni, schemi, roadmap)
    artifact_paths: list[str]

    # Fase proposal
    proposal_path: str | None
    proposal_version: int

    # Fase delivery
    delivery_milestones: list[dict]   # [{sd_id, type, title, status, milestone_name}]
    delivery_progress_pct: int | None

    # Gate
    awaiting_gate: bool
    gate_type: str | None             # "proposal_review" | "kickoff" | "delivery"

    # Contatori iterazione
    proposal_rejection_count: int
    negotiation_round: int

    # Errori
    error: str | None
    retry_count: int
```

---

## Grafo principale (`orchestrator/graph.py`)

```python
from langgraph.graph import StateGraph, END
from orchestrator.state import AgentState
from orchestrator.nodes import checkpoint, delegate, router, gates

def build_graph() -> StateGraph:
    g = StateGraph(AgentState)

    # ── Nodi ──────────────────────────────────────────────────────────────────
    g.add_node("route_phase",              router.route_phase)
    g.add_node("dispatch_scout",           delegate.dispatch_scout)
    g.add_node("dispatch_analyst",         delegate.dispatch_analyst)
    g.add_node("dispatch_lead_profiler",   delegate.dispatch_lead_profiler)
    g.add_node("dispatch_design",          delegate.dispatch_design)
    g.add_node("dispatch_proposal",        delegate.dispatch_proposal)
    g.add_node("gate_proposal_review",     gates.await_proposal_review)     # GATE 1
    g.add_node("dispatch_sales",           delegate.dispatch_sales)
    g.add_node("gate_kickoff",             gates.await_kickoff)             # GATE 2
    g.add_node("dispatch_delivery_orch",   delegate.dispatch_delivery_orchestrator)
    g.add_node("dispatch_doc_generator",   delegate.dispatch_doc_generator)
    g.add_node("dispatch_delivery_tracker",delegate.dispatch_delivery_tracker)
    g.add_node("gate_delivery",            gates.await_delivery_approval)  # GATE 3
    g.add_node("dispatch_account_manager", delegate.dispatch_account_manager)
    g.add_node("dispatch_billing",         delegate.dispatch_billing)
    g.add_node("dispatch_support",         delegate.dispatch_support)
    g.add_node("handle_error",             router.handle_error)
    g.add_node("handle_blocked",           router.handle_blocked)

    # ── Entry point ───────────────────────────────────────────────────────────
    g.set_entry_point("route_phase")

    # ── Edge routing principale ───────────────────────────────────────────────
    g.add_conditional_edges(
        "route_phase",
        router.decide_next_node,
        {
            "discovery":       "dispatch_scout",
            "proposal":        "dispatch_design",
            "delivery":        "dispatch_delivery_orch",
            "post_sale":       "dispatch_account_manager",
            "awaiting_gate":   "gate_proposal_review",  # ramo gate
            "error":           "handle_error",
            "blocked":         "handle_blocked",
            END:               END,
        }
    )

    # ── Pipeline Discovery ────────────────────────────────────────────────────
    g.add_conditional_edges(
        "dispatch_scout",
        router.after_scout,
        {
            "qualify":  "dispatch_analyst",
            "blocked":  "handle_blocked",
            "error":    "handle_error",
        }
    )
    g.add_conditional_edges(
        "dispatch_analyst",
        router.after_analyst,
        {
            "qualified":      "dispatch_lead_profiler",
            "disqualified":   END,
            "next_lead":      "dispatch_analyst",   # prossimo lead nella lista
            "no_leads":       "handle_blocked",
            "error":          "handle_error",
        }
    )
    g.add_edge("dispatch_lead_profiler", "dispatch_design")

    # ── Pipeline Proposal ─────────────────────────────────────────────────────
    g.add_edge("dispatch_design", "dispatch_proposal")
    g.add_edge("dispatch_proposal", "gate_proposal_review")

    g.add_conditional_edges(
        "gate_proposal_review",
        router.after_gate_proposal,
        {
            "approved":   "dispatch_sales",
            "rejected":   "dispatch_design",        # rigenera artefatti
            "max_rejections": "handle_blocked",
        }
    )

    g.add_conditional_edges(
        "dispatch_sales",
        router.after_sales,
        {
            "client_approved":   "gate_kickoff",
            "negotiating":       "dispatch_sales",  # round negoziazione
            "max_negotiation":   "handle_blocked",
            "lost":              END,
            "await_response":    "gate_proposal_review",  # riusa il gate come checkpoint
        }
    )

    # ── Pipeline Delivery ─────────────────────────────────────────────────────
    g.add_edge("gate_kickoff", "dispatch_delivery_orch")

    g.add_conditional_edges(
        "dispatch_delivery_orch",
        router.after_delivery_orch,
        {
            "next_delivery": "dispatch_doc_generator",
            "all_done":      "gate_delivery",
            "blocked":       "handle_blocked",
            "error":         "handle_error",
        }
    )
    g.add_edge("dispatch_doc_generator", "dispatch_delivery_tracker")

    g.add_conditional_edges(
        "dispatch_delivery_tracker",
        router.after_delivery_tracker,
        {
            "approved":  "dispatch_delivery_orch",  # torna per next delivery
            "rejected":  "dispatch_doc_generator",  # revisione
            "error":     "handle_error",
        }
    )

    g.add_conditional_edges(
        "gate_delivery",
        router.after_gate_delivery,
        {
            "approved": "dispatch_account_manager",
            "blocked":  "handle_blocked",
        }
    )

    # ── Pipeline Post-Sale ────────────────────────────────────────────────────
    g.add_edge("dispatch_account_manager", "dispatch_billing")
    g.add_edge("dispatch_billing", END)

    # ── Support (triggered separatamente da ticket in ingresso) ──────────────
    # Il Support Agent è chiamato da /runs con type="post_sale" + action="support"
    # Non fa parte del flusso main — ha il proprio sotto-grafo o viene dispatched
    # direttamente dal webhook Gmail

    return g.compile(checkpointer=get_checkpointer())


def get_checkpointer():
    """Checkpointer Redis per persistere lo stato tra gate umani."""
    from langgraph.checkpoint.redis import RedisCheckpointer
    import os
    return RedisCheckpointer(os.environ["REDIS_URL"])
```

---

## Nodi checkpoint (`orchestrator/nodes/checkpoint.py`)

Ogni gate verifica il flag nel Deal **letto da DB** (non dallo stato LangGraph).

```python
from sqlalchemy.ext.asyncio import AsyncSession
from tools.db_tools import get_deal
from orchestrator.state import AgentState
import asyncio

async def check_gate_proposal(state: AgentState, db: AsyncSession) -> bool:
    """Verifica GATE 1: proposal_human_approved."""
    deal = await get_deal(state["deal_id"], db)
    return deal is not None and deal.proposal_human_approved is True

async def check_gate_kickoff(state: AgentState, db: AsyncSession) -> bool:
    """Verifica GATE 2: kickoff_confirmed."""
    deal = await get_deal(state["deal_id"], db)
    return deal is not None and deal.kickoff_confirmed is True

async def check_gate_delivery(state: AgentState, db: AsyncSession) -> bool:
    """Verifica GATE 3: delivery_approved."""
    deal = await get_deal(state["deal_id"], db)
    return deal is not None and deal.delivery_approved is True
```

---

## Nodi gate (`orchestrator/nodes/gates.py`)

I gate scrivono nella tabella `runs` impostando `status = 'awaiting_gate'` e terminano
immediatamente — **il processo non resta appeso**. Il resume viene effettuato dal
Gate Poller (Celery Beat), non dall'API.

```python
from orchestrator.state import AgentState
from db.session import get_db_session
import datetime

async def _write_runs_awaiting(run_id: str, gate_type: str) -> None:
    async with get_db_session() as db:
        await db.execute(
            """
            UPDATE runs
            SET status = 'awaiting_gate',
                gate_type = :gate_type,
                awaiting_gate_since = :now,
                updated_at = :now
            WHERE run_id = :run_id
            """,
            {"run_id": run_id, "gate_type": gate_type, "now": datetime.datetime.utcnow()},
        )
        await db.commit()

async def await_proposal_review(state: AgentState) -> AgentState:
    """
    GATE 1 — Scrive runs.status='awaiting_gate' e termina.
    Il Gate Poller rileva e riprende quando deal.proposal_human_approved = true.
    """
    await _write_runs_awaiting(state["run_id"], "proposal_review")
    return {**state, "awaiting_gate": True, "gate_type": "proposal_review"}

async def await_kickoff(state: AgentState) -> AgentState:
    """GATE 2 — Scrive runs.status='awaiting_gate'. Riprende quando deal.kickoff_confirmed = true."""
    await _write_runs_awaiting(state["run_id"], "kickoff")
    return {**state, "awaiting_gate": True, "gate_type": "kickoff"}

async def await_delivery_approval(state: AgentState) -> AgentState:
    """GATE 3 — Scrive runs.status='awaiting_gate'. Riprende quando deal.delivery_approved = true."""
    await _write_runs_awaiting(state["run_id"], "delivery")
    return {**state, "awaiting_gate": True, "gate_type": "delivery"}
```

### Gate Poller (Celery Beat)

Un task Celery Beat (`agents/worker.py`, schedule ogni 30 s) interroga la tabella `runs`
 per trovare run in attesa e verifica il flag corrispondente nel deal.

```python
# agents/tasks/gate_poller.py
import asyncio
from celery import shared_task
from db.session import get_db_session
from orchestrator.graph import build_graph

GATE_FLAG_MAP = {
    "proposal_review": "proposal_human_approved",
    "kickoff":         "kickoff_confirmed",
    "delivery":        "delivery_approved",
}

@shared_task(name="agents.gate_poller")
def poll_gates() -> None:
    asyncio.run(_poll_gates_async())

async def _poll_gates_async() -> None:
    async with get_db_session() as db:
        rows = await db.execute(
            "SELECT run_id, deal_id, gate_type FROM runs WHERE status = 'awaiting_gate'"
        )
        pending = rows.fetchall()

    for run_id, deal_id, gate_type in pending:
        await _try_resume(run_id, deal_id, gate_type)

async def _try_resume(run_id: str, deal_id: str, gate_type: str) -> None:
    async with get_db_session() as db:
        row = await db.execute(
            f"SELECT {GATE_FLAG_MAP[gate_type]} FROM deals WHERE id = :deal_id",
            {"deal_id": deal_id},
        )
        approved = row.scalar_one_or_none()

    if not approved:
        return  # gate ancora chiuso, riprova al prossimo ciclo

    # Flag approvato: riprendi il run
    graph = build_graph()
    config = {"configurable": {"thread_id": run_id}}
    await graph.ainvoke(None, config=config)

    # Aggiorna runs.status
    async with get_db_session() as db:
        await db.execute(
            "UPDATE runs SET status = 'running', updated_at = now() WHERE run_id = :rid",
            {"rid": run_id},
        )
        await db.commit()
```

Schedule Celery Beat (in `agents/worker.py`):
```python
celery_app.conf.beat_schedule = {
    "gate-poller": {
        "task": "agents.gate_poller",
        "schedule": 30.0,   # secondi
    },
}
```

> **Non usare `graph.ainvoke()` dall'handler API** per riprendere i gate.
> L'unico entry point di resume è il Gate Poller. L'API aggiorna solo il flag nel deal.

---

## Dispatch (`orchestrator/nodes/delegate.py`)

```python
from agents.worker import celery_app
from agents.base import AgentTask
from orchestrator.state import AgentState
import uuid

def _dispatch(agent: str, task_type: str, payload: dict, state: AgentState) -> str:
    """Invia un task Celery e restituisce il task_id."""
    task_id = str(uuid.uuid4())
    task = AgentTask(
        id=task_id,
        type=task_type,
        agent=agent,
        payload=payload,
        deal_id=state.get("deal_id"),
        client_id=state.get("client_id"),
    )
    celery_app.send_task(
        f"agents.{agent}.run",
        args=[task.model_dump()],
        task_id=task_id,
    )
    return task_id

async def dispatch_scout(state: AgentState) -> AgentState:
    task_id = _dispatch("scout", "scout.discover", state["discovery_payload"], state)
    # Attende risultato via Redis pub/sub
    result = await _await_result(task_id)
    return {**state, "leads": result["output"]["leads"], "task_history": [...]}
```

---

## Avvio run da API

```python
# POST /runs — api/routes/runs.py
from orchestrator.graph import build_graph

async def start_run(run_type: str, payload: dict) -> dict:
    graph = build_graph()
    run_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": run_id}}

    initial_state: AgentState = {
        "run_id": run_id,
        "deal_id": payload.get("deal_id"),
        "client_id": None,
        "service_type": None,
        "current_phase": run_type,   # "discovery" | "proposal" | "delivery" | "post_sale"
        "current_agent": "",
        "messages": [],
        "task_history": [],
        "leads": [],
        "selected_lead": None,
        "analysis": None,
        "artifact_paths": [],
        "proposal_path": None,
        "proposal_version": 0,
        "delivery_milestones": [],
        "delivery_progress_pct": None,
        "awaiting_gate": False,
        "gate_type": None,
        "proposal_rejection_count": 0,
        "negotiation_round": 0,
        "error": None,
        "retry_count": 0,
        # Payload specifici per fase (passati al primo nodo)
        "discovery_payload": payload if run_type == "discovery" else {},
    }

    # Avvia in background — non attendere completamento
    asyncio.create_task(graph.ainvoke(initial_state, config=config))

    return {"run_id": run_id, "status": "started"}
```

---

## Thread ID e isolamento run

Ogni run ha un `run_id` (UUID) che funge da `thread_id` nel checkpointer LangGraph.
I canali Redis per i risultati Celery sono namespaced per `task_id` (non `run_id`).
L'Orchestrator mantiene il mapping `task_id → run_id` in `AgentState.task_history`.

Run paralleli su deal diversi non condividono stato — sono thread LangGraph separati.

---

## Modalità sviluppo

```bash
# Avvia orchestrator in modalità interattiva (dev)
python -m orchestrator.graph --dev

# In modalità --dev:
# - I gate non attendono approvazione esterna
# - GATE 1: auto-approved dopo 3 secondi
# - GATE 2 e 3: auto-approved
# - Utile per testare l'intera pipeline end-to-end
```
