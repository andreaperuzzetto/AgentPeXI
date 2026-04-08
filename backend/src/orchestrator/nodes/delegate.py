from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime

import redis.asyncio as aioredis

from agents.models import AgentResult, AgentTask
from agents.worker import celery_app
from db.session import get_db_session
from orchestrator.state import AgentState

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_AGENT_RESULT_TIMEOUT = 600  # 10 minuti


async def _await_result(task_id: str) -> AgentResult:
    """
    Attende il risultato di un task Celery pubblicato dal worker su Redis.
    Il worker pubblica su `agent_results:{task_id}` dopo agent.run().
    """
    r = aioredis.from_url(_REDIS_URL)
    pubsub = r.pubsub()
    channel = f"agent_results:{task_id}"
    await pubsub.subscribe(channel)
    try:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _AGENT_RESULT_TIMEOUT
        async for message in pubsub.listen():
            if loop.time() > deadline:
                raise TimeoutError(f"Task {task_id} timed out after {_AGENT_RESULT_TIMEOUT}s")
            if message["type"] == "message":
                data = json.loads(message["data"])
                return AgentResult(**data)
    finally:
        await pubsub.unsubscribe(channel)
        await r.aclose()
    raise TimeoutError(f"Task {task_id}: subscriber loop ended without result")


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
    new_history = _append_task_history(state, task_id, "scout.discover", "scout")

    result = await _await_result(task_id)

    leads: list[dict] = []
    if result.success:
        lead_ids = result.output.get("lead_ids") or []
        leads = [{"id": lid, "analyzed": False} for lid in lead_ids]

    return {
        **state,
        "current_agent": "scout",
        "leads": leads,
        "task_history": new_history,
        **({"error": result.error} if not result.success else {}),
    }


# ── Analyst ───────────────────────────────────────────────────────────────────

async def dispatch_analyst(state: AgentState) -> AgentState:
    # Prende il primo lead non ancora analizzato
    leads = state.get("leads") or []
    unanalyzed = [l for l in leads if not l.get("analyzed")]
    if not unanalyzed:
        return {**state, "current_agent": "analyst"}

    lead = unanalyzed[0]
    sector = (state.get("discovery_payload") or {}).get("sector", "")
    payload = {
        "lead_ids": [lead["id"]],
        "sector": sector,
    }
    task_id = _dispatch("analyst", "analyst.score_lead", payload, state)
    new_history = _append_task_history(state, task_id, "analyst.score_lead", "analyst")

    result = await _await_result(task_id)

    q_ids: list[str] = result.output.get("qualified_lead_ids", []) if result.success else []
    qualified = bool(q_ids)

    # GateNotApprovedError("agent_analyst_no_qualified_leads") significa solo che questo
    # lead è disqualificato: non è un errore di pipeline, si passa al prossimo lead.
    disqualified_soft = (
        not result.success
        and result.error in ("agent_analyst_no_qualified_leads", "GateNotApprovedError")
    )

    # Marca solo i flag, non fa merge dell'intero output nel dict lead
    updated_leads = [
        {**l, "analyzed": True, "qualified": l["id"] in q_ids}
        if l["id"] == lead["id"]
        else l
        for l in leads
    ]
    selected = {"id": q_ids[0], "qualified": True} if qualified else state.get("selected_lead")

    extra: dict = {}
    if not result.success and not disqualified_soft:
        extra["error"] = result.error

    return {
        **state,
        "current_agent": "analyst",
        "leads": updated_leads,
        "selected_lead": selected,
        "analysis": result.output if result.success else state.get("analysis"),
        "task_history": new_history,
        **extra,
    }


# ── Lead Profiler ──────────────────────────────────────────────────────────────

