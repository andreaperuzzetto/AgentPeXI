"""Extended tests — endpoint non coperti in test_api_routers.py.

Copre:
  - /api/pinterest/auth-status  (GET)
  - /api/pinterest/status       (GET)
  - /api/etsy/sections          (GET)
  - /api/etsy/shop-optimizer    (GET)
  - /api/etsy/shop-optimizer/preview (POST)
  - /api/etsy/style-guide-options    (GET)
  - /api/etsy/shop-identity          (GET)
  - /api/analytics/etsy-sync         (POST)
  - /api/etsy/niches/{niche}/competitor-analysis (GET)
  - /api/etsy/clusters               (GET)
  - /api/etsy/clusters/{cluster_id}  (GET)
  - /api/autopilot/pause             (POST — assicura auth 403)

Pattern identico a test_api_routers.py:
  httpx.AsyncClient + ASGITransport, dependency_overrides[verify_personal_key].
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import apps.backend.api.state as _state
from apps.backend.api.routers import autopilot, etsy
from apps.backend.api.routers import pinterest as pinterest_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_EXT_ROUTERS = [
    autopilot.router,
    etsy.router,
    pinterest_router.router,
]


@pytest.fixture(scope="module")
def ext_app():
    """App con tutti i router estesi; verify_personal_key bypassata."""
    _app = FastAPI()
    for r in _EXT_ROUTERS:
        _app.include_router(r)
    _app.dependency_overrides[_state.verify_personal_key] = lambda: None
    yield _app
    _app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def ext_unauth_app():
    """App senza override auth — per testare i 403."""
    _app = FastAPI()
    for r in _EXT_ROUTERS:
        _app.include_router(r)
    yield _app


@pytest.fixture
async def ext_client(ext_app):
    async with AsyncClient(
        transport=ASGITransport(app=ext_app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture
async def ext_unauth_client(ext_unauth_app):
    async with AsyncClient(
        transport=ASGITransport(app=ext_unauth_app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _mock_memory_with_db(db_mock: MagicMock) -> MagicMock:
    m = MagicMock()
    m.get_db = AsyncMock(return_value=db_mock)
    return m


# ---------------------------------------------------------------------------
# /api/pinterest/auth-status
# ---------------------------------------------------------------------------


async def test_pinterest_auth_status_no_memory(ext_client):
    """/api/pinterest/auth-status → 503 quando memory è None."""
    prev = _state.memory
    _state.memory = None
    try:
        r = await ext_client.get("/api/pinterest/auth-status")
    finally:
        _state.memory = prev
    assert r.status_code == 503
    assert "detail" in r.json()


async def test_pinterest_auth_status_requires_auth(ext_unauth_client):
    r = await ext_unauth_client.get("/api/pinterest/auth-status")
    assert r.status_code == 403


async def test_pinterest_auth_status_no_tokens(ext_client):
    """Se get_oauth_tokens restituisce None → connected=False."""
    mock_mem = MagicMock()
    mock_mem.get_oauth_tokens = AsyncMock(return_value=None)
    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await ext_client.get("/api/pinterest/auth-status")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert data["connected"] is False
    assert data["expires_at"] is None
    assert data["last_refresh"] is None


async def test_pinterest_auth_status_expired_token(ext_client):
    """Token scaduto → connected=False."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    mock_mem = MagicMock()
    mock_mem.get_oauth_tokens = AsyncMock(
        return_value={"expires_at": past, "updated_at": "2026-01-01T00:00:00+00:00"}
    )
    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await ext_client.get("/api/pinterest/auth-status")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert r.json()["connected"] is False


async def test_pinterest_auth_status_valid_token(ext_client):
    """Token valido non scaduto → connected=True."""
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_mem = MagicMock()
    mock_mem.get_oauth_tokens = AsyncMock(
        return_value={"expires_at": future, "updated_at": "2026-05-12T08:00:00+00:00"}
    )
    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await ext_client.get("/api/pinterest/auth-status")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert r.json()["connected"] is True


# ---------------------------------------------------------------------------
# /api/pinterest/status
# ---------------------------------------------------------------------------


async def test_pinterest_status_no_memory(ext_client):
    prev = _state.memory
    _state.memory = None
    try:
        r = await ext_client.get("/api/pinterest/status")
    finally:
        _state.memory = prev
    assert r.status_code == 503


