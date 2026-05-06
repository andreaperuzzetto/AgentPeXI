"""B-03: verifica endpoint POST /api/analytics/etsy-sync."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import apps.backend.api.state as state_mod
from apps.backend.api.routers import etsy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    """App FastAPI con etsy router, auth bypassed."""
    _app = FastAPI()
    _app.include_router(etsy.router)
    _app.dependency_overrides[state_mod.verify_personal_key] = lambda: None
    yield _app
    _app.dependency_overrides.clear()


async def _make_db_with_listing(tmp_path, listing_id: str = "listing_123"):
    """Crea MemoryBase reale con una riga in etsy_listings.

    MemoryBase.init() crea lo schema poi chiude la connessione (self._db = None).
    Riapriamo la connessione dopo init() così get_db() ritorna un oggetto valido
    per tutta la durata del test.
    """
    import aiosqlite
    from apps.backend.core._memory._base import MemoryBase

    mm = MemoryBase.__new__(MemoryBase)
    mm._db_path = str(tmp_path / "test.db")
    mm._chromadb_path = str(tmp_path / "chromadb")
    mm._db = None
    mm._chroma_collection = None
    mm._screen_memory_collection = None
    mm._personal_memory_collection = None
    mm._shared_memory_collection = None
    mm._ws_broadcaster = None
    mm._bridge_callback = None
    mm.mock_mode = False
    await mm.init()  # crea schema, poi chiude _db → _db = None

    # Riapre la connessione persistente (init() la chiude al termine)
    mm._db = await aiosqlite.connect(mm._db_path)
    mm._db.row_factory = aiosqlite.Row

    await mm._db.execute(
        "INSERT INTO etsy_listings (listing_id, niche, product_type) VALUES (?, ?, ?)",
        (listing_id, "party_printable", "printable_pdf"),
    )
    await mm._db.commit()
    return mm


_VALID_PAYLOAD = {
    "listing_id": "listing_123",
    "views": 42,
    "favorites": 7,
    "num_orders": 2,
    "revenue_eur": 9.98,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_etsy_sync_returns_200_and_ok(tmp_path, app):
    """POST /api/analytics/etsy-sync ritorna 200 con ok=True."""
    mem = await _make_db_with_listing(tmp_path)
    original_memory = state_mod.memory
    original_ll = state_mod.learning_loop
    state_mod.memory = mem
    state_mod.learning_loop = None

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/analytics/etsy-sync", json=_VALID_PAYLOAD)
    finally:
        state_mod.memory = original_memory
        state_mod.learning_loop = original_ll

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["listing_id"] == "listing_123"


@pytest.mark.asyncio
async def test_etsy_sync_inserts_row_in_listing_performance(tmp_path, app):
    """POST /api/analytics/etsy-sync scrive una riga in listing_performance."""
    import aiosqlite

    mem = await _make_db_with_listing(tmp_path)
    original_memory = state_mod.memory
    original_ll = state_mod.learning_loop
    state_mod.memory = mem
    state_mod.learning_loop = None

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/analytics/etsy-sync", json=_VALID_PAYLOAD)
    finally:
        state_mod.memory = original_memory
        state_mod.learning_loop = original_ll

    async with aiosqlite.connect(mem._db_path) as db:
        cur = await db.execute(
            "SELECT views, favorites, orders, revenue_eur, niche FROM listing_performance WHERE etsy_listing_id = ?",
            ("listing_123",),
        )
        row = await cur.fetchone()

    assert row is not None, "Nessuna riga inserita in listing_performance"
    views, favorites, orders, revenue, niche = row
    assert views == 42
    assert favorites == 7
    assert orders == 2
    assert revenue == pytest.approx(9.98)
    assert niche == "party_printable"


@pytest.mark.asyncio
async def test_etsy_sync_calls_learning_loop_when_set(tmp_path, app):
    """POST /api/analytics/etsy-sync chiama update_niche_intelligence() se learning_loop è impostato."""
    mem = await _make_db_with_listing(tmp_path)
    mock_ll = MagicMock()
    mock_ll.update_niche_intelligence = AsyncMock(return_value=5)

    original_memory = state_mod.memory
    original_ll = state_mod.learning_loop
    state_mod.memory = mem
    state_mod.learning_loop = mock_ll

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/analytics/etsy-sync", json=_VALID_PAYLOAD)
    finally:
        state_mod.memory = original_memory
        state_mod.learning_loop = original_ll

    assert resp.status_code == 200
    mock_ll.update_niche_intelligence.assert_called_once()


@pytest.mark.asyncio
async def test_etsy_sync_ok_when_learning_loop_none(tmp_path, app):
    """POST /api/analytics/etsy-sync non fallisce se learning_loop è None."""
    mem = await _make_db_with_listing(tmp_path)
    original_memory = state_mod.memory
    original_ll = state_mod.learning_loop
    state_mod.memory = mem
    state_mod.learning_loop = None

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/analytics/etsy-sync", json=_VALID_PAYLOAD)
    finally:
        state_mod.memory = original_memory
        state_mod.learning_loop = original_ll

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_etsy_sync_503_when_memory_none(app):
    """POST /api/analytics/etsy-sync ritorna 503 se state.memory è None."""
    original_memory = state_mod.memory
    state_mod.memory = None

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/analytics/etsy-sync", json=_VALID_PAYLOAD)
    finally:
        state_mod.memory = original_memory

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_etsy_sync_404_when_listing_not_found(tmp_path, app):
    """POST /api/analytics/etsy-sync ritorna 404 se listing_id non è in etsy_listings."""
    mem = await _make_db_with_listing(tmp_path, listing_id="other_listing")
    original_memory = state_mod.memory
    original_ll = state_mod.learning_loop
    state_mod.memory = mem
    state_mod.learning_loop = None

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Posting a listing_id that doesn't exist in etsy_listings
            resp = await client.post(
                "/api/analytics/etsy-sync",
                json={**_VALID_PAYLOAD, "listing_id": "nonexistent_999"},
            )
    finally:
        state_mod.memory = original_memory
        state_mod.learning_loop = original_ll

    assert resp.status_code == 404
