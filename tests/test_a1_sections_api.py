"""A.1: test endpoint GET /api/etsy/sections."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import apps.backend.api.state as state_mod
from apps.backend.api.routers import etsy


@pytest.fixture
def app():
    """App FastAPI with etsy router, auth bypassed."""
    _app = FastAPI()
    _app.include_router(etsy.router)
    _app.dependency_overrides[state_mod.verify_personal_key] = lambda: None
    yield _app
    _app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_sections_endpoint_returns_list(app):
    """GET /api/etsy/sections ritorna lista con campi attesi."""
    mock_sections_result = [
        {
            "section_id": "s1",
            "section_name": "Party & Celebrations",
            "listing_count": 5,
            "last_listing_at": "2026-05-01 12:00:00",
            "pending_uncategorized": 2,
        },
        {
            "section_id": "s2",
            "section_name": "Wedding",
            "listing_count": 3,
            "last_listing_at": None,
            "pending_uncategorized": 2,
        },
    ]

    mock_memory = MagicMock()
    mock_memory.get_db = AsyncMock(return_value=MagicMock())
    original_memory = state_mod.memory
    state_mod.memory = mock_memory

    try:
        with patch(
            "apps.backend.core.etsy_sections_service.EtsySectionsService.get_sections_with_uncategorized_counts",
            new_callable=AsyncMock,
            return_value=mock_sections_result,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/etsy/sections")
    finally:
        state_mod.memory = original_memory

    assert resp.status_code == 200
    data = resp.json()
    assert "sections" in data
    assert isinstance(data["sections"], list)
    assert len(data["sections"]) == 2
    s = data["sections"][0]
    assert s["section_id"] == "s1"
    assert s["section_name"] == "Party & Celebrations"
    assert s["listing_count"] == 5
    assert s["pending_uncategorized"] == 2


@pytest.mark.asyncio
async def test_get_sections_returns_503_when_memory_none(app):
    """GET /api/etsy/sections ritorna 503 se state.memory è None."""
    original_memory = state_mod.memory
    state_mod.memory = None
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/etsy/sections")
    finally:
        state_mod.memory = original_memory

    assert resp.status_code == 503
