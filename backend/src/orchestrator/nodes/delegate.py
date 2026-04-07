from __future__ import annotations

import uuid
from datetime import datetime

from agents.models import AgentTask
from agents.worker import celery_app
from orchestrator.state import AgentState


def _dispatch(agent: str, task_type: str, payload: dict, state: AgentState) -> str:
    """
    Crea un AgentTask, lo invia via Celery e restituisce il task_id come stringa.
    Inietta sempre run_id nel payload. Non esegue mai l'agente direttamente.
    """
    task_id = uuid.uuid4()
    task = AgentTask(
        id=task_id,
        type=task_type,
        agent=agent,
        deal_id=state["deal_id"],
        client_id=state["client_id"],
        payload={**payload, "run_id": state["run_id"]},
    )
    celery_app.send_task(
        f"agents.{agent}.run",
        args=[task.model_dump(mode="json")],
        task_id=str(task_id),
    )
    return str(task_id)


def _append_task_history(state: AgentState, task_id: str, task_type: str, agent: str) -> list[dict]:
    entry = {
        "task_id": task_id,
        "type": task_type,
        "agent": agent,
        "status": "pending",
        "dispatched_at": datetime.utcnow().isoformat(),
    }
    return [*(state.get("task_history") or []), entry]


# ── Scout ─────────────────────────────────────────────────────────────────────

async def dispatch_scout(state: AgentState) -> AgentState:
    payload = state.get("discovery_payload") or {}
    task_id = _dispatch("scout", "scout.discover", payload, state)
    return {
        **state,
        "current_agent": "scout",
        "task_history": _append_task_history(state, task_id, "scout.discover", "scout"),
    }


# ── Analyst ───────────────────────────────────────────────────────────────────

async def dispatch_analyst(state: AgentState) -> AgentState:
    payload = {
        "leads": state.get("leads") or [],
        "selected_lead": state.get("selected_lead"),
    }
    task_id = _dispatch("analyst", "analyst.analyze", payload, state)
    return {
        **state,
        "current_agent": "analyst",
        "task_history": _append_task_history(state, task_id, "analyst.analyze", "analyst"),
    }


# ── Lead Profiler ──────────────────────────────────────────────────────────────

async def dispatch_lead_profiler(state: AgentState) -> AgentState:
    payload = {
        "selected_lead": state.get("selected_lead"),
        "analysis": state.get("analysis"),
    }
    task_id = _dispatch("lead_profiler", "lead_profiler.enrich", payload, state)
    return {
        **state,
        "current_agent": "lead_profiler",
        "task_history": _append_task_history(state, task_id, "lead_profiler.enrich", "lead_profiler"),
    }


# ── Design ────────────────────────────────────────────────────────────────────

async def dispatch_design(state: AgentState) -> AgentState:
    payload = {
        "selected_lead": state.get("selected_lead"),
        "analysis": state.get("analysis"),
        "service_type": state.get("service_type"),
        "proposal_rejection_count": state.get("proposal_rejection_count", 0),
    }
    task_id = _dispatch("design", "design.create_artifacts", payload, state)
    return {
        **state,
        "current_agent": "design",
        "task_history": _append_task_history(state, task_id, "design.create_artifacts", "design"),
    }


# ── Proposal ──────────────────────────────────────────────────────────────────

async def dispatch_proposal(state: AgentState) -> AgentState:
    payload = {
        "selected_lead": state.get("selected_lead"),
        "analysis": state.get("analysis"),
        "artifact_paths": state.get("artifact_paths") or [],
        "service_type": state.get("service_type"),
        "proposal_version": state.get("proposal_version", 1),
    }
    task_id = _dispatch("proposal", "proposal.generate", payload, state)
    return {
        **state,
        "current_agent": "proposal",
        "task_history": _append_task_history(state, task_id, "proposal.generate", "proposal"),
    }


# ── Sales ─────────────────────────────────────────────────────────────────────

async def dispatch_sales(state: AgentState) -> AgentState:
    payload = {
        "selected_lead": state.get("selected_lead"),
        "proposal_path": state.get("proposal_path"),
        "negotiation_round": state.get("negotiation_round", 0),
        "service_type": state.get("service_type"),
    }
    task_id = _dispatch("sales", "sales.contact", payload, state)
    return {
        **state,
        "current_agent": "sales",
        "task_history": _append_task_history(state, task_id, "sales.contact", "sales"),
    }


# ── Delivery Orchestrator ──────────────────────────────────────────────────────

async def dispatch_delivery_orchestrator(state: AgentState) -> AgentState:
    payload = {
        "service_type": state.get("service_type"),
        "delivery_milestones": state.get("delivery_milestones") or [],
        "delivery_progress_pct": state.get("delivery_progress_pct"),
    }
    task_id = _dispatch("delivery_orchestrator", "delivery_orchestrator.plan", payload, state)
    return {
        **state,
        "current_agent": "delivery_orchestrator",
        "task_history": _append_task_history(
            state, task_id, "delivery_orchestrator.plan", "delivery_orchestrator"
        ),
    }


# ── Doc Generator ──────────────────────────────────────────────────────────────

async def dispatch_doc_generator(state: AgentState) -> AgentState:
    payload = {
        "service_type": state.get("service_type"),
        "delivery_milestones": state.get("delivery_milestones") or [],
        "artifact_paths": state.get("artifact_paths") or [],
    }
    task_id = _dispatch("doc_generator", "doc_generator.generate", payload, state)
    return {
        **state,
        "current_agent": "doc_generator",
        "task_history": _append_task_history(
            state, task_id, "doc_generator.generate", "doc_generator"
        ),
    }


# ── Delivery Tracker ──────────────────────────────────────────────────────────

async def dispatch_delivery_tracker(state: AgentState) -> AgentState:
    payload = {
        "service_type": state.get("service_type"),
        "delivery_milestones": state.get("delivery_milestones") or [],
        "delivery_progress_pct": state.get("delivery_progress_pct"),
    }
    task_id = _dispatch("delivery_tracker", "delivery_tracker.track", payload, state)
    return {
        **state,
        "current_agent": "delivery_tracker",
        "task_history": _append_task_history(
            state, task_id, "delivery_tracker.track", "delivery_tracker"
        ),
    }


# ── Account Manager ───────────────────────────────────────────────────────────

async def dispatch_account_manager(state: AgentState) -> AgentState:
    payload = {
        "service_type": state.get("service_type"),
        "delivery_milestones": state.get("delivery_milestones") or [],
    }
    task_id = _dispatch("account_manager", "account_manager.onboard", payload, state)
    return {
        **state,
        "current_agent": "account_manager",
        "task_history": _append_task_history(
            state, task_id, "account_manager.onboard", "account_manager"
        ),
    }


# ── Billing ───────────────────────────────────────────────────────────────────

async def dispatch_billing(state: AgentState) -> AgentState:
    payload = {
        "service_type": state.get("service_type"),
    }
    task_id = _dispatch("billing", "billing.invoice", payload, state)
    return {
        **state,
        "current_agent": "billing",
        "task_history": _append_task_history(state, task_id, "billing.invoice", "billing"),
    }


# ── Support ───────────────────────────────────────────────────────────────────

async def dispatch_support(state: AgentState) -> AgentState:
    payload = {
        "service_type": state.get("service_type"),
    }
    task_id = _dispatch("support", "support.handle", payload, state)
    return {
        **state,
        "current_agent": "support",
        "task_history": _append_task_history(state, task_id, "support.handle", "support"),
    }
