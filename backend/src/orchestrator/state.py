from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # Identificatori run
    run_id: str
    deal_id: str | None
    client_id: str | None
    service_type: str | None  # "consulting" | "web_design" | "digital_maintenance"

    # Posizione nella pipeline
    current_phase: str  # "discovery" | "proposal" | "delivery" | "post_sale"
    current_agent: str

    # Messaggi LangGraph (append-only)
    messages: Annotated[list, add_messages]

    # Storico task eseguiti in questo run
    task_history: list[dict]  # [{task_id, type, agent, status, completed_at}]

    # Accumulatori fase discovery
    leads: list[dict]
    selected_lead: dict | None
    analysis: dict | None
    discovery_payload: dict  # payload specifico per fase discovery, iniettato dall'API

    # Artefatti (mockup, presentazioni, schemi, roadmap)
    artifact_paths: list[str]

    # Fase proposal
    proposal_path: str | None
    proposal_version: int

    # Fase delivery
    delivery_milestones: list[dict]  # [{sd_id, type, title, status, milestone_name}]
    delivery_progress_pct: int | None

    # Gate
    awaiting_gate: bool
    gate_type: str | None  # "proposal_review" | "kickoff" | "delivery"

    # Contatori iterazione
    proposal_rejection_count: int
    negotiation_round: int

    # Errori
    error: str | None
    retry_count: int
