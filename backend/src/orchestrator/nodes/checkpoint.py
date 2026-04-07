from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.state import AgentState
from tools.db_tools import get_deal


async def check_gate_proposal(state: AgentState, db: AsyncSession) -> bool:
    """Verifica GATE 1: proposal_human_approved. Legge sempre da DB."""
    deal = await get_deal(UUID(state["deal_id"]), db)
    return deal is not None and deal.proposal_human_approved is True


async def check_gate_kickoff(state: AgentState, db: AsyncSession) -> bool:
    """Verifica GATE 2: kickoff_confirmed. Legge sempre da DB."""
    deal = await get_deal(UUID(state["deal_id"]), db)
    return deal is not None and deal.kickoff_confirmed is True


async def check_gate_delivery(state: AgentState, db: AsyncSession) -> bool:
    """
    Verifica GATE 3. Legge sempre da DB, mai da state.
    - consulting           → deal.consulting_approved
    - web_design /
      digital_maintenance  → deal.delivery_approved
    """
    deal = await get_deal(UUID(state["deal_id"]), db)
    if deal is None:
        return False
    if deal.service_type == "consulting":
        return deal.consulting_approved is True
    return deal.delivery_approved is True
