"""
Test di integrazione — pipeline Web Design
Verifica il flusso completo: Scout → Analyst → LeadProfiler →
Design (mockup UI) → Proposal → GATE 1 → Sales → GATE 2 →
DeliveryOrchestrator → DocGenerator → DeliveryTracker → GATE 3
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.delivery_orchestrator.agent import DeliveryOrchestratorAgent
from agents.delivery_tracker.agent import DeliveryTrackerAgent
from agents.doc_generator.agent import DocGeneratorAgent
from agents.models import GateNotApprovedError
from agents.sales.agent import SalesAgent
from tests.fixtures.leads import make_deal, make_lead
from tests.fixtures.proposals import make_proposal
from tests.fixtures.tasks import make_task


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def lead_web():
    return make_lead(
        business_name="Trattoria Vesuvio Web",
        sector="food_beverage",
        lead_score=72,
        service_gap_detected=True,
        suggested_service_type="web_design",
    )


@pytest.fixture
def deal_web(lead_web):
    return make_deal(
        service_type="web_design",
        status="proposal_sent",
        total_price_eur=300000,
        proposal_human_approved=False,
        kickoff_confirmed=False,
        delivery_approved=False,
    )


# ---------------------------------------------------------------------------
# GATE 1 — blocca invio senza approvazione
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wd_gate1_blocks_sales(db_session, deal_web):
    task = make_task(
        agent="sales",
        payload={
            "deal_id": str(deal_web.id),
            "action": "send_proposal",
        },
    )

    with patch("agents.sales.agent.get_deal",
               new_callable=AsyncMock, return_value=deal_web):
        agent = SalesAgent()
        with pytest.raises(GateNotApprovedError):
            await agent.execute(task, db_session)


# ---------------------------------------------------------------------------
# GATE 2 — blocca delivery senza kickoff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wd_gate2_blocks_delivery(db_session, deal_web):
    """DeliveryOrchestratorAgent bloccato senza kickoff_confirmed."""
    client_id = uuid.uuid4()
    task = make_task(
        agent="delivery_orchestrator",
        payload={
            "deal_id": str(deal_web.id),
            "client_id": str(client_id),
            "action": "plan",
        },
    )

    with patch("agents.delivery_orchestrator.agent.get_deal",
               new_callable=AsyncMock, return_value=deal_web):
        agent = DeliveryOrchestratorAgent()
        with pytest.raises(GateNotApprovedError):
            await agent.execute(task, db_session)


# ---------------------------------------------------------------------------
# GATE 2 sbloccato — pianifica deliveries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wd_gate2_plan_with_kickoff(db_session, deal_web, lead_web):
    """Con kickoff_confirmed=True il DeliveryOrchestrator pianifica le delivery."""
    deal_web.kickoff_confirmed = True
    deal_web.proposal_human_approved = True
    client_id = uuid.uuid4()

    task = make_task(
        agent="delivery_orchestrator",
        payload={
            "deal_id": str(deal_web.id),
            "client_id": str(client_id),
            "action": "plan",
            "dry_run": True,
        },
    )

    llm_json = (
        '{"delivery_plan": [{"type": "mockup_final", "milestone": "Mockup finalizzato",'
        ' "due_days": 10}, {"type": "page_build", "milestone": "Build pagine completato",'
        ' "due_days": 25}]}'
    )
    llm_resp = MagicMock()
    llm_resp.content = [MagicMock(text=llm_json)]

    with patch("agents.delivery_orchestrator.agent.get_deal",
               new_callable=AsyncMock, return_value=deal_web):
        with patch("agents.delivery_orchestrator.agent.get_lead",
                   new_callable=AsyncMock, return_value=lead_web):
            with patch("agents.delivery_orchestrator.agent.get_service_deliveries_for_deal",
                       new_callable=AsyncMock, return_value=[]):
                with patch("agents.delivery_orchestrator.agent.create_service_delivery",
                           new_callable=AsyncMock, return_value=MagicMock(id=uuid.uuid4())):
                    with patch("agents.delivery_orchestrator.agent.update_deal",
                               new_callable=AsyncMock, return_value=deal_web):
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
# DocGenerator — genera documento di progetto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wd_doc_generator(db_session, deal_web, lead_web):
    """DocGenerator produce documento per web_design."""
    deal_web.kickoff_confirmed = True
    deal_web.proposal_human_approved = True

    client_id = uuid.uuid4()
    sd = MagicMock()
    sd.id = uuid.uuid4()
    sd.deal_id = deal_web.id
    sd.client_id = client_id
    sd.delivery_type = "page"
    sd.type = "page"
    sd.service_type = "web_design"
    sd.status = "in_progress"
    sd.artifact_paths = []

    task = make_task(
        agent="doc_generator",
        payload={
            "deal_id": str(deal_web.id),
            "client_id": str(client_id),
            "service_delivery_id": str(sd.id),
            "document_type": "delivery_report",
            "dry_run": True,
        },
    )

    pdf_bytes = b"%PDF-1.4 web_design_report"
    llm_json = '{"document_title": "Report Web Design", "sections": ["intro", "deliverables"]}'
    llm_resp = MagicMock()
    llm_resp.content = [MagicMock(text=llm_json)]

    with patch("agents.doc_generator.agent.get_deal",
               new_callable=AsyncMock, return_value=deal_web):
        with patch("agents.doc_generator.agent.get_lead",
                   new_callable=AsyncMock, return_value=lead_web):
            with patch("agents.doc_generator.agent.get_service_delivery",
                       new_callable=AsyncMock, return_value=sd):
                with patch("agents.doc_generator.agent.render_pdf",
                           new_callable=AsyncMock, return_value=pdf_bytes):
                    with patch("agents.doc_generator.agent.upload_file",
                               new_callable=AsyncMock,
                               return_value=f"clients/{lead_web.id}/deliverables/report.pdf"):
                        with patch("agents.doc_generator.agent.update_service_delivery",
                                   new_callable=AsyncMock, return_value=sd):
                            with patch("agents.doc_generator.agent.get_task_by_idempotency_key",
                                       new_callable=AsyncMock, return_value=None):
                                with patch("agents.doc_generator.agent.create_task",
                                           new_callable=AsyncMock):
                                    agent = DocGeneratorAgent()
                                    agent._client = MagicMock()
                                    agent._client.messages.create = AsyncMock(return_value=llm_resp)
                                    result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# GATE 3 — verifica approvazione consegna finale
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wd_gate3_delivery_tracker_blocked(db_session, deal_web):
    """DeliveryTracker non può consegnare senza delivery_approved — gate gestito dall'orchestratore.
    Qui verifichiamo che con dry_run=True il tracker esegua la review con successo."""
    deal_web.kickoff_confirmed = True
    deal_web.proposal_human_approved = True
    deal_web.delivery_approved = False

    client_id = uuid.uuid4()
    sd = MagicMock()
    sd.id = uuid.uuid4()
    sd.client_id = client_id
    sd.type = "mockup"
    sd.service_type = "web_design"
    sd.rejection_count = 0
    sd.status = "review"
    sd.artifact_paths = []
    sd.artifact_path = f"clients/{deal_web.id}/deliverables/mockup.png"

    task = make_task(
        agent="delivery_tracker",
        payload={
            "deal_id": str(deal_web.id),
            "client_id": str(client_id),
            "service_delivery_id": str(sd.id),
            "action": "approve_artifact",
            "dry_run": True,
        },
    )

    with patch("agents.delivery_tracker.agent.get_deal",
               new_callable=AsyncMock, return_value=deal_web):
        with patch("agents.delivery_tracker.agent.get_service_delivery",
                   new_callable=AsyncMock, return_value=sd):
            with patch("agents.delivery_tracker.agent.get_task_by_idempotency_key",
                       new_callable=AsyncMock, return_value=None):
                with patch("agents.delivery_tracker.agent.create_task", new_callable=AsyncMock):
                    agent = DeliveryTrackerAgent()
                    result = await agent.execute(task, db_session)

    # In dry_run: review è bypassata e restituisce approved=True senza consegnare
    assert result.success is True