async def dispatch_lead_profiler(state: AgentState) -> AgentState:
    selected = state.get("selected_lead") or {}
    lead_id = selected.get("id")
    if not lead_id:
        return {**state, "error": "no_selected_lead_for_profiler"}

    payload = {"lead_ids": [lead_id]}
    task_id = _dispatch("lead_profiler", "lead_profiler.enrich", payload, state)
    new_history = _append_task_history(state, task_id, "lead_profiler.enrich", "lead_profiler")

    result = await _await_result(task_id)

    deal_id = state.get("deal_id")
    service_type = state.get("service_type")
    updated_lead = selected

    if result.success:
        async with get_db_session() as db:
            from tools.db_tools import create_deal as _create_deal
            from tools.db_tools import get_lead as _get_lead

            lead_obj = await _get_lead(uuid.UUID(lead_id), db)
            if lead_obj:
                updated_lead = {
                    "id": str(lead_obj.id),
                    "qualified": True,
                    "business_name": lead_obj.business_name,
                    "phone": lead_obj.phone,
                    "website_url": lead_obj.website_url,
                    "sector": lead_obj.sector,
                    "city": lead_obj.city,
                    "address": lead_obj.address,
                    "google_place_id": lead_obj.google_place_id,
                    "ateco_code": lead_obj.ateco_code,
                    "company_size": lead_obj.company_size,
                    "social_facebook_url": lead_obj.social_facebook_url,
                    "social_instagram_url": lead_obj.social_instagram_url,
                    "suggested_service_type": lead_obj.suggested_service_type,
                    "estimated_value_eur": lead_obj.estimated_value_eur,
                    "gap_summary": lead_obj.gap_summary,
                }
                service_type = lead_obj.suggested_service_type or service_type
                # Crea il Deal se ancora non esiste (deal_id non presente nello stato)
                if not deal_id and service_type:
                    deal = await _create_deal(lead_obj.id, service_type, db)
                    deal_id = str(deal.id)

    return {
        **state,
        "current_agent": "lead_profiler",
        "selected_lead": updated_lead,
        "deal_id": deal_id,
        "service_type": service_type,
        "task_history": new_history,
        **({"error": result.error} if not result.success else {}),
    }


# ── Design ────────────────────────────────────────────────────────────────────

async def dispatch_design(state: AgentState) -> AgentState:
    selected = state.get("selected_lead") or {}
    lead_id = selected.get("id")
    deal_id = state.get("deal_id")

    payload = {
        "deal_id": deal_id,
        "lead_id": lead_id,
    }
    task_id = _dispatch("design", "design.create_artifacts", payload, state)
    new_history = _append_task_history(state, task_id, "design.create_artifacts", "design")

    result = await _await_result(task_id)

    artifact_paths = (
        result.output.get("artifact_paths", [])
        if result.success
        else (state.get("artifact_paths") or [])
    )
    svc_type = result.output.get("service_type") if result.success else None

    return {
        **state,
        "current_agent": "design",
        "artifact_paths": artifact_paths,
        "service_type": svc_type or state.get("service_type"),
        "task_history": new_history,
        **({"error": result.error} if not result.success else {}),
    }


# ── Proposal ──────────────────────────────────────────────────────────────────

async def dispatch_proposal(state: AgentState) -> AgentState:
    selected = state.get("selected_lead") or {}
    lead_id = selected.get("id")
    deal_id = state.get("deal_id")

    payload = {
        "deal_id": deal_id,
        "lead_id": lead_id,
        "artifact_paths": state.get("artifact_paths") or [],
    }
    task_id = _dispatch("proposal", "proposal.generate", payload, state)
    new_history = _append_task_history(state, task_id, "proposal.generate", "proposal")

    result = await _await_result(task_id)

    proposal_path = (
        result.output.get("pdf_path") if result.success else state.get("proposal_path")
    )
    proposal_version = int(
        result.output.get("proposal_version", state.get("proposal_version") or 1)
        if result.success
        else (state.get("proposal_version") or 1)
    )
    # Conserva proposal_id e altri dati nella analysis per uso successivo (es. sales)
    analysis = dict(state.get("analysis") or {})
    if result.success:
        analysis["proposal_id"] = result.output.get("proposal_id")
        analysis["proposal_service_type"] = result.output.get("service_type")
        analysis["estimated_value_eur"] = result.output.get("estimated_value_eur")

    return {
        **state,
        "current_agent": "proposal",
        "proposal_path": proposal_path,
        "proposal_version": proposal_version,
        "analysis": analysis,
        "task_history": new_history,
        **({"error": result.error} if not result.success else {}),
    }


# ── Sales ─────────────────────────────────────────────────────────────────────

