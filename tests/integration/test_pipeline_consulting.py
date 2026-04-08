"""
Test di integrazione — pipeline Consulenza
Scout → Analyst → LeadProfiler → Design → Proposal → Sales (GATE 1 bloccante)

Tutti i tool esterni sono mockati. DB è AsyncMock.
dry_run=True su tutti gli agenti per evitare side effect.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.analyst.agent import AnalystAgent
from agents.design.agent import DesignAgent
from agents.lead_profiler.agent import LeadProfilerAgent
from agents.models import AgentResult, GateNotApprovedError
from agents.proposal.agent import ProposalAgent
from agents.sales.agent import SalesAgent
from agents.scout.agent import ScoutAgent
from tests.fixtures.leads import make_deal, make_lead
from tests.fixtures.proposals import make_proposal
from tests.fixtures.tasks import make_task


# ---------------------------------------------------------------------------
# Fixtures di pipeline
# ---------------------------------------------------------------------------

@pytest.fixture
def lead_consulting():
    return make_lead(
        business_name="Studio Rinaldi Consulting",
        sector="professional_services",
        lead_score=78,
        service_gap_detected=True,
        suggested_service_type="consulting",
        phone="REDACTED",
    )


@pytest.fixture
def deal_consulting(lead_consulting):
    return make_deal(
        service_type="consulting",
        status="proposal_sent",
        total_price_eur=400000,  # EUR in centesimi
        proposal_human_approved=False,  # Gate 1 inizialmente chiuso
    )


# ---------------------------------------------------------------------------
# STEP 1 — Scout individua il lead
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase1_scout_consulting(db_session):
    """Scout scopre un business nel settore consulting e crea il lead."""
    task = make_task(
        agent="scout",
        payload={
            "zone": "Milano",
            "sector": "professional_services",
            "radius_km": 5,
            "max_results": 3,
            "dry_run": True,
        },
    )

    place = {
        "google_place_id": "place-consulting-001",
        "name": "Studio Rinaldi Consulting",
        "business_name": "Studio Rinaldi Consulting",
        "address": "Via Torino 10, Milano",
        "phone": None,
        "website": "https://studioconsulting.it",
        "website_url": "https://studioconsulting.it",
        "rating": 4.2,
        "total_ratings": 27,
        "types": ["establishment"],
    }

    with patch("agents.scout.agent.search_businesses",
               new_callable=AsyncMock, return_value=[place]):
        with patch("agents.scout.agent.get_lead_by_place_id",
                   new_callable=AsyncMock, return_value=None):
            with patch("agents.scout.agent.create_lead",
                       new_callable=AsyncMock,
                       return_value=make_lead(place_id="place-consulting-001")):
                agent = ScoutAgent()
                result = await agent.execute(task, db_session)

    assert result.success is True
    assert result.output.get("leads_found", 0) >= 1 or result.output.get("dry_run") is True


# ---------------------------------------------------------------------------
# STEP 2 — Analyst qualifica il lead
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase1_analyst_qualifies_consulting_lead(db_session, lead_consulting):
    """Analyst qualifica il lead con service_type=consulting."""
    task = make_task(
        agent="analyst",
        payload={
            "lead_id": str(lead_consulting.id),
            "dry_run": True,
        },
    )

    llm_json = (
        '{"signals": {'
        '"consulting": {"operational_inefficiency": true, "rapid_growth_no_support": true, "no_internal_expertise": true},'
        '"web_design": {},'
        '"digital_maintenance": {}'
        '},'
        '"suggested_service_type": "consulting",'
        '"gap_summary": "Crescita senza struttura operativa",'
        '"estimated_value_eur": 4000}'
    )
    llm_resp = MagicMock()
    llm_resp.content = [MagicMock(text=llm_json)]

    with patch("agents.analyst.agent.get_lead",
               new_callable=AsyncMock, return_value=lead_consulting):
        with patch("agents.analyst.agent.update_lead", new_callable=AsyncMock,
                   return_value=lead_consulting):
            with patch("agents.analyst.agent.get_task_by_idempotency_key",
                       new_callable=AsyncMock, return_value=None):
                with patch("agents.analyst.agent.create_task", new_callable=AsyncMock):
                    agent = AnalystAgent()
                    agent._client = MagicMock()
                    agent._client.messages.create = AsyncMock(return_value=llm_resp)
                    result = await agent.execute(task, db_session)

    assert result.success is True
    output = result.output
    assert output.get("leads_qualified", 0) >= 1 or output.get("qualified") is True


# ---------------------------------------------------------------------------
# STEP 3 — LeadProfiler arricchisce il lead
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase1_lead_profiler_enriches(db_session, lead_consulting):
    """LeadProfiler arricchisce con ATECO e company_size."""
    task = make_task(
        agent="lead_profiler",
        payload={
            "lead_id": str(lead_consulting.id),
            "dry_run": True,
        },
    )

    place_details = {
        "name": "Studio Rinaldi Consulting",
        "formatted_address": "Via Torino 10, Milano",
        "website": "https://studioconsulting.it",
        "formatted_phone_number": None,
        "opening_hours": {"open_now": True},
        "rating": 4.2,
    }

    llm_json = (
        '{"ateco_code": "70.22.09", "company_size": "small",'
        ' "social_profiles": {}, "enrichment_notes": "Studio piccolo consulenza"}'
    )
    llm_resp = MagicMock()
    llm_resp.content = [MagicMock(text=llm_json)]

    with patch("agents.lead_profiler.agent.get_lead",
               new_callable=AsyncMock, return_value=lead_consulting):
        with patch("agents.lead_profiler.agent.get_place_details",
                   new_callable=AsyncMock, return_value=place_details):
            with patch("agents.lead_profiler.agent.update_lead",
                       new_callable=AsyncMock, return_value=lead_consulting):
                with patch("agents.lead_profiler.agent.get_task_by_idempotency_key",
                           new_callable=AsyncMock, return_value=None):
                    with patch("agents.lead_profiler.agent.create_task", new_callable=AsyncMock):
                        agent = LeadProfilerAgent()
                        agent._client = MagicMock()
                        agent._client.messages.create = AsyncMock(return_value=llm_resp)
                        result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# STEP 4 — Design produce artefatti per consulenza
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase2_design_consulting_artifacts(db_session, lead_consulting, deal_consulting):
    """Design Agent genera artefatti visivi per service_type=consulting."""
    task = make_task(
        agent="design",
        payload={
            "deal_id": str(deal_consulting.id),
            "lead_id": str(lead_consulting.id),
            "service_type": "consulting",
            "dry_run": True,
        },
    )

    llm_json = (
        '{"roadmap_notes": "Piano a 3 fasi", "artifact_pages": '
        '["roadmap", "workshop_structure", "process_schema", "presentation"]}'
    )
    llm_resp = MagicMock()
    llm_resp.content = [MagicMock(text=llm_json)]

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    with patch("agents.design.agent.get_deal",
               new_callable=AsyncMock, return_value=deal_consulting):
        with patch("agents.design.agent.get_lead",
                   new_callable=AsyncMock, return_value=lead_consulting):
            with patch("agents.design.agent.render_to_png",
                       new_callable=AsyncMock, return_value=png_bytes):
                with patch("agents.design.agent.upload_file",
                           new_callable=AsyncMock,
                           return_value=f"clients/{lead_consulting.id}/mockups/roadmap.png"):
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
# STEP 5 — Proposal Agent crea la proposta
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase2_proposal_consulting(db_session, lead_consulting, deal_consulting):
    """Proposal Agent genera proposta PDF per consulenza."""
    task = make_task(
        agent="proposal",
        payload={
            "deal_id": str(deal_consulting.id),
            "lead_id": str(lead_consulting.id),
            "dry_run": True,
        },
    )

    proposal = make_proposal(deal_id=deal_consulting.id, version=1, service_type="consulting")
    pdf_bytes = b"%PDF-1.4 mock"

    llm_json = (
        '{"solution_summary": "Consulenza strategica 3 fasi",'
        ' "roi_summary": "ROI atteso 3x in 12 mesi",'
        ' "roi_metrics": [{"metric": "efficienza", "value": "+30%"}],'
        ' "milestones": [{"phase": "Fase 1", "deliverable": "Analisi", "weeks": 2}],'
        ' "key_benefits": ["efficienza", "struttura", "crescita"]}'
    )
    llm_resp = MagicMock()
    llm_resp.content = [MagicMock(text=llm_json)]

    with patch("agents.proposal.agent.get_deal",
               new_callable=AsyncMock, return_value=deal_consulting):
        with patch("agents.proposal.agent.get_lead",
                   new_callable=AsyncMock, return_value=lead_consulting):
            with patch("agents.proposal.agent.get_latest_proposal",
                       new_callable=AsyncMock, return_value=None):
                with patch("agents.proposal.agent.render_pdf",
                           new_callable=AsyncMock, return_value=pdf_bytes):
                    with patch("agents.proposal.agent.upload_file",
                               new_callable=AsyncMock,
                               return_value=f"clients/{lead_consulting.id}/proposals/v1.pdf"):
                        with patch("agents.proposal.agent.create_proposal",
                                   new_callable=AsyncMock, return_value=proposal):
                            with patch("agents.proposal.agent.update_deal",
                                       new_callable=AsyncMock, return_value=deal_consulting):
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
# GATE 1 bloccante — proposta non approvata dall'operatore
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gate1_blocks_sales_before_approval(db_session, lead_consulting, deal_consulting):
    """
    GATE 1: SalesAgent NON può inviare email prima che l'operatore
    approvi la proposta (deal.proposal_human_approved=False).
    """
    # deal_consulting ha proposal_human_approved=False per default
    task = make_task(
        agent="sales",
        payload={
            "deal_id": str(deal_consulting.id),
            "action": "send_proposal",
            "dry_run": True,
        },
    )

    with patch("agents.sales.agent.get_deal",
               new_callable=AsyncMock, return_value=deal_consulting):
        agent = SalesAgent()
        with pytest.raises(GateNotApprovedError):
            await agent.execute(task, db_session)


# ---------------------------------------------------------------------------
# GATE 1 superato — proposta approvata, Sales invia email
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gate1_unlocks_sales_after_approval(db_session, lead_consulting, deal_consulting):
    """Con proposal_human_approved=True il Sales Agent procede all'invio."""
    deal_consulting.proposal_human_approved = True
    _proposal = make_proposal(deal_id=deal_consulting.id)

    task = make_task(
        agent="sales",
        payload={
            "deal_id": str(deal_consulting.id),
            "lead_id": str(lead_consulting.id),
            "proposal_id": str(_proposal.id),
            "contact_email": "client@studiorinaldi.it",
            "contact_name": "Mario Rinaldi",
            "action": "send_proposal",
            "dry_run": True,
        },
    )

    with patch("agents.sales.agent.get_deal",
               new_callable=AsyncMock, return_value=deal_consulting):
        with patch("agents.sales.agent.get_lead",
                   new_callable=AsyncMock, return_value=lead_consulting):
            with patch("agents.sales.agent.get_latest_proposal",
                       new_callable=AsyncMock,
                       return_value=_proposal):
                with patch("agents.sales.agent.get_task_by_idempotency_key",
                           new_callable=AsyncMock, return_value=None):
                    with patch("agents.sales.agent.create_task", new_callable=AsyncMock):
                        with patch("agents.sales.agent.send_email",
                                   new_callable=AsyncMock,
                                   return_value={"message_id": "m1", "thread_id": "t1"}):
                            with patch("agents.sales.agent.log_email", new_callable=AsyncMock):
                                with patch("agents.sales.agent.update_deal",
                                           new_callable=AsyncMock, return_value=deal_consulting):
                                    agent = SalesAgent()
                                    result = await agent.execute(task, db_session)

    assert result.success is True
