"""Test unit per agents/delivery_tracker/agent.py"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.delivery_tracker.agent import DeliveryTrackerAgent
from agents.models import AgentToolError, GateNotApprovedError
from tests.fixtures.leads import make_deal, make_lead
from tests.fixtures.proposals import make_proposal
from tests.fixtures.tasks import make_task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sd(deal_id, client_id=None, service_type="web_design", sd_type="mockup", status="pending"):
    sd = MagicMock()
    sd.id = uuid.uuid4()
    sd.deal_id = deal_id
    sd.client_id = client_id or uuid.uuid4()
    sd.type = sd_type
    sd.service_type = service_type
    sd.title = f"Test {sd_type}"
    sd.description = ""
    sd.status = status
    sd.rejection_count = 0
    sd.artifact_paths = [f"clients/{deal_id}/artifacts/{service_type}/{sd_type}.png"]
    return sd


# ---------------------------------------------------------------------------
# Happy path — approvazione artefatto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delivery_tracker_approve_artifact(db_session):
    """DeliveryTracker approva un artefatto con quality check positivo."""
    client_id = uuid.uuid4()
    deal = make_deal(service_type="web_design", kickoff_confirmed=True)
    lead = make_lead()
    sd = _make_sd(deal.id, client_id=client_id, service_type="web_design", sd_type="mockup")
    proposal = make_proposal(deal_id=deal.id)

    task = make_task(
        agent="delivery_tracker",
        payload={
            "deal_id": str(deal.id),
            "lead_id": str(lead.id),
            "client_id": str(client_id),
            "service_delivery_id": str(sd.id),
            "action": "review",
        },
    )

    # LLM: approvazione con completeness alta
    llm_result = json.dumps({
        "approved": True,
        "completeness_pct": 92.0,
        "blocking_issues": [],
        "notes": [],
    })
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=llm_result)]

    # Artefatto PNG fittizio (simulato come byte)
    artifact_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # PNG magic bytes

    with patch("agents.delivery_tracker.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.delivery_tracker.agent.get_lead", new_callable=AsyncMock, return_value=lead):
            with patch("agents.delivery_tracker.agent.get_service_delivery",
                       new_callable=AsyncMock, return_value=sd):
                with patch("agents.delivery_tracker.agent.get_latest_proposal",
                           new_callable=AsyncMock, return_value=proposal):
                    with patch("agents.delivery_tracker.agent.get_task_by_idempotency_key",
                               new_callable=AsyncMock, return_value=None):
                        with patch("agents.delivery_tracker.agent.create_task",
                                   new_callable=AsyncMock):
                            with patch("agents.delivery_tracker.agent.download_bytes",
                                       new_callable=AsyncMock, return_value=artifact_bytes):
                                with patch("agents.delivery_tracker.agent.upload_bytes",
                                           new_callable=AsyncMock,
                                           return_value="clients/test/reports/report.pdf"):
                                    with patch("agents.delivery_tracker.agent.create_delivery_report",
                                               new_callable=AsyncMock):
                                        with patch("agents.delivery_tracker.agent.update_service_delivery",
                                                   new_callable=AsyncMock, return_value=sd):
                                            with patch("agents.delivery_tracker.agent.update_proposal",
                                                       new_callable=AsyncMock):
                                                with patch("agents.delivery_tracker.agent.get_service_deliveries_for_deal",
                                                           new_callable=AsyncMock, return_value=[sd]):
                                                    agent = DeliveryTrackerAgent()
                                                    agent._client = MagicMock()
                                                    agent._client.messages.create = AsyncMock(return_value=mock_msg)

                                                    result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Artefatto rifiutato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delivery_tracker_reject_artifact(db_session):
    """DeliveryTracker rifiuta artefatto con blocking issues."""
    client_id = uuid.uuid4()
    deal = make_deal(service_type="web_design", kickoff_confirmed=True)
    lead = make_lead()
    sd = _make_sd(deal.id, client_id=client_id, sd_type="mockup")
    proposal = make_proposal(deal_id=deal.id)

    task = make_task(
        agent="delivery_tracker",
        payload={
            "deal_id": str(deal.id),
            "lead_id": str(lead.id),
            "client_id": str(client_id),
            "service_delivery_id": str(sd.id),
            "action": "review",
        },
    )

    llm_result = json.dumps({
        "approved": False,
        "completeness_pct": 45.0,
        "blocking_issues": [
            {"field": "responsive", "description": "Il layout non è responsive a 390px"}
        ],
        "notes": [],
    })
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=llm_result)]

    artifact_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    with patch("agents.delivery_tracker.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.delivery_tracker.agent.get_lead", new_callable=AsyncMock, return_value=lead):
            with patch("agents.delivery_tracker.agent.get_service_delivery",
                       new_callable=AsyncMock, return_value=sd):
                with patch("agents.delivery_tracker.agent.get_latest_proposal",
                           new_callable=AsyncMock, return_value=proposal):
                    with patch("agents.delivery_tracker.agent.get_task_by_idempotency_key",
                               new_callable=AsyncMock, return_value=None):
                        with patch("agents.delivery_tracker.agent.create_task",
                                   new_callable=AsyncMock):
                            with patch("agents.delivery_tracker.agent.download_bytes",
                                       new_callable=AsyncMock, return_value=artifact_bytes):
                                with patch("agents.delivery_tracker.agent.upload_bytes",
                                           new_callable=AsyncMock,
                                           return_value="clients/test/reports/report.pdf"):
                                    with patch("agents.delivery_tracker.agent.create_delivery_report",
                                               new_callable=AsyncMock):
                                        with patch("agents.delivery_tracker.agent.update_service_delivery",
                                                   new_callable=AsyncMock, return_value=sd):
                                            with patch("agents.delivery_tracker.agent.update_proposal",
                                                       new_callable=AsyncMock):
                                                with patch("agents.delivery_tracker.agent.get_service_deliveries_for_deal",
                                                           new_callable=AsyncMock, return_value=[sd]):
                                                    agent = DeliveryTrackerAgent()
                                                    agent._client = MagicMock()
                                                    agent._client.messages.create = AsyncMock(
                                                        return_value=mock_msg
                                                    )
                                                    result = await agent.execute(task, db_session)

    assert result.success is True
    assert result.output.get("approved") is False


# ---------------------------------------------------------------------------
# Deal non trovato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delivery_tracker_deal_not_found(db_session):
    client_id = uuid.uuid4()
    sd_id = uuid.uuid4()
    sd = _make_sd(uuid.uuid4(), client_id=client_id, sd_type="mockup")
    sd.id = sd_id
    task = make_task(
        agent="delivery_tracker",
        payload={
            "deal_id": str(uuid.uuid4()),
            "lead_id": str(uuid.uuid4()),
            "client_id": str(client_id),
            "service_delivery_id": str(sd_id),
            "action": "review",
        },
    )

    with patch("agents.delivery_tracker.agent.get_deal", new_callable=AsyncMock, return_value=None):
        with patch("agents.delivery_tracker.agent.get_service_delivery",
                   new_callable=AsyncMock, return_value=sd):
            with patch("agents.delivery_tracker.agent.get_task_by_idempotency_key",
                       new_callable=AsyncMock, return_value=None):
                agent = DeliveryTrackerAgent()
                with pytest.raises(AgentToolError) as exc_info:
                    await agent.execute(task, db_session)

    assert exc_info.value.code == "tool_db_deal_not_found"