async def dispatch_sales(state: AgentState) -> AgentState:
    selected = state.get("selected_lead") or {}
    lead_id = selected.get("id")
    deal_id = state.get("deal_id")
    analysis = dict(state.get("analysis") or {})

    # Determina l'azione in base allo stato corrente
    sales_action = analysis.get("sales_action_next", "send_proposal")

    payload: dict = {
        "deal_id": deal_id,
        "action": sales_action,
        "lead_id": lead_id,
    }

    if sales_action == "send_proposal":
        proposal_id = analysis.get("proposal_id")
        # contact_email: non disponibile nel Lead (no campo email nel modello).
        # Per ora è stringa vuota — l'agente sales solleverà un errore se mancante.
        # Deve essere iniettato dall'operatore via webhook o campo manuale sul Deal.
        contact_email = selected.get("contact_email", "")
        payload.update({
            "proposal_id": str(proposal_id) if proposal_id else "",
            "contact_email": contact_email,
        })
    elif sales_action == "follow_up":
        payload.update({
            "contact_email": selected.get("contact_email", ""),
            "gmail_thread_id": analysis.get("gmail_thread_id"),
            "follow_up_number": analysis.get("follow_up_number", 1),
        })
    elif sales_action == "handle_response":
        payload.update({
            "client_response": analysis.get("client_response", ""),
            "contact_email": selected.get("contact_email", ""),
            "gmail_thread_id": analysis.get("gmail_thread_id"),
            "negotiation_round": state.get("negotiation_round", 0),
            "client_notes": analysis.get("client_notes", ""),
        })

    task_id = _dispatch("sales", "sales.contact", payload, state)
    new_history = _append_task_history(state, task_id, "sales.contact", "sales")

    result = await _await_result(task_id)

    negotiation_round = state.get("negotiation_round", 0)
    if result.success:
        action_out = result.output.get("action", "")
        if action_out == "send_proposal":
            analysis["sales_outcome"] = "await_response"
            analysis["sales_action_next"] = "follow_up"
            analysis["gmail_thread_id"] = result.output.get("gmail_thread_id")
        elif action_out == "client_approved":
            analysis["sales_outcome"] = "client_approved"
        elif action_out == "client_rejected":
            analysis["sales_outcome"] = "lost"
        elif action_out == "negotiation_response":
            analysis["sales_outcome"] = "negotiating"
            analysis["sales_action_next"] = "handle_response"
            negotiation_round = result.output.get("negotiation_round", negotiation_round)
        else:
            analysis["sales_outcome"] = "await_response"

    return {
        **state,
        "current_agent": "sales",
        "analysis": analysis,
        "negotiation_round": negotiation_round,
        "task_history": new_history,
        **({"error": result.error} if not result.success else {}),
    }


# ── Delivery Orchestrator ──────────────────────────────────────────────────────

async def dispatch_delivery_orchestrator(state: AgentState) -> AgentState:
    milestones = state.get("delivery_milestones") or []
    action = "plan" if not milestones else "check_progress"

    payload = {
        "deal_id": state.get("deal_id"),
        "client_id": state.get("client_id"),
        "action": action,
    }
    task_id = _dispatch("delivery_orchestrator", "delivery_orchestrator.plan", payload, state)
    new_history = _append_task_history(
        state, task_id, "delivery_orchestrator.plan", "delivery_orchestrator"
    )

    result = await _await_result(task_id)

    updated_milestones = milestones
    if result.success:
        ready_ids: list[str] = result.output.get("ready_delivery_ids", [])
        agent_status: str = result.output.get("status", "in_delivery")
        if action == "plan":
            updated_milestones = [{"sd_id": sid, "status": "pending"} for sid in ready_ids]
        elif agent_status != "delivered":
            # Aggiunge eventuali nuove delivery pronte non ancora tracciate
            tracked_ids = {m["sd_id"] for m in milestones}
            new_ms = [
                {"sd_id": sid, "status": "pending"}
                for sid in ready_ids
                if sid not in tracked_ids
            ]
            updated_milestones = milestones + new_ms

    return {
        **state,
        "current_agent": "delivery_orchestrator",
        "delivery_milestones": updated_milestones,
        "task_history": new_history,
        **({"error": result.error} if not result.success else {}),
    }


# ── Doc Generator ──────────────────────────────────────────────────────────────

async def dispatch_doc_generator(state: AgentState) -> AgentState:
    milestones = state.get("delivery_milestones") or []
    # Prima milestone in stato "pending"
    current_ms = next((m for m in milestones if m.get("status") == "pending"), None)
    if not current_ms:
        return {**state, "current_agent": "doc_generator", "error": "no_pending_milestone"}

    sd_id = current_ms["sd_id"]
    payload = {
        "service_delivery_id": sd_id,
        "deal_id": state.get("deal_id"),
        "client_id": state.get("client_id"),
    }
    task_id = _dispatch("doc_generator", "doc_generator.generate", payload, state)
    new_history = _append_task_history(
        state, task_id, "doc_generator.generate", "doc_generator"
    )

    result = await _await_result(task_id)

    updated_milestones = [
        {**m, "status": "in_review"} if m["sd_id"] == sd_id else m
        for m in milestones
    ]
    new_artifacts = result.output.get("artifact_paths", []) if result.success else []
    artifact_paths = (state.get("artifact_paths") or []) + new_artifacts

    return {
        **state,
        "current_agent": "doc_generator",
        "delivery_milestones": updated_milestones,
        "artifact_paths": artifact_paths,
        "task_history": new_history,
        **({"error": result.error} if not result.success else {}),
    }


