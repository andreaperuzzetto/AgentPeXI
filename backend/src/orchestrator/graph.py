from __future__ import annotations

import os

from langgraph.graph import END, StateGraph

from orchestrator.nodes import checkpoint, delegate, gates, router
from orchestrator.state import AgentState


def get_checkpointer():
    """
    Checkpointer PostgreSQL per persistere lo stato LangGraph tra gate umani.
    Usa langgraph-checkpoint-postgres (driver psycopg, non psycopg2).

    Le tabelle del checkpointer sono gestite internamente da LangGraph.
    Eseguire checkpointer.setup() alla prima partenza dell'app.

    DATABASE_SYNC_URL esempio:
        postgresql://agentpexi:changeme@localhost:5432/agentpexi
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    postgres_url = os.environ["DATABASE_SYNC_URL"]
    return AsyncPostgresSaver.from_conn_string(postgres_url)


def build_graph() -> StateGraph:
    """
    Costruisce e compila il grafo LangGraph principale di AgentPeXI.

    Flusso canonico:
        Discovery  → Scout → Analyst → Lead Profiler
        Proposal   → Design → Proposal → GATE 1 → Sales
        Delivery   → GATE 2 → Delivery Orch → Doc Generator → Delivery Tracker → GATE 3
        Post-Sale  → Account Manager → Billing

    I gate terminano subito e persistono su runs.status = 'awaiting_gate'.
    Il resume è demandato al Gate Poller (Celery Beat, ogni 30 s).
    """
    g = StateGraph(AgentState)

    # ── Nodi ──────────────────────────────────────────────────────────────────
    g.add_node("route_phase", router.route_phase)
    g.add_node("dispatch_scout", delegate.dispatch_scout)
    g.add_node("dispatch_analyst", delegate.dispatch_analyst)
    g.add_node("dispatch_lead_profiler", delegate.dispatch_lead_profiler)
    g.add_node("dispatch_design", delegate.dispatch_design)
    g.add_node("dispatch_proposal", delegate.dispatch_proposal)
    g.add_node("gate_proposal_review", gates.await_proposal_review)  # GATE 1
    g.add_node("dispatch_sales", delegate.dispatch_sales)
    g.add_node("gate_kickoff", gates.await_kickoff)  # GATE 2
    g.add_node("dispatch_delivery_orch", delegate.dispatch_delivery_orchestrator)
    g.add_node("dispatch_doc_generator", delegate.dispatch_doc_generator)
    g.add_node("dispatch_delivery_tracker", delegate.dispatch_delivery_tracker)
    g.add_node("gate_delivery", gates.await_delivery_approval)  # GATE 3
    g.add_node("dispatch_account_manager", delegate.dispatch_account_manager)
    g.add_node("dispatch_billing", delegate.dispatch_billing)
    g.add_node("dispatch_support", delegate.dispatch_support)
    g.add_node("handle_error", router.handle_error)
    g.add_node("handle_blocked", router.handle_blocked)

    # ── Entry point ────────────────────────────────────────────────────────────
    g.set_entry_point("route_phase")

    # ── Edge routing principale ────────────────────────────────────────────────
    g.add_conditional_edges(
        "route_phase",
        router.decide_next_node,
        {
            "discovery": "dispatch_scout",
            "proposal": "dispatch_design",
            "delivery": "dispatch_delivery_orch",
            "post_sale": "dispatch_account_manager",
            "awaiting_gate": "gate_proposal_review",
            "error": "handle_error",
            "blocked": "handle_blocked",
            END: END,
        },
    )

    # ── Pipeline Discovery ─────────────────────────────────────────────────────
    g.add_conditional_edges(
        "dispatch_scout",
        router.after_scout,
        {
            "qualify": "dispatch_analyst",
            "blocked": "handle_blocked",
            "error": "handle_error",
        },
    )
    g.add_conditional_edges(
        "dispatch_analyst",
        router.after_analyst,
        {
            "qualified": "dispatch_lead_profiler",
            "disqualified": END,
            "next_lead": "dispatch_analyst",
            "no_leads": "handle_blocked",
            "error": "handle_error",
        },
    )
    g.add_edge("dispatch_lead_profiler", "dispatch_design")

    # ── Pipeline Proposal ──────────────────────────────────────────────────────
    g.add_edge("dispatch_design", "dispatch_proposal")
    g.add_edge("dispatch_proposal", "gate_proposal_review")

    g.add_conditional_edges(
        "gate_proposal_review",
        router.after_gate_proposal,
        {
            "approved": "dispatch_sales",
            "rejected": "dispatch_design",
            "max_rejections": "handle_blocked",
        },
    )

    g.add_conditional_edges(
        "dispatch_sales",
        router.after_sales,
        {
            "client_approved": "gate_kickoff",
            "negotiating": "dispatch_sales",
            "max_negotiation": "handle_blocked",
            "lost": END,
            "await_response": "gate_proposal_review",
        },
    )

    # ── Pipeline Delivery ──────────────────────────────────────────────────────
    g.add_edge("gate_kickoff", "dispatch_delivery_orch")

    g.add_conditional_edges(
        "dispatch_delivery_orch",
        router.after_delivery_orch,
        {
            "next_delivery": "dispatch_doc_generator",
            "all_done": "gate_delivery",
            "blocked": "handle_blocked",
            "error": "handle_error",
        },
    )
    g.add_edge("dispatch_doc_generator", "dispatch_delivery_tracker")

    g.add_conditional_edges(
        "dispatch_delivery_tracker",
        router.after_delivery_tracker,
        {
            "approved": "dispatch_delivery_orch",
            "rejected": "dispatch_doc_generator",
            "error": "handle_error",
        },
    )

    g.add_conditional_edges(
        "gate_delivery",
        router.after_gate_delivery,
        {
            "approved": "dispatch_account_manager",
            "blocked": "handle_blocked",
        },
    )

    # ── Pipeline Post-Sale ─────────────────────────────────────────────────────
    g.add_edge("dispatch_account_manager", "dispatch_billing")
    g.add_edge("dispatch_billing", END)

    return g.compile(checkpointer=get_checkpointer())
