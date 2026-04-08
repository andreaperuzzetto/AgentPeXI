"""Test unit per agents/delivery_orchestrator/agent.py"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.delivery_orchestrator.agent import DeliveryOrchestratorAgent
from agents.models import AgentToolError, GateNotApprovedError
from tests.fixtures.leads import make_deal, make_lead
from tests.fixtures.tasks import make_task


# ---------------------------------------------------------------------------
# GATE 2 — kickoff non confermato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delivery_orch_gate2_blocked(db_session):
    """DeliveryOrchestrator solleva GateNotApprovedError se kickoff_confirmed=False."""
    deal = make_deal(kickoff_confirmed=False)
    task = make_task(
        agent="delivery_orchestrator",
        payload={"deal_id": str(deal.id), "client_id": str(uuid.uuid4()), "action": "plan"},
    )

    with patch("agents.delivery_orchestrator.agent.get_deal",
               new_callable=AsyncMock, return_value=deal):
        agent = DeliveryOrchestratorAgent()
        with pytest.raises(GateNotApprovedError):
            await agent.execute(task, db_session)


# ---------------------------------------------------------------------------
# Happy path — plan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delivery_orch_plan_creates_deliveries(db_session):
    """DeliveryOrchestrator crea service_deliveries al piano di erogazione."""
    lead = make_lead()
    deal = make_deal(
        service_type="consulting",
        kickoff_confirmed=True,
        proposal_human_approved=True,
        lead_id=lead.id,
    )
    proposal = MagicMock()
    proposal.deliverables_json = {"deliverables": ["report"]}
    proposal.timeline_weeks = 6

    task = make_task(
        agent="delivery_orchestrator",
        payload={"deal_id": str(deal.id), "client_id": str(uuid.uuid4()), "action": "plan", "dry_run": True},
    )

    with patch("agents.delivery_orchestrator.agent.get_deal",
               new_callable=AsyncMock, return_value=deal):
        with patch("agents.delivery_orchestrator.agent.get_lead",
                   new_callable=AsyncMock, return_value=lead):
            with patch("agents.delivery_orchestrator.agent.get_latest_proposal",
                       new_callable=AsyncMock, return_value=proposal):
                with patch("agents.delivery_orchestrator.agent.get_service_deliveries_for_deal",
                           new_callable=AsyncMock, return_value=[]):
                    with patch("agents.delivery_orchestrator.agent.create_service_delivery",
                               new_callable=AsyncMock) as mock_create_sd:
                        with patch("agents.delivery_orchestrator.agent.create_task",
                                   new_callable=AsyncMock):
                            with patch("agents.delivery_orchestrator.agent.update_deal",
                                       new_callable=AsyncMock, return_value=deal):
                                with patch("agents.delivery_orchestrator.agent.get_task_by_idempotency_key",
                                           new_callable=AsyncMock, return_value=None):
                                    agent = DeliveryOrchestratorAgent()
                                    result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# check_progress — consegna completata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delivery_orch_check_progress_all_done(db_session):
    """DeliveryOrchestrator aggiorna deal a 'delivered' quando tutte le delivery sono complete."""
    deal = make_deal(
        service_type="consulting",
        kickoff_confirmed=True,
        proposal_human_approved=True,
        status="in_delivery",
    )

    sd = MagicMock()
    sd.id = uuid.uuid4()
    sd.type = "report"
    sd.status = "approved"
    sd.depends_on = []

    task = make_task(
        agent="delivery_orchestrator",
        payload={"deal_id": str(deal.id), "client_id": str(uuid.uuid4()), "action": "check_progress"},
    )

    with patch("agents.delivery_orchestrator.agent.get_deal",
               new_callable=AsyncMock, return_value=deal):
        with patch("agents.delivery_orchestrator.agent.get_service_deliveries_for_deal",
                   new_callable=AsyncMock, return_value=[sd]):
            with patch("agents.delivery_orchestrator.agent.update_deal",
                       new_callable=AsyncMock, return_value=deal):
                with patch("agents.delivery_orchestrator.agent.create_task",
                           new_callable=AsyncMock):
                    agent = DeliveryOrchestratorAgent()
                    result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Deal non trovato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delivery_orch_deal_not_found(db_session):
    task = make_task(
        agent="delivery_orchestrator",
        payload={"deal_id": str(uuid.uuid4()), "client_id": str(uuid.uuid4()), "action": "plan"},
    )

    with patch("agents.delivery_orchestrator.agent.get_deal",
               new_callable=AsyncMock, return_value=None):
        agent = DeliveryOrchestratorAgent()
        with pytest.raises(AgentToolError) as exc_info:
            await agent.execute(task, db_session)

    assert exc_info.value.code == "tool_db_deal_not_found"
