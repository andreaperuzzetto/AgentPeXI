"""A.2 Task 3: test API endpoints for shop identity."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
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


@pytest.fixture
async def seeded_shop_identity_db():
    """Fixture that seeds shop_identity table with 3 records, record 1 is active."""
    import aiosqlite
    
    # Create in-memory DB
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row  # Enable dict-like access
    
    # Create shop_identity table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS shop_identity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aesthetic_name TEXT NOT NULL,
            palette_primary TEXT NOT NULL,
            palette_secondary TEXT NOT NULL,
            palette_accent TEXT NOT NULL,
            mockup_style TEXT NOT NULL,
            tone TEXT NOT NULL,
            logo_path TEXT,
            banner_path TEXT,
            approved_at REAL NOT NULL,
            approved_by TEXT NOT NULL,
            is_active INTEGER DEFAULT 0
        )
    """)
    await db.commit()
    
    # Insert 3 test records
    await db.executemany(
        """
        INSERT INTO shop_identity 
        (aesthetic_name, palette_primary, palette_secondary, palette_accent, mockup_style, tone, logo_path, banner_path, approved_at, approved_by, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("Option A", "#FF0000", "#00FF00", "#0000FF", "flat", "professional", None, None, 1234567890.0, "andrea", 1),
            ("Option B", "#AA0000", "#00AA00", "#0000AA", "realistic", "casual", None, None, 1234567891.0, "andrea", 0),
            ("Option C", "#550000", "#005500", "#000055", "minimalist", "friendly", None, None, 1234567892.0, "andrea", 0),
        ]
    )
    await db.commit()
    
    # Mock state.memory to return this DB
    mock_memory = MagicMock()
    mock_memory.get_db = AsyncMock(return_value=db)
    original_memory = state_mod.memory
    state_mod.memory = mock_memory
    
    yield db
    
    # Cleanup
    state_mod.memory = original_memory
    await db.close()


@pytest.mark.asyncio
async def test_get_style_guide_options_empty(app):
    """Returns empty list when no options exist."""
    # Mock empty DB
    db = await __import__("aiosqlite").connect(":memory:")
    db.row_factory = __import__("aiosqlite").Row  # Enable dict-like access
    await db.execute("""
        CREATE TABLE IF NOT EXISTS shop_identity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aesthetic_name TEXT NOT NULL,
            palette_primary TEXT NOT NULL,
            palette_secondary TEXT NOT NULL,
            palette_accent TEXT NOT NULL,
            mockup_style TEXT NOT NULL,
            tone TEXT NOT NULL,
            logo_path TEXT,
            banner_path TEXT,
            approved_at REAL NOT NULL,
            approved_by TEXT NOT NULL,
            is_active INTEGER DEFAULT 0
        )
    """)
    await db.commit()
    
    mock_memory = MagicMock()
    mock_memory.get_db = AsyncMock(return_value=db)
    original_memory = state_mod.memory
    state_mod.memory = mock_memory
    
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/etsy/style-guide-options")
    finally:
        state_mod.memory = original_memory
        await db.close()
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["options"] == []


@pytest.mark.asyncio
async def test_get_style_guide_options_with_data(app, seeded_shop_identity_db):
    """Returns all shop identity records as options."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/etsy/style-guide-options")
    
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["options"]) == 3
    option = data["options"][0]
    assert "id" in option
    assert "aesthetic_name" in option
    assert "palette_primary" in option
    assert "palette_secondary" in option
    assert "palette_accent" in option
    assert "mockup_style" in option
    assert "tone" in option
    assert "is_active" in option


@pytest.mark.asyncio
async def test_get_shop_identity_no_active(app):
    """Returns null when no identity is active."""
    # Mock DB with no active identity
    db = await __import__("aiosqlite").connect(":memory:")
    db.row_factory = __import__("aiosqlite").Row  # Enable dict-like access
    await db.execute("""
        CREATE TABLE IF NOT EXISTS shop_identity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aesthetic_name TEXT NOT NULL,
            palette_primary TEXT NOT NULL,
            palette_secondary TEXT NOT NULL,
            palette_accent TEXT NOT NULL,
            mockup_style TEXT NOT NULL,
            tone TEXT NOT NULL,
            logo_path TEXT,
            banner_path TEXT,
            approved_at REAL NOT NULL,
            approved_by TEXT NOT NULL,
            is_active INTEGER DEFAULT 0
        )
    """)
    await db.commit()
    
    mock_memory = MagicMock()
    mock_memory.get_db = AsyncMock(return_value=db)
    original_memory = state_mod.memory
    state_mod.memory = mock_memory
    
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/etsy/shop-identity")
    finally:
        state_mod.memory = original_memory
        await db.close()
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["identity"] is None


@pytest.mark.asyncio
async def test_get_shop_identity_with_active(app, seeded_shop_identity_db):
    """Returns active identity when one is set."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/etsy/shop-identity")
    
    assert resp.status_code == 200
    data = resp.json()
    # seeded_shop_identity_db sets record 1 (Option A) as active
    assert data["identity"] is not None
    assert data["identity"]["aesthetic_name"] == "Option A"
    assert data["identity"]["is_active"] is True