async def test_pinterest_status_requires_auth(ext_unauth_client):
    r = await ext_unauth_client.get("/api/pinterest/status")
    assert r.status_code == 403


async def test_pinterest_status_with_memory(ext_client):
    """Stato completo con mock DB — verifica shape risposta."""
    mock_db = MagicMock()
    mock_db.execute_fetchall = AsyncMock(
        side_effect=[
            # pin counts row
            [{"pins_today": 2, "pins_queued": 1, "pins_failed": 0, "cost_today": 0.05}],
            # next_pin_at row
            [{"next_pin_at": None}],
            # boards rows
            [],
        ]
    )
    mock_mem = _mock_memory_with_db(mock_db)
    mock_mem.get_oauth_tokens = AsyncMock(return_value=None)

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await ext_client.get("/api/pinterest/status")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    data = r.json()
    assert "pins_today" in data
    assert "pins_queued" in data
    assert "pins_failed" in data
    assert "boards" in data
    assert isinstance(data["boards"], list)
    assert data["pins_today"] == 2
    assert data["connected"] is False


# ---------------------------------------------------------------------------
# /api/etsy/sections
# ---------------------------------------------------------------------------


async def test_etsy_sections_no_memory(ext_client):
    prev = _state.memory
    _state.memory = None
    try:
        r = await ext_client.get("/api/etsy/sections")
    finally:
        _state.memory = prev
    assert r.status_code == 503
    assert "detail" in r.json()


async def test_etsy_sections_requires_auth(ext_unauth_client):
    r = await ext_unauth_client.get("/api/etsy/sections")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# /api/etsy/shop-optimizer
# ---------------------------------------------------------------------------


async def test_etsy_shop_optimizer_no_memory(ext_client):
    """/api/etsy/shop-optimizer → status=unavailable quando memory=None."""
    prev = _state.memory
    _state.memory = None
    try:
        r = await ext_client.get("/api/etsy/shop-optimizer")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "unavailable"
    assert data["last_title"] is None


async def test_etsy_shop_optimizer_requires_auth(ext_unauth_client):
    r = await ext_unauth_client.get("/api/etsy/shop-optimizer")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# /api/etsy/shop-optimizer/preview
# ---------------------------------------------------------------------------


async def test_etsy_shop_optimizer_preview_no_optimizer(ext_client):
    """/api/etsy/shop-optimizer/preview → 503 quando shop_optimizer=None."""
    prev = _state.shop_optimizer
    _state.shop_optimizer = None
    try:
        r = await ext_client.post("/api/etsy/shop-optimizer/preview", json={})
    finally:
        _state.shop_optimizer = prev
    assert r.status_code == 503
    assert "detail" in r.json()


async def test_etsy_shop_optimizer_preview_requires_auth(ext_unauth_client):
    r = await ext_unauth_client.post("/api/etsy/shop-optimizer/preview", json={})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# /api/etsy/style-guide-options
# ---------------------------------------------------------------------------


async def test_etsy_style_guide_options_no_memory(ext_client):
    prev = _state.memory
    _state.memory = None
    try:
        r = await ext_client.get("/api/etsy/style-guide-options")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert r.json() == {"options": []}


async def test_etsy_style_guide_options_requires_auth(ext_unauth_client):
    r = await ext_unauth_client.get("/api/etsy/style-guide-options")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# /api/etsy/shop-identity
# ---------------------------------------------------------------------------


async def test_etsy_shop_identity_no_memory(ext_client):
    prev = _state.memory
    _state.memory = None
    try:
        r = await ext_client.get("/api/etsy/shop-identity")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert r.json() == {"identity": None}


async def test_etsy_shop_identity_requires_auth(ext_unauth_client):
    r = await ext_unauth_client.get("/api/etsy/shop-identity")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# /api/analytics/etsy-sync
# ---------------------------------------------------------------------------

_VALID_SYNC_BODY = {
    "listing_id": 123456,
    "views": 100,
    "favorites": 10,
    "num_orders": 3,
    "revenue_eur": 27.99,
}


async def test_etsy_analytics_sync_no_memory(ext_client):
    prev = _state.memory
    _state.memory = None
    try:
        r = await ext_client.post("/api/analytics/etsy-sync", json=_VALID_SYNC_BODY)
    finally:
        _state.memory = prev
    assert r.status_code == 503
    assert "detail" in r.json()


