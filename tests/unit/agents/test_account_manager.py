"""Test unit per agents/account_manager/agent.py"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.account_manager.agent import AccountManagerAgent
from agents.models import AgentToolError
from tests.fixtures.leads import make_client, make_deal, make_lead
from tests.fixtures.tasks import make_task


# ---------------------------------------------------------------------------
# Happy path — onboarding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_account_manager_onboarding_happy_path(db_session):
    """AccountManager esegue onboarding email in dry_run."""
    lead = make_lead()
    deal = make_deal(
        service_type="web_design",
        status="client_approved",
        proposal_human_approved=True,
    )
    client = make_client(lead_id=lead.id, contact_email="client@example.com", contact_name="Mario Rossi")

    task = make_task(
        agent="account_manager",
        payload={
            "deal_id": str(deal.id),
            "client_id": str(client.id),
            "action": "onboarding",
            "dry_run": True,
        },
    )

    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="Benvenuto in AgentPeXI! Siamo pronti per iniziare.")]

    with patch("agents.account_manager.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.account_manager.agent.get_client",
                   new_callable=AsyncMock, return_value=client):
            with patch("agents.account_manager.agent.get_lead",
                       new_callable=AsyncMock, return_value=lead):
                with patch("agents.account_manager.agent.get_task_by_idempotency_key",
                           new_callable=AsyncMock, return_value=None):
                    with patch("agents.account_manager.agent.create_task",
                               new_callable=AsyncMock):
                        with patch("agents.account_manager.agent.send_email",
                                   new_callable=AsyncMock,
                                   return_value={"message_id": "msg-001", "thread_id": "th-001"}):
                            with patch("agents.account_manager.agent.log_email",
                                       new_callable=AsyncMock):
                                with patch("agents.account_manager.agent.update_deal",
                                           new_callable=AsyncMock, return_value=deal):
                                    agent = AccountManagerAgent()
                                    agent._client = MagicMock()
                                    agent._client.messages.create = AsyncMock(return_value=mock_msg)

                                    result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Happy path — NPS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_account_manager_nps_creates_record(db_session):
    """AccountManager crea NPS record e invia survey."""
    lead = make_lead()
    deal = make_deal(service_type="consulting", status="active")
    client = make_client(lead_id=lead.id, contact_email="client@example.com")

    task = make_task(
        agent="account_manager",
        payload={
            "deal_id": str(deal.id),
            "client_id": str(client.id),
            "action": "nps",
            "dry_run": True,
        },
    )

    nps_record = MagicMock()
    nps_record.id = uuid.uuid4()

    with patch("agents.account_manager.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.account_manager.agent.get_client",
                   new_callable=AsyncMock, return_value=client):
            with patch("agents.account_manager.agent.get_lead",
                       new_callable=AsyncMock, return_value=lead):
                with patch("agents.account_manager.agent.get_task_by_idempotency_key",
                           new_callable=AsyncMock, return_value=None):
                    with patch("agents.account_manager.agent.create_task",
                               new_callable=AsyncMock):
                        with patch("agents.account_manager.agent.create_nps_record",
                                   new_callable=AsyncMock, return_value=nps_record):
                            with patch("agents.account_manager.agent.send_email",
                                       new_callable=AsyncMock,
                                       return_value={"message_id": "msg-002", "thread_id": "th-002"}):
                                with patch("agents.account_manager.agent.log_email",
                                           new_callable=AsyncMock):
                                    agent = AccountManagerAgent()
                                    agent._client = MagicMock()
                                    result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Azione non valida
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_account_manager_invalid_action(db_session):
    task = make_task(
        agent="account_manager",
        payload={
            "deal_id": str(uuid.uuid4()),
            "client_id": str(uuid.uuid4()),
            "action": "delete_everything",
        },
    )

    deal = make_deal()
    with patch("agents.account_manager.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        agent = AccountManagerAgent()
        with pytest.raises(AgentToolError) as exc_info:
            await agent.execute(task, db_session)

    assert "validation" in exc_info.value.code or "invalid" in exc_info.value.code.lower()


# ---------------------------------------------------------------------------
# Deal non trovato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_account_manager_deal_not_found(db_session):
    task = make_task(
        agent="account_manager",
        payload={
            "deal_id": str(uuid.uuid4()),
            "client_id": str(uuid.uuid4()),
            "action": "onboarding",
        },
    )

    with patch("agents.account_manager.agent.get_deal", new_callable=AsyncMock, return_value=None):
        agent = AccountManagerAgent()
        with pytest.raises(AgentToolError) as exc_info:
            await agent.execute(task, db_session)

    assert exc_info.value.code == "tool_db_deal_not_found"
