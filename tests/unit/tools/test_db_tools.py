"""Test unit per tools/db_tools.py"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fixtures.leads import make_deal, make_lead
from tools.db_tools import (
    LeadAlreadyExistsError,
    MaxProposalVersionsError,
    create_lead,
    get_deal,
    get_lead,
    get_lead_by_place_id,
    get_task_by_idempotency_key,
    update_deal,
    update_lead,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scalar_result(value):
    """Mocked execute().scalar_one_or_none() chain."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar_one.return_value = value
    return result


def _scalar_none():
    return _scalar_result(None)


# ---------------------------------------------------------------------------
# get_lead
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_lead_found():
    lead = make_lead()
    db = AsyncMock()
    db.execute.return_value = _scalar_result(lead)

    result = await get_lead(lead.id, db)

    assert result is lead
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_lead_not_found():
    db = AsyncMock()
    db.execute.return_value = _scalar_none()

    result = await get_lead(uuid.uuid4(), db)

    assert result is None


# ---------------------------------------------------------------------------
# get_lead_by_place_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_lead_by_place_id_found():
    lead = make_lead(google_place_id="ChIJtest123")
    db = AsyncMock()
    db.execute.return_value = _scalar_result(lead)

    result = await get_lead_by_place_id("ChIJtest123", db)

    assert result is lead


@pytest.mark.asyncio
async def test_get_lead_by_place_id_not_found():
    db = AsyncMock()
    db.execute.return_value = _scalar_none()

    result = await get_lead_by_place_id("ChIJnotexist", db)

    assert result is None


# ---------------------------------------------------------------------------
# create_lead
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_lead_happy_path():
    db = AsyncMock()
    # Simula nessun lead esistente
    db.execute.return_value = _scalar_none()

    data = {
        "google_place_id": "ChIJnew123",
        "business_name": "Nuovo Bar",
        "sector": "horeca",
    }
    with patch("tools.db_tools.get_lead_by_place_id", new_callable=AsyncMock, return_value=None):
        with patch("tools.db_tools.Lead") as MockLead:
            mock_instance = MagicMock()
            MockLead.return_value = mock_instance
            result = await create_lead(data, db)

    db.add.assert_called_once()
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_create_lead_duplicate_raises():
    existing_lead = make_lead(google_place_id="ChIJdup")
    db = AsyncMock()

    with patch("tools.db_tools.get_lead_by_place_id", new_callable=AsyncMock, return_value=existing_lead):
        with pytest.raises(LeadAlreadyExistsError):
            await create_lead({"google_place_id": "ChIJdup", "business_name": "Dup", "sector": "horeca"}, db)


# ---------------------------------------------------------------------------
# update_lead
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_lead_sets_updated_at():
    lead = make_lead()
    db = AsyncMock()
    db.execute.return_value = _scalar_result(lead)

    result = await update_lead(lead.id, {"status": "qualified"}, db)

    # execute deve essere chiamato almeno 2 volte (UPDATE + SELECT)
    assert db.execute.await_count >= 2


# ---------------------------------------------------------------------------
# get_deal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_deal_found():
    deal = make_deal()
    db = AsyncMock()
    db.execute.return_value = _scalar_result(deal)

    result = await get_deal(deal.id, db)

    assert result is deal


@pytest.mark.asyncio
async def test_get_deal_not_found():
    db = AsyncMock()
    db.execute.return_value = _scalar_none()

    result = await get_deal(uuid.uuid4(), db)

    assert result is None


# ---------------------------------------------------------------------------
# update_deal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_deal_success():
    deal = make_deal()
    db = AsyncMock()
    db.execute.return_value = _scalar_result(deal)

    result = await update_deal(deal.id, {"status": "proposal_sent"}, db)

    assert db.execute.await_count >= 2


# ---------------------------------------------------------------------------
# get_task_by_idempotency_key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_task_by_idempotency_key_found():
    from types import SimpleNamespace

    task_orm = SimpleNamespace(
        id=uuid.uuid4(),
        idempotency_key="test-key",
        status="completed",
        output={"result": "ok"},
    )

    db = AsyncMock()
    db.execute.return_value = _scalar_result(task_orm)

    result = await get_task_by_idempotency_key("test-key", db)

    assert result is task_orm


@pytest.mark.asyncio
async def test_get_task_by_idempotency_key_not_found():
    db = AsyncMock()
    db.execute.return_value = _scalar_none()

    result = await get_task_by_idempotency_key("nonexistent-key", db)

    assert result is None
