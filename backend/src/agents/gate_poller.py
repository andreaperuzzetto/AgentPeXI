"""
Gate Poller — Celery Beat task (ogni 30 secondi).

Interroga `runs` per run in stato 'awaiting_gate', verifica il flag corrispondente
nel deal e riprende il grafo LangGraph se il gate è stato approvato dall'operatore.

L'unico entry point per il resume dei gate è questo poller.
Non riprendere mai i gate dall'handler API — l'API aggiorna solo il flag nel deal.
"""
from __future__ import annotations

import asyncio
import structlog

from celery import shared_task
from sqlalchemy import text

from db.session import get_db_session

log = structlog.get_logger()

# Mappa gate_type → colonna del deal da verificare
_GATE_FLAG_MAP: dict[str, str] = {
    "proposal_review": "proposal_human_approved",
    "kickoff":         "kickoff_confirmed",
    "delivery":        "delivery_approved",
}


@shared_task(name="agents.gate_poller")
def poll_gates() -> None:
    """Entry point Celery Beat — sync wrapper. Non rendere async."""
    asyncio.run(_poll_gates_async())


async def _poll_gates_async() -> None:
    """Interroga runs.status='awaiting_gate' e riprende i run con gate approvato."""
    async with get_db_session() as db:
        result = await db.execute(
            text(
                "SELECT run_id, deal_id, gate_type "
                "FROM runs "
                "WHERE status = 'awaiting_gate' AND deleted_at IS NULL"
            )
        )
        pending = result.fetchall()

    if not pending:
        return

    log.info("gate_poller.checking", pending_count=len(pending))

    for row in pending:
        run_id: str = row[0]
        deal_id: str = row[1]
        gate_type: str = row[2]
        try:
            await _try_resume(run_id, deal_id, gate_type)
        except Exception as exc:
            # Non bloccare il ciclo su un errore singolo — logga e continua
            log.error(
                "gate_poller.resume_error",
                run_id=run_id,
                gate_type=gate_type,
                error=str(exc),
            )


async def _try_resume(run_id: str, deal_id: str, gate_type: str) -> None:
    """
    Verifica se il gate per questo run è stato approvato.
    Se sì, riprende il grafo e aggiorna runs.status.
    """
    async with get_db_session() as db:
        # Ottieni service_type del deal (necessario per GATE 3 consulenza)
        svc_row = await db.execute(
            text("SELECT service_type FROM deals WHERE id = :deal_id AND deleted_at IS NULL"),
            {"deal_id": deal_id},
        )
        service_type: str | None = svc_row.scalar_one_or_none()

        # Per consulenza il GATE 3 usa consulting_approved invece di delivery_approved
        if gate_type == "delivery" and service_type == "consulting":
            flag_col = "consulting_approved"
        else:
            flag_col = _GATE_FLAG_MAP.get(gate_type, "delivery_approved")

        # Leggi il flag di approvazione
        flag_row = await db.execute(
            text(f"SELECT {flag_col} FROM deals WHERE id = :deal_id AND deleted_at IS NULL"),
            {"deal_id": deal_id},
        )
        approved: bool | None = flag_row.scalar_one_or_none()

    if not approved:
        # Gate ancora chiuso — nessuna azione, riprova al prossimo ciclo
        return

    log.info(
        "gate_poller.resuming",
        run_id=run_id,
        deal_id=deal_id,
        gate_type=gate_type,
        flag_col=flag_col,
    )

    # Riprendi il grafo LangGraph dal checkpoint
    # Import lazy per evitare circolarità all'avvio del worker
    from orchestrator.graph import build_graph  # noqa: PLC0415

    graph = build_graph()
    config = {"configurable": {"thread_id": run_id}}
    await graph.ainvoke(None, config=config)

    # Aggiorna runs.status → 'running'
    async with get_db_session() as db:
        await db.execute(
            text(
                "UPDATE runs SET status = 'running', updated_at = now() "
                "WHERE run_id = :rid AND deleted_at IS NULL"
            ),
            {"rid": run_id},
        )
        await db.commit()

    log.info("gate_poller.resumed", run_id=run_id, gate_type=gate_type)
