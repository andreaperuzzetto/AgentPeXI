"""Test unit per agents/scout/agent.py"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.models import AgentResult, GateNotApprovedError
from agents.scout.agent import ScoutAgent
from tests.fixtures.leads import make_lead
from tests.fixtures.tasks import make_task


# ---------------------------------------------------------------------------
# Dati di fixture
# ---------------------------------------------------------------------------

_PLACE_1 = {
    "google_place_id": "ChIJtest001",
    "business_name": "Bar Roma Test",
    "address": "Via Roma 1",
    "city": "Roma",
    "region": "Lazio",
    "country": "IT",
    "latitude": 41.9028,
    "longitude": 12.4964,
    "google_rating": 4.2,
    "google_review_count": 87,
    "google_category": "Bar",
    "website_url": None,
    "phone": None,
}

_PLACE_2 = {**_PLACE_1, "google_place_id": "ChIJtest002", "business_name": "Caffè Milano Test"}
_PLACE_3 = {**_PLACE_1, "google_place_id": "ChIJtest003", "business_name": "Ristorante Napoli"}


# ---------------------------------------------------------------------------
# Happy path — dry_run=True evita scritture DB reali
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scout_happy_path_dry_run(db_session):
    """Scout trova lead e li conta in dry_run senza scrivere su DB."""
    task = make_task(
        agent="scout",
        payload={"zone": "Roma, Italia", "sector": "horeca", "dry_run": True},
    )

    with patch("agents.scout.agent.search_businesses", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [_PLACE_1, _PLACE_2, _PLACE_3]
        with patch("agents.scout.agent.get_lead_by_place_id", new_callable=AsyncMock, return_value=None):
            agent = ScoutAgent()
            result = await agent.execute(task, db_session)

    assert result.success is True
    assert result.output["leads_found"] == 3
    assert result.output["leads_written"] == 3  # dry_run: counted but not DB-written
    assert result.output["skipped_duplicates"] == 0


@pytest.mark.asyncio
async def test_scout_writes_to_db(db_session):
    """Scout crea lead nel DB quando dry_run=False."""
    task = make_task(
        agent="scout",
        payload={"zone": "Roma, Italia", "sector": "horeca", "dry_run": False},
    )

    with patch("agents.scout.agent.search_businesses", new_callable=AsyncMock, return_value=[_PLACE_1]):
        with patch("agents.scout.agent.get_lead_by_place_id", new_callable=AsyncMock, return_value=None):
            with patch("agents.scout.agent.create_lead", new_callable=AsyncMock) as mock_create:
                mock_create.return_value = make_lead()
                agent = ScoutAgent()
                result = await agent.execute(task, db_session)

    assert result.success is True
    assert result.output["leads_written"] == 1
    mock_create.assert_awaited_once()


# ---------------------------------------------------------------------------
# Duplicati skippati
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scout_duplicate_lead_skipped(db_session):
    """Scout salta lead già presenti per place_id (pre-check con get_lead_by_place_id)."""
    existing = make_lead(google_place_id=_PLACE_1["google_place_id"])
    task = make_task(
        agent="scout",
        payload={"zone": "Roma, Italia", "sector": "horeca", "dry_run": False},
    )

    with patch("agents.scout.agent.search_businesses", new_callable=AsyncMock, return_value=[_PLACE_1]):
        with patch("agents.scout.agent.get_lead_by_place_id", new_callable=AsyncMock, return_value=existing):
            agent = ScoutAgent()
            result = await agent.execute(task, db_session)

    assert result.success is True
    assert result.output["leads_written"] == 0
    assert result.output["skipped_duplicates"] == 1


# ---------------------------------------------------------------------------
# Nessun risultato — gate error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scout_no_results_raises_gate_error(db_session):
    """Scout solleva GateNotApprovedError quando non trova alcun risultato."""
    task = make_task(
        agent="scout",
        payload={"zone": "Comune Sconosciuto, Italia", "sector": "horeca"},
    )

    with patch("agents.scout.agent.search_businesses", new_callable=AsyncMock, return_value=[]):
        agent = ScoutAgent()
        with pytest.raises(GateNotApprovedError) as exc_info:
            await agent.execute(task, db_session)

    assert "no_results" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Settore non valido
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scout_invalid_sector_raises_tool_error(db_session):
    """Scout solleva AgentToolError per settore non presente in sectors.yaml."""
    from agents.models import AgentToolError

    task = make_task(
        agent="scout",
        payload={"zone": "Roma, Italia", "sector": "settore_inesistente"},
    )

    agent = ScoutAgent()
    with pytest.raises(AgentToolError) as exc_info:
        await agent.execute(task, db_session)

    assert exc_info.value.code == "validation_invalid_sector"


# ---------------------------------------------------------------------------
# Payload mancante
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scout_missing_zone_raises_tool_error(db_session):
    from agents.models import AgentToolError

    task = make_task(agent="scout", payload={"sector": "horeca"})
    agent = ScoutAgent()
    with pytest.raises(AgentToolError) as exc_info:
        await agent.execute(task, db_session)

    assert exc_info.value.code == "validation_missing_payload_field"


# ---------------------------------------------------------------------------
# Race condition: LeadAlreadyExistsError durante create_lead
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scout_race_condition_create_lead(db_session):
    """Race condition tra pre-check e INSERT → skipped_duplicates++."""
    from tools.db_tools import LeadAlreadyExistsError

    task = make_task(
        agent="scout",
        payload={"zone": "Roma, Italia", "sector": "horeca", "dry_run": False},
    )

    with patch("agents.scout.agent.search_businesses", new_callable=AsyncMock, return_value=[_PLACE_1]):
        with patch("agents.scout.agent.get_lead_by_place_id", new_callable=AsyncMock, return_value=None):
            with patch("agents.scout.agent.create_lead", new_callable=AsyncMock,
                       side_effect=LeadAlreadyExistsError("race")):
                agent = ScoutAgent()
                result = await agent.execute(task, db_session)

    assert result.success is True
    assert result.output["skipped_duplicates"] == 1
