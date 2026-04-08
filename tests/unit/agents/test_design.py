"""Test unit per agents/design/agent.py"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.design.agent import DesignAgent
from agents.models import AgentToolError
from tests.fixtures.leads import make_deal, make_lead
from tests.fixtures.tasks import make_task


# ---------------------------------------------------------------------------
# Happy path — dry_run evita render reale
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_design_happy_path_dry_run(db_session, tmp_path):
    """DesignAgent genera artefatti in dry_run (nessun render reale)."""
    lead = make_lead(sector="horeca", website_url=None)
    deal = make_deal(service_type="web_design", lead_id=lead.id)
    task = make_task(
        agent="design",
        payload={"deal_id": str(deal.id), "lead_id": str(lead.id), "dry_run": True},
    )

    # Mock LLM: ritorna contesto JSON per i template
    llm_context = json.dumps({"brand_name": "Bar Test", "primary_color": "#e63946"})
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=llm_context)]

    with patch("agents.design.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.design.agent.get_lead", new_callable=AsyncMock, return_value=lead):
            with patch("agents.design.agent.get_task_by_idempotency_key",
                       new_callable=AsyncMock, return_value=None):
                with patch("agents.design.agent.create_task", new_callable=AsyncMock):
                    with patch("agents.design.agent.file_exists",
                               new_callable=AsyncMock, return_value=False):
                        with patch("agents.design.agent.render_to_png",
                                   new_callable=AsyncMock, return_value="/tmp/fake.png"):
                            with patch("agents.design.agent.upload_file",
                                       new_callable=AsyncMock, return_value="clients/test/artifact.png"):
                                agent = DesignAgent()
                                agent._client = MagicMock()
                                agent._client.messages.create = AsyncMock(return_value=mock_msg)

                                result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Deal non trovato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_design_deal_not_found(db_session):
    task = make_task(
        agent="design",
        payload={"deal_id": str(uuid.uuid4()), "lead_id": str(uuid.uuid4())},
    )

    with patch("agents.design.agent.get_deal", new_callable=AsyncMock, return_value=None):
        agent = DesignAgent()
        with pytest.raises(AgentToolError) as exc_info:
            await agent.execute(task, db_session)

    assert exc_info.value.code == "tool_db_deal_not_found"


# ---------------------------------------------------------------------------
# Lead non trovato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_design_lead_not_found(db_session):
    deal = make_deal()
    task = make_task(
        agent="design",
        payload={"deal_id": str(deal.id), "lead_id": str(uuid.uuid4())},
    )

    with patch("agents.design.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.design.agent.get_lead", new_callable=AsyncMock, return_value=None):
            agent = DesignAgent()
            with pytest.raises(AgentToolError) as exc_info:
                await agent.execute(task, db_session)

    assert exc_info.value.code == "tool_db_lead_not_found"


# ---------------------------------------------------------------------------
# Payload mancante
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_design_missing_payload(db_session):
    task = make_task(agent="design", payload={"deal_id": str(uuid.uuid4())})
    agent = DesignAgent()
    with pytest.raises(AgentToolError) as exc_info:
        await agent.execute(task, db_session)

    assert exc_info.value.code == "validation_missing_payload_field"


# ---------------------------------------------------------------------------
# Idempotenza: artefatto già esistente su MinIO viene skippato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_design_idempotency_existing_artifact(db_session):
    """DesignAgent skippa il render se l'artefatto esiste già su MinIO."""
    lead = make_lead(sector="horeca")
    deal = make_deal(service_type="web_design", lead_id=lead.id)
    task = make_task(
        agent="design",
        payload={"deal_id": str(deal.id), "lead_id": str(lead.id), "dry_run": False},
    )

    llm_context = json.dumps({"brand_name": "Test"})
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=llm_context)]

    with patch("agents.design.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.design.agent.get_lead", new_callable=AsyncMock, return_value=lead):
            with patch("agents.design.agent.get_task_by_idempotency_key",
                       new_callable=AsyncMock, return_value=None):
                with patch("agents.design.agent.create_task", new_callable=AsyncMock):
                    # Artefatto già caricato su MinIO
                    with patch("agents.design.agent.file_exists",
                               new_callable=AsyncMock, return_value=True):
                        agent = DesignAgent()
                        agent._client = MagicMock()
                        agent._client.messages.create = AsyncMock(return_value=mock_msg)

                        result = await agent.execute(task, db_session)

    assert result.success is True
