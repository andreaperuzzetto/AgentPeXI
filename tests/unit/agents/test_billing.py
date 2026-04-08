"""Test unit per agents/billing/agent.py"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.billing.agent import BillingAgent
from agents.models import AgentToolError
from tests.fixtures.leads import make_client, make_deal, make_lead
from tests.fixtures.tasks import make_task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_invoice(deal_id=None, amount_cents=105000, milestone="deposit", status="draft"):
    inv = MagicMock()
    inv.id = uuid.uuid4()
    inv.deal_id = deal_id or uuid.uuid4()
    inv.amount_cents = amount_cents
    inv.milestone = milestone
    inv.status = status
    inv.invoice_number = "2026-001"
    inv.due_date = date.today() + timedelta(days=30)
    inv.tax_rate_pct = 22.00
    return inv


# ---------------------------------------------------------------------------
# Happy path — create_invoice (deposito)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_billing_create_deposit_invoice(db_session):
    """BillingAgent crea fattura di acconto (milestone=deposit)."""
    lead = make_lead()
    deal = make_deal(
        service_type="web_design",
        status="client_approved",
        proposal_human_approved=True,
        total_price_eur=350000,
        deposit_pct=30,
    )
    client = make_client(lead_id=lead.id)
    invoice = _make_invoice(deal_id=deal.id)

    task = make_task(
        agent="billing",
        payload={
            "deal_id": str(deal.id),
            "client_id": str(client.id),
            "action": "create_invoice",
            "milestone": "deposit",
            "dry_run": True,
        },
    )

    with patch("agents.billing.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.billing.agent.get_client", new_callable=AsyncMock, return_value=client):
            with patch("agents.billing.agent.get_lead", new_callable=AsyncMock, return_value=lead):
                with patch("agents.billing.agent.get_task_by_idempotency_key",
                           new_callable=AsyncMock, return_value=None):
                    with patch("agents.billing.agent.create_task", new_callable=AsyncMock):
                        with patch("agents.billing.agent.create_invoice",
                                   new_callable=AsyncMock, return_value=invoice):
                            agent = BillingAgent()
                            result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Happy path — send_invoice
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_billing_send_invoice(db_session):
    """BillingAgent invia fattura via email."""
    lead = make_lead()
    deal = make_deal(status="client_approved", total_price_eur=350000)
    client = make_client(lead_id=lead.id, contact_email="client@example.com", contact_name="Mario Rossi")
    invoice = _make_invoice(deal_id=deal.id, status="draft")

    task = make_task(
        agent="billing",
        payload={
            "deal_id": str(deal.id),
            "client_id": str(client.id),
            "action": "send_invoice",
            "invoice_id": str(invoice.id),
            "invoice_number": "2026-001",
            "amount_cents": 105000,
            "total_cents": 105000,
            "due_date": "2026-12-31",
            "milestone": "deposit",
            "dry_run": True,
        },
    )

    with patch("agents.billing.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.billing.agent.get_client", new_callable=AsyncMock, return_value=client):
            with patch("agents.billing.agent.get_lead", new_callable=AsyncMock, return_value=lead):
                with patch("agents.billing.agent.get_task_by_idempotency_key",
                           new_callable=AsyncMock, return_value=None):
                    with patch("agents.billing.agent.create_task", new_callable=AsyncMock):
                        agent = BillingAgent()
                        result = await agent.execute(task, db_session)

    assert result.success is True


# ---------------------------------------------------------------------------
# Azione non valida
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_billing_invalid_action_raises_tool_error(db_session):
    task = make_task(
        agent="billing",
        payload={
            "deal_id": str(uuid.uuid4()),
            "client_id": str(uuid.uuid4()),
            "action": "delete_invoice",
        },
    )

    deal = make_deal()
    with patch("agents.billing.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        agent = BillingAgent()
        with pytest.raises(AgentToolError) as exc_info:
            await agent.execute(task, db_session)

    assert "validation" in exc_info.value.code


# ---------------------------------------------------------------------------
# Deal non trovato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_billing_deal_not_found(db_session):
    task = make_task(
        agent="billing",
        payload={
            "deal_id": str(uuid.uuid4()),
            "client_id": str(uuid.uuid4()),
            "action": "create_invoice",
            "milestone": "deposit",
        },
    )

    with patch("agents.billing.agent.get_deal", new_callable=AsyncMock, return_value=None):
        agent = BillingAgent()
        with pytest.raises(AgentToolError) as exc_info:
            await agent.execute(task, db_session)

    assert exc_info.value.code == "tool_db_deal_not_found"


# ---------------------------------------------------------------------------
# Idempotenza — invoice già creata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_billing_idempotency_invoice_already_created(db_session):
    """BillingAgent non ricrea fattura se trova la chiave idempotenza completata."""
    deal = make_deal(total_price_eur=350000)
    client = make_client()
    existing_invoice = _make_invoice(deal_id=deal.id, status="sent")

    from types import SimpleNamespace

    existing_task = SimpleNamespace(
        id=uuid.uuid4(),
        status="completed",
        output={"invoice_id": str(existing_invoice.id)},
    )

    task = make_task(
        agent="billing",
        payload={
            "deal_id": str(deal.id),
            "client_id": str(client.id),
            "action": "create_invoice",
            "milestone": "deposit",
        },
    )

    with patch("agents.billing.agent.get_deal", new_callable=AsyncMock, return_value=deal):
        with patch("agents.billing.agent.get_client", new_callable=AsyncMock, return_value=client):
            with patch("agents.billing.agent.get_task_by_idempotency_key",
                       new_callable=AsyncMock, return_value=existing_task):
                agent = BillingAgent()
                result = await agent.execute(task, db_session)

    assert result.success is True
