"""
AgentPeXI — Celery worker centralizzato.

Avvio:
    celery -A agents.worker worker --loglevel=info --concurrency=4
    celery -A agents.worker beat  --loglevel=info

Pattern obbligatorio macOS ARM: ogni task è sync e chiama asyncio.run() due volte separate.
Non usare async def, uvloop, gevent o eventlet.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import redis.asyncio as aioredis
import structlog
from celery import Celery

from agents.models import AgentResult, AgentTask, TransientError

# ── Configurazione ─────────────────────────────────────────────────────────────
REDIS_URL: str = os.environ["REDIS_URL"]

app = Celery("agentpexi", broker=REDIS_URL, backend=REDIS_URL)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,            # ack dopo completamento (non dopo ricezione)
    worker_prefetch_multiplier=1,   # un task alla volta — safe con acks_late
    task_time_limit=600,            # hard kill dopo 10 minuti
    task_soft_time_limit=300,       # SIGTERM dopo 5 minuti (gestibile)
    broker_connection_retry_on_startup=True,  # Celery 6.0 compat
)

# Alias per import dall'Orchestrator
celery_app = app

log = structlog.get_logger()


# ── Pubblicazione risultati su Redis ──────────────────────────────────────────

async def _publish_result(result: AgentResult) -> None:
    """
    Pubblica il risultato dell'agente sul canale Redis dell'Orchestrator.
    Chiamato in un asyncio.run() separato dopo agent.run() — intenzionale su macOS ARM
    per evitare "This event loop is already running" con asyncpg.
    """
    r = aioredis.from_url(REDIS_URL)
    try:
        await r.publish(
            f"agent_results:{result.task_id}",
            json.dumps(result.model_dump(), default=str),
        )
    finally:
        await r.aclose()


# ── Factory task ──────────────────────────────────────────────────────────────

def _make_task(agent_name: str, agent_class: type) -> Any:
    """
    Crea e registra un task Celery sync per l'agente dato.

    Due chiamate asyncio.run() separate sono intenzionali:
    - La prima chiude il loop dopo agent.run()
    - La seconda apre un nuovo loop per la pubblicazione
    Questo evita conflitti con asyncpg su macOS ARM (no uvloop).
    """

    @app.task(
        name=f"agents.{agent_name}.run",
        autoretry_for=(TransientError,),
        retry_backoff=True,       # 2s → 4s → 8s → 16s ...
        retry_backoff_max=60,
        max_retries=3,
        bind=True,
    )
    def run(self, task_dict: dict) -> dict:  # noqa: ANN001
        """Wrapper sync → async. Non rendere async questa funzione."""
        task = AgentTask(**task_dict)
        agent = agent_class()

        log.info(
            "worker.task_received",
            agent=agent_name,
            task_id=str(task.id),
            task_type=task.type,
        )

        # Prima chiamata: esegue la logica dell'agente
        result: AgentResult = asyncio.run(agent.run(task))

        # Seconda chiamata separata: pubblica il risultato su Redis
        # (nuovo event loop per evitare conflitti asyncpg su macOS ARM)
        asyncio.run(_publish_result(result))

        return result.model_dump()

    return run


# ── Registrazione dei 12 agenti ───────────────────────────────────────────────
# Import lazy per evitare circolarità e mantenere startup veloce.
# Ogni import porta con sé le dipendenze dell'agente (anthropic, jinja2, ecc.).

from agents.scout.agent               import ScoutAgent               # noqa: E402
from agents.analyst.agent             import AnalystAgent             # noqa: E402
from agents.lead_profiler.agent       import LeadProfilerAgent        # noqa: E402
from agents.design.agent              import DesignAgent              # noqa: E402
from agents.proposal.agent            import ProposalAgent            # noqa: E402
from agents.sales.agent               import SalesAgent               # noqa: E402
from agents.delivery_orchestrator.agent import DeliveryOrchestratorAgent  # noqa: E402
from agents.doc_generator.agent       import DocGeneratorAgent        # noqa: E402
from agents.delivery_tracker.agent    import DeliveryTrackerAgent     # noqa: E402
from agents.account_manager.agent     import AccountManagerAgent      # noqa: E402
from agents.billing.agent             import BillingAgent             # noqa: E402
from agents.support.agent             import SupportAgent             # noqa: E402

scout_task                 = _make_task("scout",                 ScoutAgent)
analyst_task               = _make_task("analyst",               AnalystAgent)
lead_profiler_task         = _make_task("lead_profiler",         LeadProfilerAgent)
design_task                = _make_task("design",                DesignAgent)
proposal_task              = _make_task("proposal",              ProposalAgent)
sales_task                 = _make_task("sales",                 SalesAgent)
delivery_orchestrator_task = _make_task("delivery_orchestrator", DeliveryOrchestratorAgent)
doc_generator_task         = _make_task("doc_generator",         DocGeneratorAgent)
delivery_tracker_task      = _make_task("delivery_tracker",      DeliveryTrackerAgent)
account_manager_task       = _make_task("account_manager",       AccountManagerAgent)
billing_task               = _make_task("billing",               BillingAgent)
support_task               = _make_task("support",               SupportAgent)


# ── Pollers (import dopo registrazione agenti per evitare circolarità) ─────────
from agents.gate_poller  import poll_gates   # noqa: E402, F401
from agents.gmail_poller import poll_gmail   # noqa: E402, F401


# ── Celery Beat — schedule periodici ──────────────────────────────────────────
app.conf.beat_schedule = {
    # Gate Poller: ogni 30 secondi — controlla flag approvazione nei deal
    "gate-poller": {
        "task": "agents.gate_poller",
        "schedule": 30.0,
    },
    # Gmail Poller: ogni 5 minuti — rileva nuove email di supporto in inbox
    "gmail-poller": {
        "task": "agents.gmail_poller",
        "schedule": 300.0,
    },
}

app.conf.beat_scheduler = "celery.beat:PersistentScheduler"
app.conf.beat_schedule_filename = "celerybeat-schedule"
