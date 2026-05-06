"""A.4 T3 Part C: test /api/etsy/niches merges warmup candidates."""
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
async def test_niches_includes_warmup_candidates(app):
    """GET /api/etsy/niches merges warmup candidates from ChromaDB."""
    # Mock DB response (1 niche from DB)
    mock_db = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.fetchall.return_value = [
        {
            "niche": "digital prints",
            "product_type": "print",
            "performance_score": 0.9,
            "confidence_level": "high",
            "avg_ctr": 0.05,
            "total_orders": 100,
            "total_listings": 50,
            "total_revenue_eur": 1000.0,
            "last_updated_at": 1234567890.0,
            "audience_target": None,
            "expansion_potential": None,
            "entry_score": 0.85,
            "tier": 1,
            "avg_price_eur": 20.0,
            "google_trend_score": 0.7,
            "section_name": "Wall Art"
        }
    ]
    mock_db.execute.return_value = mock_cursor
    
    # Mock memory with ChromaDB query returning 1 warmup candidate
    mock_memory = MagicMock()
    mock_memory.get_db = AsyncMock(return_value=mock_db)
    mock_memory.query_insights_by_type = AsyncMock(return_value=[
        {
            "id": "warmup1",
            "text": "Warmup candidate text",
            "metadata": {
                "type": "warmup_candidate",
                "niche": "wall art",
                "product_type": "canvas",
                "score": "0.75",
                "status": "pending_warmup",
                "section": "Home Decor",
                "source": "coldstart_bootstrap"
            }
        }
    ])
    
    original_memory = state_mod.memory
    state_mod.memory = mock_memory
    
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/etsy/niches")
        
        assert resp.status_code == 200
        data = resp.json()
        assert "niches" in data
        niches = data["niches"]
        
        # Should have 2 niches total: 1 from DB + 1 warmup candidate
        assert len(niches) == 2
        
        # Find warmup candidate
        warmup = [n for n in niches if n.get("source_type") == "warmup_candidate"]
        assert len(warmup) == 1
        assert warmup[0]["niche"] == "wall art"
        assert warmup[0]["product_type"] == "canvas"
        assert warmup[0]["performance_score"] == 0.75
        assert warmup[0]["confidence_level"] == "pending_warmup"
        
        # Verify query_insights_by_type was called
        mock_memory.query_insights_by_type.assert_called_once_with("warmup_candidate")
        
    finally:
        state_mod.memory = original_memory


@pytest.mark.asyncio
async def test_niches_deduplicates_warmup_candidates(app):
    """Warmup candidate is dropped if DB already has same niche+product_type."""
    # Mock DB response with niche="wall art", product_type="print"
    mock_db = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.fetchall.return_value = [
        {
            "niche": "wall art",
            "product_type": "print",
            "performance_score": 0.9,
            "confidence_level": "high",
            "avg_ctr": 0.05,
            "total_orders": 100,
            "total_listings": 50,
            "total_revenue_eur": 1000.0,
            "last_updated_at": 1234567890.0,
            "audience_target": None,
            "expansion_potential": None,
            "entry_score": 0.85,
            "tier": 1,
            "avg_price_eur": 20.0,
            "google_trend_score": 0.7,
            "section_name": "Wall Art"
        }
    ]
    mock_db.execute.return_value = mock_cursor
    
    # Mock warmup candidate with same niche+product_type
    mock_memory = MagicMock()
    mock_memory.get_db = AsyncMock(return_value=mock_db)
    mock_memory.query_insights_by_type = AsyncMock(return_value=[
        {
            "id": "warmup1",
            "text": "Duplicate warmup candidate",
            "metadata": {
                "type": "warmup_candidate",
                "niche": "wall art",
                "product_type": "print",  # Same as DB
                "score": "0.75",
                "status": "pending_warmup",
                "section": "Wall Art",
                "source": "coldstart_bootstrap"
            }
        }
    ])
    
    original_memory = state_mod.memory
    state_mod.memory = mock_memory
    
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/etsy/niches")
        
        assert resp.status_code == 200
        data = resp.json()
        niches = data["niches"]
        
        # Should have exactly 1 niche (DB version wins, warmup dropped)
        assert len(niches) == 1
        assert niches[0]["niche"] == "wall art"
        assert niches[0]["product_type"] == "print"
        assert niches[0]["performance_score"] == 0.9  # DB score, not warmup score
        assert niches[0].get("source_type") is None  # DB version has no source_type
        
    finally:
        state_mod.memory = original_memory


@pytest.mark.asyncio
async def test_niches_handles_warmup_without_product_type(app):
    """Warmup candidate without product_type is handled correctly."""
    mock_db = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.fetchall.return_value = []
    mock_db.execute.return_value = mock_cursor
    
    # Warmup candidate without product_type
    mock_memory = MagicMock()
    mock_memory.get_db = AsyncMock(return_value=mock_db)
    mock_memory.query_insights_by_type = AsyncMock(return_value=[
        {
            "id": "warmup1",
            "text": "Candidate without product type",
            "metadata": {
                "type": "warmup_candidate",
                "niche": "home decor",
                "score": "0.65",
                "status": "pending_warmup",
                "source": "coldstart_bootstrap"
                # product_type intentionally missing
            }
        }
    ])
    
    original_memory = state_mod.memory
    state_mod.memory = mock_memory
    
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/etsy/niches")
        
        assert resp.status_code == 200
        data = resp.json()
        niches = data["niches"]
        
        assert len(niches) == 1
        assert niches[0]["niche"] == "home decor"
        assert niches[0]["product_type"] is None
        assert niches[0]["source_type"] == "warmup_candidate"
        
    finally:
        state_mod.memory = original_memory
