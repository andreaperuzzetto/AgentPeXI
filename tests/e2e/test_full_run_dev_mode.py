"""
Test E2E — full run in modalità dev (dry_run=True, gate bypass via mock DB)

Simula l'intera pipeline Consulenza end-to-end:
Scout → Analyst → LeadProfiler → Design → Proposal →
[GATE 1 bypass] → Sales → [GATE 2 bypass] → DeliveryOrchestrator →
DocGenerator → DeliveryTracker → [GATE 3 bypass] → AccountManager

Tutti i tool esterni e LLM sono mockati.
dry_run=True su tutti gli agenti.
Gate flag settati direttamente sull'oggetto Deal mockato.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.account_manager.agent import AccountManagerAgent
from agents.analyst.agent import AnalystAgent
from agents.delivery_orchestrator.agent import DeliveryOrchestratorAgent
from agents.delivery_tracker.agent import DeliveryTrackerAgent
from agents.design.agent import DesignAgent
from agents.doc_generator.agent import DocGeneratorAgent
from agents.lead_profiler.agent import LeadProfilerAgent
from agents.proposal.agent import ProposalAgent
from agents.sales.agent import SalesAgent
from agents.scout.agent import ScoutAgent
from tests.fixtures.leads import make_client, make_deal, make_lead
from tests.fixtures.proposals import make_proposal
from tests.fixtures.tasks import make_task


# ---------------------------------------------------------------------------
# Fixture condivisa per l'intero E2E
# ---------------------------------------------------------------------------

@pytest.fixture
def e2e_state():
    """Stato condiviso tra gli step del run E2E."""
    lead = make_lead(
        business_name="E2E Consulting SRL",
        sector="professional_services",
        lead_score=75,
        service_gap_detected=True,
        suggested_service_type="consulting",
        phone="REDACTED",
    )
    deal = make_deal(
        service_type="consulting",
        status="discovery",
        total_price_eur=500000,
        # Tutti i gate aperti (dev/bypass mode)
        proposal_human_approved=True,
        kickoff_confirmed=True,
        consulting_approved=True,
        delivery_approved=True,
    )
    client = make_client(lead_id=lead.id, contact_email="client@e2e-consulting.it")
    proposal = make_proposal(deal_id=deal.id, version=1, service_type="consulting")
    return {"lead": lead, "deal": deal, "client": client, "proposal": proposal}


# ---------------------------------------------------------------------------
# Helper — LLM mock generico
# ---------------------------------------------------------------------------

def _llm(json_str: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=json_str)]
    return resp


# ---------------------------------------------------------------------------
# Step 1 — Scout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_step1_scout(db_session, e2e_state):
    lead = e2e_state["lead"]
    task = make_task(
        agent="scout",
        payload={
            "zone": "Roma",
            "sector": "professional_services",
            "radius_km": 5,
            "max_results": 2,
            "dry_run": True,
        },
    )

    place = {
        "google_place_id": lead.place_id or f"place-{lead.id}",
        "name": lead.business_name,
        "business_name": lead.business_name,
        "address": lead.address or "Via Roma 1",
        "phone": None,
        "website": "https://e2e-consulting.it",
        "website_url": "https://e2e-consulting.it",
        "rating": 4.0,
        "total_ratings": 20,
        "types": ["establishment"],
    }

    with patch("agents.scout.agent.search_businesses",
               new_callable=AsyncMock, return_value=[place]):
        with patch("agents.scout.agent.get_lead_by_place_id",
                   new_callable=AsyncMock, return_value=None):
            with patch("agents.scout.agent.create_lead",
                       new_callable=AsyncMock, return_value=lead):
                agent = ScoutAgent()
                result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Step 2 — Analyst
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_step2_analyst(db_session, e2e_state):
    lead = e2e_state["lead"]
    task = make_task(
        agent="analyst",
        payload={"lead_id": str(lead.id), "dry_run": True},
    )

    llm = _llm(
        '{"signals": {'
        '"consulting": {"operational_inefficiency": true, "rapid_growth_no_support": true, "no_internal_expertise": true},'
        '"web_design": {},'
        '"digital_maintenance": {}'
        '},'
        '"suggested_service_type": "consulting",'
        '"gap_summary": "Aziendalizzazione in corso",'
        '"estimated_value_eur": 5000}'
    )

    with patch("agents.analyst.agent.get_lead", new_callable=AsyncMock, return_value=lead):
        with patch("agents.analyst.agent.update_lead", new_callable=AsyncMock, return_value=lead):
            with patch("agents.analyst.agent.get_task_by_idempotency_key",
                       new_callable=AsyncMock, return_value=None):
                with patch("agents.analyst.agent.create_task", new_callable=AsyncMock):
                    agent = AnalystAgent()
                    agent._client = MagicMock()
                    agent._client.messages.create = AsyncMock(return_value=llm)
                    result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Step 3 — LeadProfiler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_step3_lead_profiler(db_session, e2e_state):
    lead = e2e_state["lead"]
    task = make_task(
        agent="lead_profiler",
        payload={"lead_id": str(lead.id), "dry_run": True},
    )

    place_details = {
        "name": lead.business_name,
        "formatted_address": "Via Roma 1, 00100 Roma",
        "website": "https://e2e-consulting.it",
        "formatted_phone_number": None,
        "opening_hours": {"open_now": True},
        "rating": 4.0,
    }

    llm = _llm(
        '{"ateco_code": "70.22.09", "company_size": "small",'
        ' "social_profiles": {}, "enrichment_notes": "PMI consulenza"}'
    )

    with patch("agents.lead_profiler.agent.get_lead", new_callable=AsyncMock, return_value=lead):
        with patch("agents.lead_profiler.agent.get_place_details",
                   new_callable=AsyncMock, return_value=place_details):
            with patch("agents.lead_profiler.agent.update_lead",
                       new_callable=AsyncMock, return_value=lead):
                with patch("agents.lead_profiler.agent.get_task_by_idempotency_key",
                           new_callable=AsyncMock, return_value=None):
                    with patch("agents.lead_profiler.agent.create_task", new_callable=AsyncMock):
                        agent = LeadProfilerAgent()
                        agent._client = MagicMock()
                        agent._client.messages.create = AsyncMock(return_value=llm)
                        result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Step 4 — Design
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_step4_design(db_session, e2e_state):
    lead, deal = e2e_state["lead"], e2e_state["deal"]
    task = make_task(
        agent="design",
        payload={
            "deal_id": str(deal.id),
            "lead_id": str(lead.id),
            "service_type": "consulting",
            "dry_run": True,
        },
    )

    llm = _llm(
        '{"roadmap_notes": "Piano 4 fasi", "artifact_pages":'
        ' ["roadmap", "presentation"]}'
    )
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

    with patch("agents.design.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.design.agent.get_lead", new_callable=AsyncMock, return_value=lead):
            with patch("agents.design.agent.render_to_png",
                       new_callable=AsyncMock, return_value=png_bytes):
                with patch("agents.design.agent.upload_file",
                           new_callable=AsyncMock,
                           return_value=f"clients/{lead.id}/mockups/roadmap.png"):
                    with patch("agents.design.agent.file_exists",
                               new_callable=AsyncMock, return_value=False):
                        with patch("agents.design.agent.get_task_by_idempotency_key",
                                   new_callable=AsyncMock, return_value=None):
                            with patch("agents.design.agent.create_task", new_callable=AsyncMock):
                                agent = DesignAgent()
                                agent._client = MagicMock()
                                agent._client.messages.create = AsyncMock(return_value=llm)
                                result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Step 5 — Proposal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_step5_proposal(db_session, e2e_state):
    lead, deal, proposal = e2e_state["lead"], e2e_state["deal"], e2e_state["proposal"]
    task = make_task(
        agent="proposal",
        payload={
            "deal_id": str(deal.id),
            "lead_id": str(lead.id),
            "dry_run": True,
        },
    )

    llm = _llm(
        '{"solution_summary": "Consulenza strategica 4 fasi",'
        ' "roi_summary": "ROI atteso 3x in 12 mesi",'
        ' "roi_metrics": [{"metric": "efficienza", "value": "+30%"}],'
        ' "milestones": [{"phase": "Fase 1", "deliverable": "Analisi", "weeks": 2}],'
        ' "key_benefits": ["efficienza", "struttura"]}'
    )

    with patch("agents.proposal.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.proposal.agent.get_lead", new_callable=AsyncMock, return_value=lead):
            with patch("agents.proposal.agent.get_latest_proposal",
                       new_callable=AsyncMock, return_value=None):
                with patch("agents.proposal.agent.render_pdf",
                           new_callable=AsyncMock, return_value=b"%PDF e2e"):
                    with patch("agents.proposal.agent.upload_file",
                               new_callable=AsyncMock,
                               return_value=f"clients/{lead.id}/proposals/v1.pdf"):
                        with patch("agents.proposal.agent.create_proposal",
                                   new_callable=AsyncMock, return_value=proposal):
                            with patch("agents.proposal.agent.update_deal",
                                       new_callable=AsyncMock, return_value=deal):
                                with patch("agents.proposal.agent.get_task_by_idempotency_key",
                                           new_callable=AsyncMock, return_value=None):
                                    with patch("agents.proposal.agent.create_task",
                                               new_callable=AsyncMock):
                                        agent = ProposalAgent()
                                        agent._client = MagicMock()
                                        agent._client.messages.create = AsyncMock(return_value=llm)
                                        result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Step 6 — Sales (GATE 1 già aperto nel fixture)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_step6_sales(db_session, e2e_state):
    lead, deal, proposal = e2e_state["lead"], e2e_state["deal"], e2e_state["proposal"]
    task = make_task(
        agent="sales",
        payload={
            "deal_id": str(deal.id),
            "lead_id": str(lead.id),
            "proposal_id": str(proposal.id),
            "contact_email": "client@e2e-consulting.it",
            "contact_name": "E2E Cliente",
            "action": "send_proposal",
            "dry_run": True,
        },
    )

    with patch("agents.sales.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.sales.agent.get_lead", new_callable=AsyncMock, return_value=lead):
            with patch("agents.sales.agent.get_latest_proposal",
                       new_callable=AsyncMock, return_value=proposal):
                with patch("agents.sales.agent.get_task_by_idempotency_key",
                           new_callable=AsyncMock, return_value=None):
                    with patch("agents.sales.agent.create_task", new_callable=AsyncMock):
                        with patch("agents.sales.agent.send_email",
                                   new_callable=AsyncMock,
                                   return_value={"message_id": "m-e2e", "thread_id": "t-e2e"}):
                            with patch("agents.sales.agent.log_email", new_callable=AsyncMock):
                                with patch("agents.sales.agent.update_deal",
                                           new_callable=AsyncMock, return_value=deal):
                                    agent = SalesAgent()
                                    result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Step 7 — DeliveryOrchestrator (GATE 2 già aperto)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_step7_delivery_orchestrator(db_session, e2e_state):
    lead, deal = e2e_state["lead"], e2e_state["deal"]
    client_id = e2e_state["client"].id
    task = make_task(
        agent="delivery_orchestrator",
        payload={
            "deal_id": str(deal.id),
            "client_id": str(client_id),
            "action": "plan",
            "dry_run": True,
        },
    )

    llm = _llm(
        '{"delivery_plan": [{"type": "report", "milestone": "Report fase 1", "due_days": 14}]}'
    )

    with patch("agents.delivery_orchestrator.agent.get_deal",
               new_callable=AsyncMock, return_value=deal):
        with patch("agents.delivery_orchestrator.agent.get_lead",
                   new_callable=AsyncMock, return_value=lead):
            with patch("agents.delivery_orchestrator.agent.get_service_deliveries_for_deal",
                       new_callable=AsyncMock, return_value=[]):
                with patch("agents.delivery_orchestrator.agent.create_service_delivery",
                           new_callable=AsyncMock, return_value=MagicMock(id=uuid.uuid4())):
                    with patch("agents.delivery_orchestrator.agent.update_deal",
                               new_callable=AsyncMock, return_value=deal):
                        with patch("agents.delivery_orchestrator.agent.get_task_by_idempotency_key",
                                   new_callable=AsyncMock, return_value=None):
                            with patch("agents.delivery_orchestrator.agent.create_task",
                                       new_callable=AsyncMock):
                                agent = DeliveryOrchestratorAgent()
                                agent._client = MagicMock()
                                agent._client.messages.create = AsyncMock(return_value=llm)
                                result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Step 8 — DocGenerator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_step8_doc_generator(db_session, e2e_state):
    lead, deal = e2e_state["lead"], e2e_state["deal"]
    client_id = e2e_state["client"].id
    sd = MagicMock()
    sd.id = uuid.uuid4()
    sd.deal_id = deal.id
    sd.client_id = client_id
    sd.delivery_type = "report"
    sd.type = "report"
    sd.service_type = "consulting"
    sd.status = "in_progress"
    sd.artifact_paths = []

    task = make_task(
        agent="doc_generator",
        payload={
            "deal_id": str(deal.id),
            "client_id": str(client_id),
            "service_delivery_id": str(sd.id),
            "document_type": "consulting_report",
            "dry_run": True,
        },
    )

    llm = _llm(
        '{"document_title": "Report Consulenza E2E",'
        ' "sections": ["abstract", "analisi", "roadmap", "conclusioni"]}'
    )

    with patch("agents.doc_generator.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.doc_generator.agent.get_lead", new_callable=AsyncMock, return_value=lead):
            with patch("agents.doc_generator.agent.get_service_delivery",
                       new_callable=AsyncMock, return_value=sd):
                with patch("agents.doc_generator.agent.render_pdf",
                           new_callable=AsyncMock, return_value=b"%PDF e2e report"):
                    with patch("agents.doc_generator.agent.upload_file",
                               new_callable=AsyncMock,
                               return_value=f"clients/{lead.id}/deliverables/report.pdf"):
                        with patch("agents.doc_generator.agent.update_service_delivery",
                                   new_callable=AsyncMock, return_value=sd):
                            with patch("agents.doc_generator.agent.get_task_by_idempotency_key",
                                       new_callable=AsyncMock, return_value=None):
                                with patch("agents.doc_generator.agent.create_task",
                                           new_callable=AsyncMock):
                                    agent = DocGeneratorAgent()
                                    agent._client = MagicMock()
                                    agent._client.messages.create = AsyncMock(return_value=llm)
                                    result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Step 9 — DeliveryTracker approva il deliverable (GATE 3 aperto)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_step9_delivery_tracker_approve(db_session, e2e_state):
    deal = e2e_state["deal"]
    client_id = e2e_state["client"].id
    # deal.consulting_approved=True grazie al fixture

    sd = MagicMock()
    sd.id = uuid.uuid4()
    sd.deal_id = deal.id
    sd.client_id = client_id
    sd.type = "report"
    sd.service_type = "consulting"
    sd.rejection_count = 0
    sd.status = "review"
    sd.artifact_path = f"clients/{deal.id}/deliverables/report.pdf"
    sd.artifact_paths = [sd.artifact_path]

    task = make_task(
        agent="delivery_tracker",
        payload={
            "deal_id": str(deal.id),
            "client_id": str(client_id),
            "service_delivery_id": str(sd.id),
            "action": "approve_artifact",
            "dry_run": True,
        },
    )

    pdf_bytes = b"%PDF-1.4 consulting report"

    with patch("agents.delivery_tracker.agent.get_deal",
               new_callable=AsyncMock, return_value=deal):
        with patch("agents.delivery_tracker.agent.get_service_delivery",
                   new_callable=AsyncMock, return_value=sd):
            with patch("agents.delivery_tracker.agent.download_bytes",
                       new_callable=AsyncMock, return_value=pdf_bytes):
                with patch("agents.delivery_tracker.agent.update_service_delivery",
                           new_callable=AsyncMock, return_value=sd):
                    with patch("agents.delivery_tracker.agent.get_task_by_idempotency_key",
                               new_callable=AsyncMock, return_value=None):
                        with patch("agents.delivery_tracker.agent.create_task",
                                   new_callable=AsyncMock):
                            agent = DeliveryTrackerAgent()
                            agent._client = MagicMock()
                            result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Step 10 — AccountManager — onboarding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_step10_account_manager_onboarding(db_session, e2e_state):
    deal, client = e2e_state["deal"], e2e_state["client"]
    deal.status = "delivered"

    task = make_task(
        agent="account_manager",
        payload={
            "deal_id": str(deal.id),
            "client_id": str(client.id),
            "action": "onboarding",
            "dry_run": True,
        },
    )

    with patch("agents.account_manager.agent.get_deal",
               new_callable=AsyncMock, return_value=deal):
        with patch("agents.account_manager.agent.get_client",
                   new_callable=AsyncMock, return_value=client):
            with patch("agents.account_manager.agent.get_task_by_idempotency_key",
                       new_callable=AsyncMock, return_value=None):
                with patch("agents.account_manager.agent.create_task", new_callable=AsyncMock):
                    with patch("agents.account_manager.agent.send_email",
                               new_callable=AsyncMock,
                               return_value={"message_id": "e2e-onboarding", "thread_id": "t-ob"}):
                        with patch("agents.account_manager.agent.log_email",
                                   new_callable=AsyncMock):
                            with patch("agents.account_manager.agent.update_deal",
                                       new_callable=AsyncMock, return_value=deal):
                                agent = AccountManagerAgent()
                                result = await agent.execute(task, db_session)

    assert result.success is True
