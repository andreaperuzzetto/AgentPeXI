from __future__ import annotations

from langgraph.graph import END

from orchestrator.state import AgentState

# Soglie
MAX_PROPOSAL_REJECTIONS = 5
MAX_NEGOTIATION_ROUNDS = 2


# ── Entry point ───────────────────────────────────────────────────────────────

async def route_phase(state: AgentState) -> AgentState:
    """
    Nodo entry point del grafo. Non modifica lo stato — serve da punto di routing
    per decide_next_node() che determina il nodo successivo.
    """
    return state


def decide_next_node(state: AgentState) -> str:
    """
    Determina il nodo successivo in base alla fase corrente e allo stato del run.
    Usato come conditional edge da 'route_phase'.
    """
    if state.get("error"):
        return "error"

    if state.get("awaiting_gate"):
        return "awaiting_gate"

    phase = state.get("current_phase", "discovery")
    if phase == "discovery":
        return "discovery"
    if phase == "proposal":
        return "proposal"
    if phase == "delivery":
        return "delivery"
    if phase == "post_sale":
        return "post_sale"

    return END


# ── After Scout ───────────────────────────────────────────────────────────────

def after_scout(state: AgentState) -> str:
    """Routing dopo dispatch_scout."""
    if state.get("error"):
        return "error"
    leads = state.get("leads") or []
    if not leads:
        return "blocked"
    return "qualify"


# ── After Analyst ─────────────────────────────────────────────────────────────

def after_analyst(state: AgentState) -> str:
    """Routing dopo dispatch_analyst."""
    if state.get("error"):
        return "error"

    selected = state.get("selected_lead")
    if selected and selected.get("qualified"):
        return "qualified"

    leads = state.get("leads") or []
    # controlla se ci sono lead non ancora analizzati
    unanalyzed = [l for l in leads if not l.get("analyzed")]
    if unanalyzed:
        return "next_lead"

    # tutti disqualificati
    analysis = state.get("analysis")
    if analysis and analysis.get("disqualified"):
        return "disqualified"

    return "no_leads"


# ── After Gate Proposal ───────────────────────────────────────────────────────

def after_gate_proposal(state: AgentState) -> str:
    """Routing dopo gate_proposal_review (GATE 1)."""
    if not state.get("awaiting_gate"):
        # gate approvato oppure rifiutato
        gate_outcome = (state.get("analysis") or {}).get("gate_outcome")
        if state.get("proposal_rejection_count", 0) >= MAX_PROPOSAL_REJECTIONS:
            return "max_rejections"
        if gate_outcome == "rejected":
            return "rejected"
        return "approved"

    # ancora in attesa
    return "approved"  # verrà ri-valutato al resume


# ── After Sales ───────────────────────────────────────────────────────────────

def after_sales(state: AgentState) -> str:
    """Routing dopo dispatch_sales."""
    if state.get("error"):
        return "error"

    outcome = (state.get("analysis") or {}).get("sales_outcome", "")

    if outcome == "client_approved":
        return "client_approved"

    if outcome == "lost":
        return "lost"

    if outcome == "negotiating":
        if state.get("negotiation_round", 0) >= MAX_NEGOTIATION_ROUNDS:
            return "max_negotiation"
        return "negotiating"

    if outcome == "await_response":
        return "await_response"

    # default: considerare in attesa di risposta
    return "await_response"


# ── After Delivery Orch ───────────────────────────────────────────────────────

def after_delivery_orch(state: AgentState) -> str:
    """Routing dopo dispatch_delivery_orchestrator."""
    if state.get("error"):
        return "error"

    milestones = state.get("delivery_milestones") or []
    pending = [m for m in milestones if m.get("status") not in ("completed", "approved")]

    if not milestones:
        return "blocked"

    if pending:
        return "next_delivery"

    return "all_done"


# ── After Delivery Tracker ────────────────────────────────────────────────────

def after_delivery_tracker(state: AgentState) -> str:
    """Routing dopo dispatch_delivery_tracker."""
    if state.get("error"):
        return "error"

    outcome = (state.get("analysis") or {}).get("delivery_outcome", "")
    if outcome == "rejected":
        return "rejected"

    return "approved"


# ── After Gate Delivery ───────────────────────────────────────────────────────

def after_gate_delivery(state: AgentState) -> str:
    """Routing dopo gate_delivery (GATE 3)."""
    if state.get("awaiting_gate"):
        return "blocked"
    return "approved"


# ── Error / Blocked ───────────────────────────────────────────────────────────

async def handle_error(state: AgentState) -> AgentState:
    """Nodo terminale per errori non recuperabili."""
    return {
        **state,
        "current_agent": "error_handler",
        "error": state.get("error") or "unknown_error",
    }


async def handle_blocked(state: AgentState) -> AgentState:
    """Nodo terminale per situazioni bloccate (no leads, max rejections, ecc.)."""
    return {
        **state,
        "current_agent": "blocked_handler",
    }
