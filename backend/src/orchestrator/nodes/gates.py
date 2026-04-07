from __future__ import annotations

import datetime

from sqlalchemy import text

from db.session import get_db_session
from orchestrator.state import AgentState


async def _write_runs_awaiting(run_id: str, gate_type: str) -> None:
    """Scrive in runs: status='awaiting_gate', gate_type, awaiting_gate_since, updated_at."""
    now = datetime.datetime.utcnow()
    async with get_db_session() as db:
        await db.execute(
            text(
                """
                UPDATE runs
                SET status = 'awaiting_gate',
                    gate_type = :gate_type,
                    awaiting_gate_since = :now,
                    updated_at = :now
                WHERE run_id = :run_id
                """
            ),
            {"run_id": run_id, "gate_type": gate_type, "now": now},
        )
        await db.commit()


async def await_proposal_review(state: AgentState) -> AgentState:
    """
    GATE 1 — Scrive runs.status='awaiting_gate' e termina immediatamente.
    Il Gate Poller (Celery Beat) riprende quando deal.proposal_human_approved = true.
    Non resta appeso in memoria, non fa polling.
    """
    await _write_runs_awaiting(state["run_id"], "proposal_review")
    return {**state, "awaiting_gate": True, "gate_type": "proposal_review"}


async def await_kickoff(state: AgentState) -> AgentState:
    """
    GATE 2 — Scrive runs.status='awaiting_gate' e termina immediatamente.
    Il Gate Poller riprende quando deal.kickoff_confirmed = true.
    """
    await _write_runs_awaiting(state["run_id"], "kickoff")
    return {**state, "awaiting_gate": True, "gate_type": "kickoff"}


async def await_delivery_approval(state: AgentState) -> AgentState:
    """
    GATE 3 — Scrive runs.status='awaiting_gate' e termina immediatamente.
    Il Gate Poller riprende quando deal.delivery_approved = true
    (o deal.consulting_approved per service_type='consulting').
    """
    await _write_runs_awaiting(state["run_id"], "delivery")
    return {**state, "awaiting_gate": True, "gate_type": "delivery"}