# ── Delivery Tracker ──────────────────────────────────────────────────────────

async def dispatch_delivery_tracker(state: AgentState) -> AgentState:
    milestones = state.get("delivery_milestones") or []
    # Prima milestone in stato "in_review"
    current_ms = next((m for m in milestones if m.get("status") == "in_review"), None)
    if not current_ms:
        return {**state, "current_agent": "delivery_tracker", "error": "no_in_review_milestone"}

    sd_id = current_ms["sd_id"]
    payload = {
        "service_delivery_id": sd_id,
        "deal_id": state.get("deal_id"),
        "client_id": state.get("client_id"),
    }
    task_id = _dispatch("delivery_tracker", "delivery_tracker.track", payload, state)
    new_history = _append_task_history(
        state, task_id, "delivery_tracker.track", "delivery_tracker"
    )

    result = await _await_result(task_id)

    approved = result.output.get("approved", False) if result.success else False
    # Se rifiutato → torna "pending" per ri-generazione da doc_generator
    new_status = "approved" if approved else "pending"
    updated_milestones = [
        {**m, "status": new_status} if m["sd_id"] == sd_id else m
        for m in milestones
    ]

    analysis = dict(state.get("analysis") or {})
    if result.success:
        analysis["delivery_outcome"] = "approved" if approved else "rejected"

    return {
        **state,
        "current_agent": "delivery_tracker",
        "delivery_milestones": updated_milestones,
        "analysis": analysis,
        "task_history": new_history,
        **({"error": result.error} if not result.success else {}),
    }


# ── Account Manager ───────────────────────────────────────────────────────────

async def dispatch_account_manager(state: AgentState) -> AgentState:
    analysis = state.get("analysis") or {}
    action = analysis.get("account_action_next", "onboarding")

    payload = {
        "deal_id": state.get("deal_id"),
        "client_id": state.get("client_id"),
        "action": action,
    }
    task_id = _dispatch("account_manager", "account_manager.onboard", payload, state)
    new_history = _append_task_history(
        state, task_id, "account_manager.onboard", "account_manager"
    )

    result = await _await_result(task_id)

    return {
        **state,
        "current_agent": "account_manager",
        "task_history": new_history,
        **({"error": result.error} if not result.success else {}),
    }


# ── Billing ───────────────────────────────────────────────────────────────────

async def dispatch_billing(state: AgentState) -> AgentState:
    analysis = state.get("analysis") or {}
    action = analysis.get("billing_action_next", "create_invoice")
    milestone = analysis.get("billing_milestone", "deposit")

    payload = {
        "deal_id": state.get("deal_id"),
        "client_id": state.get("client_id"),
        "action": action,
        "milestone": milestone,
    }
    task_id = _dispatch("billing", "billing.invoice", payload, state)
    new_history = _append_task_history(state, task_id, "billing.invoice", "billing")

    result = await _await_result(task_id)

    return {
        **state,
        "current_agent": "billing",
        "task_history": new_history,
        **({"error": result.error} if not result.success else {}),
    }


# ── Support ───────────────────────────────────────────────────────────────────

async def dispatch_support(state: AgentState) -> AgentState:
    analysis = state.get("analysis") or {}
    action = analysis.get("support_action_next", "classify")

    payload: dict = {
        "deal_id": state.get("deal_id"),
        "client_id": state.get("client_id"),
        "action": action,
    }
    # Campi dipendenti dall'azione
    if action == "classify":
        payload["email_thread_id"] = analysis.get("email_thread_id", "")
    elif action in ("respond", "resolve", "check_sla", "create_intervention"):
        payload["ticket_id"] = analysis.get("ticket_id", "")

    task_id = _dispatch("support", "support.handle", payload, state)
    new_history = _append_task_history(state, task_id, "support.handle", "support")

    result = await _await_result(task_id)

    return {
        **state,
        "current_agent": "support",
        "task_history": new_history,
        **({"error": result.error} if not result.success else {}),
    }