async def test_etsy_analytics_sync_requires_auth(ext_unauth_client):
    r = await ext_unauth_client.post("/api/analytics/etsy-sync", json=_VALID_SYNC_BODY)
    assert r.status_code == 403


async def test_etsy_analytics_sync_missing_required_fields(ext_client):
    """Body privo di campi obbligatori → 422 Unprocessable Entity."""
    r = await ext_client.post("/api/analytics/etsy-sync", json={})
    assert r.status_code == 422


async def test_etsy_analytics_sync_negative_listing_id(ext_client):
    """listing_id < 0 viola Field(ge=0) → 422."""
    bad_body = {**_VALID_SYNC_BODY, "listing_id": -1}
    r = await ext_client.post("/api/analytics/etsy-sync", json=bad_body)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# /api/etsy/niches/{niche}/competitor-analysis
# ---------------------------------------------------------------------------


async def test_competitor_analysis_no_memory(ext_client):
    """Quando memory=None la dependency restituisce None → available=False."""
    prev = _state.memory
    _state.memory = None
    try:
        r = await ext_client.get("/api/etsy/niches/svg-design/competitor-analysis")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False
    assert data["niche"] == "svg-design"


async def test_competitor_analysis_requires_auth(ext_unauth_client):
    r = await ext_unauth_client.get("/api/etsy/niches/svg-design/competitor-analysis")
    assert r.status_code == 403


async def test_competitor_analysis_with_memory_no_cache(ext_client):
    """query_chromadb restituisce lista vuota → available=False."""
    mock_mem = MagicMock()
    mock_mem.query_chromadb = AsyncMock(return_value=[])
    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await ext_client.get("/api/etsy/niches/wedding-planner/competitor-analysis")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False
    assert data["niche"] == "wedding-planner"


# ---------------------------------------------------------------------------
# /api/etsy/clusters
# ---------------------------------------------------------------------------


async def test_clusters_no_memory(ext_client):
    prev = _state.memory
    _state.memory = None
    try:
        r = await ext_client.get("/api/etsy/clusters")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert r.json() == {"clusters": []}


async def test_clusters_requires_auth(ext_unauth_client):
    r = await ext_unauth_client.get("/api/etsy/clusters")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# /api/etsy/clusters/{cluster_id}
# ---------------------------------------------------------------------------


async def test_cluster_detail_no_memory(ext_client):
    """Quando memory=None → 404."""
    prev = _state.memory
    _state.memory = None
    try:
        r = await ext_client.get("/api/etsy/clusters/cluster-abc")
    finally:
        _state.memory = prev
    assert r.status_code == 404


async def test_cluster_detail_requires_auth(ext_unauth_client):
    r = await ext_unauth_client.get("/api/etsy/clusters/cluster-abc")
    assert r.status_code == 403


async def test_cluster_detail_not_found_with_memory(ext_client):
    """Memory presente ma cluster inesistente → 404."""
    mock_db = MagicMock()
    mock_mem = _mock_memory_with_db(mock_db)

    with patch("apps.backend.api.routers.etsy.ProductionQueueService") as MockPQS:
        mock_pqs = AsyncMock()
        mock_pqs.get_cluster_items = AsyncMock(return_value=[])
        MockPQS.return_value = mock_pqs

        prev = _state.memory
        _state.memory = mock_mem
        try:
            r = await ext_client.get("/api/etsy/clusters/nonexistent-cluster")
        finally:
            _state.memory = prev

    assert r.status_code == 404
    assert "nonexistent-cluster" in r.json()["detail"]


# ---------------------------------------------------------------------------
# /api/autopilot/pause — verifica che l'auth sia richiesta
# ---------------------------------------------------------------------------


async def test_autopilot_pause_requires_auth(ext_unauth_client):
    """/api/autopilot/pause senza X-Personal-Key → 403."""
    r = await ext_unauth_client.post("/api/autopilot/pause")
    assert r.status_code == 403


async def test_autopilot_start_requires_auth(ext_unauth_client):
    r = await ext_unauth_client.post("/api/autopilot/start")
    assert r.status_code == 403


async def test_autopilot_stop_requires_auth(ext_unauth_client):
    r = await ext_unauth_client.post("/api/autopilot/stop")
    assert r.status_code == 403
