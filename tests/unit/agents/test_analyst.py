"""Test unit per agents/analyst/agent.py"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.analyst.agent import AnalystAgent
from agents.models import AgentToolError
from tests.fixtures.leads import make_lead
from tests.fixtures.tasks import make_task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_claude_response(score: int = 72, service_type: str = "web_design") -> dict:
    """Risposta LLM valida per l'analyst — struttura signals attesa dall'agente.
    
    Segnali web_design: no_website(40) + outdated_website(32) + no_social_presence(20) = 92/125 * 100 = 73.6
    * 1.10 (horeca) = 80.96 + 8 (rating bonus) = 88 → ben sopra la soglia 65
    """
    return {
        "signals": {
            "web_design": {
                "no_website": True,           # weight 40
                "outdated_website": True,     # weight 32
                "no_social_presence": True,  # weight 20
                "poor_brand_image": False,
                "low_google_rating": False,
                "few_google_reviews": False,
            },
            "consulting": {
                "high_review_volume": True,
            },
            "digital_maintenance": {
                "existing_digital_presence": False,
                "high_update_frequency_sector": False,
            },
        },
        "suggested_service_type": service_type,
        "gap_summary": "Il business non ha sito web e ha buone recensioni.",
        "estimated_value_eur": 3500,
    }


async def _patch_analyst_llm(mocker, response: dict):
    """Patcha il client Anthropic dell'analyst."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=str(response).replace("'", '"'))]

    import json
    mock_msg.content = [MagicMock(text=json.dumps(response))]

    mock_create = AsyncMock(return_value=mock_msg)

    # Patch a livello di istanza dopo creazione
    return mock_create


# ---------------------------------------------------------------------------
# Happy path — lead qualificato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyst_qualifies_lead(db_session):
    """Analyst qualifica lead con score >= 65."""
    import json

    lead = make_lead(
        google_place_id="ChIJanalyst001",
        business_name="Ristorante Roma Test",
        google_rating=4.5,
        google_review_count=120,
        website_url=None,
        sector="horeca",
        status="discovered",
    )
    lead.gap_signals = None
    lead.phone = "REDACTED"  # non PII reale — evita il malus -20 della formula

    task = make_task(
        agent="analyst",
        payload={"lead_id": str(lead.id), "dry_run": True},
    )

    llm_response = json.dumps(_mock_claude_response(score=75))
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=llm_response)]

    with patch("agents.analyst.agent.get_lead", new_callable=AsyncMock, return_value=lead):
        with patch("agents.analyst.agent.get_task_by_idempotency_key", new_callable=AsyncMock, return_value=None):
            with patch("agents.analyst.agent.update_lead", new_callable=AsyncMock, return_value=lead):
                with patch("agents.analyst.agent.create_task", new_callable=AsyncMock):
                    agent = AnalystAgent()
                    agent._client = MagicMock()
                    agent._client.messages.create = AsyncMock(return_value=mock_msg)

                    result = await agent.execute(task, db_session)

    assert result.success is True
    assert result.output["leads_analyzed"] >= 1


# ---------------------------------------------------------------------------
# Lead non trovato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyst_lead_not_found_raises_tool_error(db_session):
    task = make_task(
        agent="analyst",
        payload={"lead_id": str(uuid.uuid4())},
    )

    with patch("agents.analyst.agent.get_lead", new_callable=AsyncMock, return_value=None):
        agent = AnalystAgent()
        with pytest.raises(AgentToolError) as exc_info:
            await agent.execute(task, db_session)

    assert exc_info.value.code == "tool_db_lead_not_found"


# ---------------------------------------------------------------------------
# Payload mancante
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyst_missing_payload_raises_tool_error(db_session):
    task = make_task(agent="analyst", payload={})
    agent = AnalystAgent()
    with pytest.raises(AgentToolError) as exc_info:
        await agent.execute(task, db_session)

    assert exc_info.value.code == "validation_missing_payload_field"


# ---------------------------------------------------------------------------
# Idempotenza — task già completato viene skippato
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyst_idempotent_skip(db_session):
    """Analyst salta il lead se trova una chiave idempotenza già completata."""
    lead = make_lead(status="discovered")
    task = make_task(
        agent="analyst",
        payload={"lead_id": str(lead.id)},
    )

    from types import SimpleNamespace

    existing_task = SimpleNamespace(
        id=uuid.uuid4(),
        status="completed",
        output={"analyzed": True},
    )

    with patch("agents.analyst.agent.get_lead", new_callable=AsyncMock, return_value=lead):
        with patch("agents.analyst.agent.get_task_by_idempotency_key",
                   new_callable=AsyncMock, return_value=existing_task):
            agent = AnalystAgent()
            result = await agent.execute(task, db_session)

    assert result.success is True
    assert result.output["leads_skipped"] == 1


# ---------------------------------------------------------------------------
# Lead già analizzato — skippato per status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyst_skip_already_qualified_lead(db_session):
    """Analyst salta lead con status già qualificato."""
    lead = make_lead(status="qualified")
    task = make_task(
        agent="analyst",
        payload={"lead_id": str(lead.id)},
    )

    with patch("agents.analyst.agent.get_lead", new_callable=AsyncMock, return_value=lead):
        agent = AnalystAgent()
        result = await agent.execute(task, db_session)

    assert result.success is True
    assert result.output.get("leads_skipped", 0) >= 1
