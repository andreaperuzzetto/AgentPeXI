"""Test unit per agents/lead_profiler/agent.py"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.lead_profiler.agent import LeadProfilerAgent
from agents.models import AgentToolError
from tests.fixtures.leads import make_lead
from tests.fixtures.tasks import make_task


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lead_profiler_enriches_lead(db_session):
    """LeadProfiler arricchisce un lead non ancora enrichito."""
    lead = make_lead(enrichment_level=None, google_place_id="ChIJprofiler001")
    task = make_task(
        agent="lead_profiler",
        payload={"lead_id": str(lead.id), "dry_run": True},
    )

    llm_result = json.dumps({
        "ateco_code": "56.10",
        "company_size": "micro",
        "confidence": 0.75,
        "social_facebook_url": None,
        "social_instagram_url": None,
    })
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=llm_result)]

    place_details = {
        "google_place_id": lead.google_place_id,
        "business_name": lead.business_name,
        "website_url": None,
        "phone": None,
    }

    with patch("agents.lead_profiler.agent.get_lead", new_callable=AsyncMock, return_value=lead):
        with patch("agents.lead_profiler.agent.get_task_by_idempotency_key",
                   new_callable=AsyncMock, return_value=None):
            with patch("agents.lead_profiler.agent.get_place_details",
                       new_callable=AsyncMock, return_value=place_details):
                with patch("agents.lead_profiler.agent.update_lead",
                           new_callable=AsyncMock, return_value=lead):
                    with patch("agents.lead_profiler.agent.create_task",
                               new_callable=AsyncMock):
                        agent = LeadProfilerAgent()
                        agent._client = MagicMock()
                        agent._client.messages.create = AsyncMock(return_value=mock_msg)

                        result = await agent.execute(task, db_session)

    assert result.success is True
    assert result.output.get("leads_enriched", 0) >= 1


# ---------------------------------------------------------------------------
# Skip lead già enrichito
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lead_profiler_skip_already_enriched(db_session):
    """LeadProfiler salta lead con enrichment_level già impostato."""
    lead = make_lead(enrichment_level="full")
    task = make_task(
        agent="lead_profiler",
        payload={"lead_id": str(lead.id)},
    )

    with patch("agents.lead_profiler.agent.get_lead", new_callable=AsyncMock, return_value=lead):
        agent = LeadProfilerAgent()
        result = await agent.execute(task, db_session)

    assert result.success is True
    assert result.output.get("leads_skipped", 0) >= 1


# ---------------------------------------------------------------------------
# Lead non trovato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lead_profiler_lead_not_found(db_session):
    task = make_task(
        agent="lead_profiler",
        payload={"lead_id": str(uuid.uuid4())},
    )

    with patch("agents.lead_profiler.agent.get_lead", new_callable=AsyncMock, return_value=None):
        agent = LeadProfilerAgent()
        with pytest.raises(AgentToolError) as exc_info:
            await agent.execute(task, db_session)

    assert exc_info.value.code == "tool_db_lead_not_found"


# ---------------------------------------------------------------------------
# Idempotenza
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lead_profiler_idempotency(db_session):
    """LeadProfiler salta se trova una chiave idempotenza completata."""
    lead = make_lead(enrichment_level=None)
    task = make_task(
        agent="lead_profiler",
        payload={"lead_id": str(lead.id)},
    )

    from types import SimpleNamespace

    existing = SimpleNamespace(
        id=uuid.uuid4(),
        status="completed",
        output={},
    )

    with patch("agents.lead_profiler.agent.get_lead", new_callable=AsyncMock, return_value=lead):
        with patch("agents.lead_profiler.agent.get_task_by_idempotency_key",
                   new_callable=AsyncMock, return_value=existing):
            agent = LeadProfilerAgent()
            result = await agent.execute(task, db_session)

    assert result.success is True
    assert result.output.get("leads_skipped", 0) >= 1


# ---------------------------------------------------------------------------
# Payload mancante
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lead_profiler_missing_payload(db_session):
    task = make_task(agent="lead_profiler", payload={})
    agent = LeadProfilerAgent()
    with pytest.raises(AgentToolError) as exc_info:
        await agent.execute(task, db_session)

    assert exc_info.value.code == "validation_missing_payload_field"
