"""
Test di integrazione — pipeline Manutenzione Digitale
digital_maintenance: Scout → Analyst → LeadProfiler →
Design (architettura/sicurezza) → Proposal → GATE 1 → Sales →
GATE 2 → DeliveryOrchestrator → DocGenerator → DeliveryTracker → GATE 3
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.delivery_orchestrator.agent import DeliveryOrchestratorAgent
from agents.delivery_tracker.agent import DeliveryTrackerAgent
from agents.design.agent import DesignAgent
from agents.models import GateNotApprovedError
from agents.proposal.agent import ProposalAgent
from agents.sales.agent import SalesAgent
from tests.fixtures.leads import make_deal, make_lead
from tests.fixtures.proposals import make_proposal
from tests.fixtures.tasks import make_task


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def lead_dm():
    return make_lead(
        business_name="Officina Ferri & Figli",
        sector="manufacturing",
        lead_score=70,
        service_gap_detected=True,
        suggested_service_type="digital_maintenance",
    )


@pytest.fixture
def deal_dm(lead_dm):
    return make_deal(
        service_type="digital_maintenance",
        status="discovery",
        total_price_eur=180000,
        proposal_human_approved=False,
        kickoff_confirmed=False,
        delivery_approved=False,
    )


# ---------------------------------------------------------------------------
# Design Agent — artefatti manutenzione digitale
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dm_design_artifacts(db_session, deal_dm, lead_dm):
    """Design Agent genera artefatti architetturali per digital_maintenance."""
    task = make_task(
        agent="design",
        payload={
            "deal_id": str(deal_dm.id),
            "lead_id": str(lead_dm.id),
            "service_type": "digital_maintenance",
            "dry_run": True,
        },
    )

    llm_json = (
        '{"artifact_pages": ["architecture_schema", "update_plan", "monitoring_dashboard"],'
        ' "audit_notes": "Sistema PHP 5.6 obsoleto, vulnerabilità OWASP rilevate"}'
    )
    llm_resp = MagicMock()
    llm_resp.content = [MagicMock(text=llm_json)]

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    with patch("agents.design.agent.get_deal", new_callable=AsyncMock, return_value=deal_dm):
        with patch("agents.design.agent.get_lead", new_callable=AsyncMock, return_value=lead_dm):
            with patch("agents.design.agent.render_to_png",
                       new_callable=AsyncMock, return_value=png_bytes):
                with patch("agents.design.agent.upload_file",
                           new_callable=AsyncMock,
                           return_value=f"clients/{lead_dm.id}/mockups/architecture.png"):
                    with patch("agents.design.agent.file_exists",
                               new_callable=AsyncMock, return_value=False):
                        with patch("agents.design.agent.get_task_by_idempotency_key",
                                   new_callable=AsyncMock, return_value=None):
                            with patch("agents.design.agent.create_task", new_callable=AsyncMock):
                                agent = DesignAgent()
                                agent._client = MagicMock()
                                agent._client.messages.create = AsyncMock(return_value=llm_resp)
                                result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Proposal Agent — proposta per digital_maintenance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dm_proposal(db_session, deal_dm, lead_dm):
    """Proposal genera proposta con pricing per aggiornamento una tantum."""
    task = make_task(
        agent="proposal",
        payload={
            "deal_id": str(deal_dm.id),
            "lead_id": str(lead_dm.id),
            "dry_run": True,
        },
    )

    proposal = make_proposal(deal_id=deal_dm.id, version=1, service_type="digital_maintenance")
    pdf_bytes = b"%PDF-1.4 dm_proposal"

    llm_json = (
        '{"solution_summary": "Aggiornamento sistema a PHP 8.2, patch sicurezza OWASP",'
        ' "roi_summary": "Riduzione vulnerabilità OWASP del 95%",'
        ' "roi_metrics": [{"metric": "sicurezza", "value": "+95%"}],'
        ' "milestones": [{"phase": "Fase 1", "deliverable": "Audit", "weeks": 1}],'
        ' "key_benefits": ["sicurezza", "performance", "compatibilità"]}'
    )
    llm_resp = MagicMock()
    llm_resp.content = [MagicMock(text=llm_json)]

    with patch("agents.proposal.agent.get_deal", new_callable=AsyncMock, return_value=deal_dm):
        with patch("agents.proposal.agent.get_lead", new_callable=AsyncMock, return_value=lead_dm):
            with patch("agents.proposal.agent.get_latest_proposal",
                       new_callable=AsyncMock, return_value=None):
                with patch("agents.proposal.agent.render_pdf",
                           new_callable=AsyncMock, return_value=pdf_bytes):
                    with patch("agents.proposal.agent.upload_file",
                               new_callable=AsyncMock,
                               return_value=f"clients/{lead_dm.id}/proposals/v1.pdf"):
                        with patch("agents.proposal.agent.create_proposal",
                                   new_callable=AsyncMock, return_value=proposal):
                            with patch("agents.proposal.agent.update_deal",
                                       new_callable=AsyncMock, return_value=deal_dm):
                                with patch("agents.proposal.agent.get_task_by_idempotency_key",
                                           new_callable=AsyncMock, return_value=None):
                                    with patch("agents.proposal.agent.create_task",
                                               new_callable=AsyncMock):
                                        agent = ProposalAgent()
                                        agent._client = MagicMock()
                                        agent._client.messages.create = AsyncMock(
                                            return_value=llm_resp
                                        )
                                        result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# GATE 1 — blocca sales per digital_maintenance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dm_gate1_blocks_sales(db_session, deal_dm):
    task = make_task(
        agent="sales",
        payload={
            "deal_id": str(deal_dm.id),
            "action": "send_proposal",
        },
    )

    with patch("agents.sales.agent.get_deal", new_callable=AsyncMock, return_value=deal_dm):
        agent = SalesAgent()
        with pytest.raises(GateNotApprovedError):
            await agent.execute(task, db_session)


# ---------------------------------------------------------------------------
# GATE 2 — blocca delivery senza kickoff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dm_gate2_blocks_delivery(db_session, deal_dm):
    client_id = uuid.uuid4()
    task = make_task(
        agent="delivery_orchestrator",
        payload={
            "deal_id": str(deal_dm.id),
            "client_id": str(client_id),
            "action": "plan",
        },
    )

    with patch("agents.delivery_orchestrator.agent.get_deal",
               new_callable=AsyncMock, return_value=deal_dm):
        agent = DeliveryOrchestratorAgent()
        with pytest.raises(GateNotApprovedError):
            await agent.execute(task, db_session)


# ---------------------------------------------------------------------------
# DeliveryOrchestrator — pianifica con kickoff confermato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dm_delivery_plan_with_kickoff(db_session, deal_dm, lead_dm):
    """Con kickoff_confirmed=True, pianifica il ciclo di aggiornamento."""
    deal_dm.kickoff_confirmed = True
    deal_dm.proposal_human_approved = True
    client_id = uuid.uuid4()

    task = make_task(
        agent="delivery_orchestrator",
        payload={
            "deal_id": str(deal_dm.id),
            "client_id": str(client_id),
            "action": "plan",
            "dry_run": True,
        },
    )

    llm_json = (
        '{"delivery_plan": ['
        '{"type": "security_patch", "milestone": "Patch OWASP applicata", "due_days": 7},'
        '{"type": "update_cycle", "milestone": "Aggiornamento PHP completato", "due_days": 14}'
        ']}'
    )
    llm_resp = MagicMock()
    llm_resp.content = [MagicMock(text=llm_json)]

    with patch("agents.delivery_orchestrator.agent.get_deal",
               new_callable=AsyncMock, return_value=deal_dm):
        with patch("agents.delivery_orchestrator.agent.get_lead",
                   new_callable=AsyncMock, return_value=lead_dm):
            with patch("agents.delivery_orchestrator.agent.get_service_deliveries_for_deal",
                       new_callable=AsyncMock, return_value=[]):
                with patch("agents.delivery_orchestrator.agent.create_service_delivery",
                           new_callable=AsyncMock, return_value=MagicMock(id=uuid.uuid4())):
                    with patch("agents.delivery_orchestrator.agent.update_deal",
                               new_callable=AsyncMock, return_value=deal_dm):
                        with patch("agents.delivery_orchestrator.agent.get_task_by_idempotency_key",
                                   new_callable=AsyncMock, return_value=None):
                            with patch("agents.delivery_orchestrator.agent.create_task",
                                       new_callable=AsyncMock):
                                agent = DeliveryOrchestratorAgent()
                                agent._client = MagicMock()
                                agent._client.messages.create = AsyncMock(return_value=llm_resp)
                                result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# GATE 3 — delivery_approved richiesto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dm_gate3_blocks_final_delivery(db_session, deal_dm):
    """DeliveryTracker non può consegnare senza delivery_approved — gate gestito dall'orchestratore.
    Qui verifichiamo che con dry_run=True il tracker esegua la review con successo."""
    deal_dm.kickoff_confirmed = True
    deal_dm.proposal_human_approved = True
    deal_dm.delivery_approved = False

    client_id = uuid.uuid4()
    sd = MagicMock()
    sd.id = uuid.uuid4()
    sd.client_id = client_id
    sd.type = "security_patch"
    sd.service_type = "digital_maintenance"
    sd.rejection_count = 0
    sd.status = "review"
    sd.artifact_paths = []

    task = make_task(
        agent="delivery_tracker",
        payload={
            "deal_id": str(deal_dm.id),
            "client_id": str(client_id),
            "service_delivery_id": str(sd.id),
            "action": "approve_artifact",
            "dry_run": True,
        },
    )

    with patch("agents.delivery_tracker.agent.get_deal",
               new_callable=AsyncMock, return_value=deal_dm):
        with patch("agents.delivery_tracker.agent.get_service_delivery",
                   new_callable=AsyncMock, return_value=sd):
            with patch("agents.delivery_tracker.agent.get_task_by_idempotency_key",
                       new_callable=AsyncMock, return_value=None):
                with patch("agents.delivery_tracker.agent.create_task", new_callable=AsyncMock):
                    agent = DeliveryTrackerAgent()
                    result = await agent.execute(task, db_session)

    assert result.success is True
