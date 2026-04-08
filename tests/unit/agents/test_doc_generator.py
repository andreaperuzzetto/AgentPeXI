"""Test unit per agents/doc_generator/agent.py"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.doc_generator.agent import DocGeneratorAgent
from agents.models import AgentToolError
from tests.fixtures.leads import make_deal, make_lead
from tests.fixtures.tasks import make_task


# ---------------------------------------------------------------------------
# Happy path — dry_run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_doc_generator_happy_path_consulting(db_session, tmp_path):
    """DocGenerator produce un documento PDF consulting in dry_run."""
    lead = make_lead()
    client_id = uuid.uuid4()
    deal = make_deal(service_type="consulting", kickoff_confirmed=True)

    sd = MagicMock()
    sd.id = uuid.uuid4()
    sd.client_id = client_id
    sd.type = "report"
    sd.status = "pending"
    sd.rejection_count = 0

    task = make_task(
        agent="doc_generator",
        payload={
            "deal_id": str(deal.id),
            "lead_id": str(lead.id),
            "client_id": str(client_id),
            "service_delivery_id": str(sd.id),
            "dry_run": True,
        },
    )

    llm_html = "<html><body><h1>Report Diagnostico</h1></body></html>"
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=llm_html)]

    with patch("agents.doc_generator.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.doc_generator.agent.get_lead", new_callable=AsyncMock, return_value=lead):
            with patch("agents.doc_generator.agent.get_service_delivery",
                       new_callable=AsyncMock, return_value=sd):
                with patch("agents.doc_generator.agent.get_task_by_idempotency_key",
                           new_callable=AsyncMock, return_value=None):
                    with patch("agents.doc_generator.agent.create_task", new_callable=AsyncMock):
                        with patch("agents.doc_generator.agent.update_service_delivery",
                                   new_callable=AsyncMock, return_value=sd):
                            with patch("agents.doc_generator.agent.render_pdf",
                                       new_callable=AsyncMock,
                                       return_value=str(tmp_path / "report.pdf")):
                                with patch("agents.doc_generator.agent.upload_file",
                                           new_callable=AsyncMock,
                                           return_value="clients/test/artifacts/consulting/report.pdf"):
                                    with patch("agents.doc_generator.agent.file_exists",
                                               new_callable=AsyncMock, return_value=False):
                                        agent = DocGeneratorAgent()
                                        agent._client = MagicMock()
                                        agent._client.messages.create = AsyncMock(return_value=mock_msg)

                                        result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# ServiceDelivery non trovata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_doc_generator_service_delivery_not_found(db_session):
    deal = make_deal(service_type="consulting")
    lead = make_lead()
    client_id = uuid.uuid4()
    task = make_task(
        agent="doc_generator",
        payload={
            "deal_id": str(deal.id),
            "lead_id": str(lead.id),
            "client_id": str(client_id),
            "service_delivery_id": str(uuid.uuid4()),
        },
    )

    with patch("agents.doc_generator.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.doc_generator.agent.get_lead", new_callable=AsyncMock, return_value=lead):
            with patch("agents.doc_generator.agent.get_service_delivery",
                       new_callable=AsyncMock, return_value=None):
                agent = DocGeneratorAgent()
                with pytest.raises(AgentToolError) as exc_info:
                    await agent.execute(task, db_session)

    assert "not_found" in exc_info.value.code


# ---------------------------------------------------------------------------
# Deal non trovato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_doc_generator_deal_not_found(db_session):
    client_id = uuid.uuid4()
    sd = MagicMock()
    sd.id = uuid.uuid4()
    sd.client_id = client_id
    sd.type = "report"
    sd.status = "pending"
    sd.rejection_count = 0
    sd.artifact_paths = []

    task = make_task(
        agent="doc_generator",
        payload={
            "deal_id": str(uuid.uuid4()),
            "lead_id": str(uuid.uuid4()),
            "client_id": str(client_id),
            "service_delivery_id": str(sd.id),
        },
    )

    with patch("agents.doc_generator.agent.get_deal", new_callable=AsyncMock, return_value=None):
        with patch("agents.doc_generator.agent.get_service_delivery",
                   new_callable=AsyncMock, return_value=sd):
            with patch("agents.doc_generator.agent.get_task_by_idempotency_key",
                       new_callable=AsyncMock, return_value=None):
                agent = DocGeneratorAgent()
                with pytest.raises(AgentToolError) as exc_info:
                    await agent.execute(task, db_session)

    assert exc_info.value.code == "tool_db_deal_not_found"


# ---------------------------------------------------------------------------
# Idempotenza — artefatto già caricato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_doc_generator_idempotency_artifact_exists(db_session):
    """DocGenerator skippa la generazione se l'artefatto esiste già su MinIO."""
    client_id = uuid.uuid4()
    deal = make_deal(service_type="web_design")
    lead = make_lead()
    sd = MagicMock()
    sd.id = uuid.uuid4()
    sd.client_id = client_id
    sd.type = "mockup"
    sd.status = "pending"
    sd.rejection_count = 0

    task = make_task(
        agent="doc_generator",
        payload={
            "deal_id": str(deal.id),
            "lead_id": str(lead.id),
            "client_id": str(client_id),
            "service_delivery_id": str(sd.id),
            "dry_run": False,
        },
    )

    llm_ctx = "{}"
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=llm_ctx)]

    with patch("agents.doc_generator.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.doc_generator.agent.get_lead", new_callable=AsyncMock, return_value=lead):
            with patch("agents.doc_generator.agent.get_service_delivery",
                       new_callable=AsyncMock, return_value=sd):
                with patch("agents.doc_generator.agent.get_task_by_idempotency_key",
                           new_callable=AsyncMock, return_value=None):
                    with patch("agents.doc_generator.agent.create_task", new_callable=AsyncMock):
                        with patch("agents.doc_generator.agent.update_service_delivery",
                                   new_callable=AsyncMock, return_value=sd):
                            # Artefatto già esistente
                            with patch("agents.doc_generator.agent.file_exists",
                                       new_callable=AsyncMock, return_value=True):
                                with patch("agents.doc_generator.agent.upload_bytes",
                                           new_callable=AsyncMock):
                                    agent = DocGeneratorAgent()
                                    agent._client = MagicMock()
                                    agent._client.messages.create = AsyncMock(return_value=mock_msg)

                                    result = await agent.execute(task, db_session)

    assert result.success is True
