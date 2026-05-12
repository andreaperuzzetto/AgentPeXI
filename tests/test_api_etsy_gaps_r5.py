"""tests/test_api_etsy_gaps_r5.py — Copertura gap round 5.

Copre le righe scoperte:
  108, 116-124, 135-137, 162, 169-173, 267-269, 289-293, 305,
  309-316, 331-375, 386-419, 436-448, 477-479, 508-510, 554-555, 585-586
di apps/backend/api/routers/etsy.py.

Pattern identico a test_api_routers.py:
  AsyncClient + ASGITransport, dependency_overrides[verify_personal_key].
Non duplica nessun test già presente in test_api_routers.py
o test_api_routers_extended.py.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import apps.backend.api.state as _state
import apps.backend.api.routers.etsy as _etsy_router
from apps.backend.api.routers import etsy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    _app = FastAPI()
    _app.include_router(etsy.router)
    _app.dependency_overrides[_state.verify_personal_key] = lambda: None
    yield _app
    _app.dependency_overrides.clear()


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cursor(fetchone_val=None, fetchall_val=None):
    cur = MagicMock()
    cur.fetchone = AsyncMock(return_value=fetchone_val)
    cur.fetchall = AsyncMock(return_value=fetchall_val if fetchall_val is not None else [])
    return cur


def _memory_with_db(db):
    mem = MagicMock()
    mem.get_db = AsyncMock(return_value=db)
    return mem


def _simple_db(fetchone_val=None, fetchall_val=None):
    cur = _cursor(fetchone_val=fetchone_val, fetchall_val=fetchall_val)
    db = MagicMock()
    db.execute = AsyncMock(return_value=cur)
    db.commit = AsyncMock()
    return db, cur


# ---------------------------------------------------------------------------
# POST /api/etsy/auth/status  — line 108
# ---------------------------------------------------------------------------

async def test_etsy_auth_status_success(client):
    """line 108: etsy_api.check_auth_status() → 200 con payload reale."""
    mock_api = AsyncMock()
    mock_api.check_auth_status = AsyncMock(return_value={"valid": True, "shop_id": "99"})
    prev = _state.etsy_api
    _state.etsy_api = mock_api
    try:
        r = await client.post("/api/etsy/auth/status")
    finally:
        _state.etsy_api = prev
    assert r.status_code == 200
    assert r.json()["valid"] is True


# ---------------------------------------------------------------------------
# GET /api/etsy/shop  — lines 116-124
# ---------------------------------------------------------------------------

async def test_etsy_shop_200(client):
    """lines 116-118: get_shop() → {"shop": ...}."""
    mock_api = AsyncMock()
    mock_api.get_shop = AsyncMock(return_value={"shop_id": "42", "shop_name": "TestShop"})
    prev = _state.etsy_api
    _state.etsy_api = mock_api
    try:
        r = await client.get("/api/etsy/shop")
    finally:
        _state.etsy_api = prev
    assert r.status_code == 200
    data = r.json()
    assert "shop" in data
    assert data["shop"]["shop_name"] == "TestShop"


async def test_etsy_shop_401_runtime_error(client):
    """lines 119-121: get_shop() RuntimeError → 401."""
    mock_api = AsyncMock()
    mock_api.get_shop = AsyncMock(side_effect=RuntimeError("Token scaduto"))
    prev = _state.etsy_api
    _state.etsy_api = mock_api
    try:
        r = await client.get("/api/etsy/shop")
    finally:
        _state.etsy_api = prev
    assert r.status_code == 401
    assert "detail" in r.json()


async def test_etsy_shop_502_generic_error(client):
    """lines 122-124: get_shop() Exception generico → 502."""
    mock_api = AsyncMock()
    mock_api.get_shop = AsyncMock(side_effect=Exception("Network error"))
    prev = _state.etsy_api
    _state.etsy_api = mock_api
    try:
        r = await client.get("/api/etsy/shop")
    finally:
        _state.etsy_api = prev
    assert r.status_code == 502
    assert "detail" in r.json()


# ---------------------------------------------------------------------------
# GET /api/etsy/listings  — lines 135-137
# ---------------------------------------------------------------------------

async def test_etsy_listings_status_active(client):
    """line 135-137: status=active → memory.get_etsy_listings(status='active', limit=50)."""
    data_in = [{"listing_id": 1, "title": "My SVG", "status": "active"}]
    mock_mem = MagicMock()
    mock_mem.get_etsy_listings = AsyncMock(return_value=data_in)
    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await client.get("/api/etsy/listings?status=active")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert r.json()["listings"] == data_in
    mock_mem.get_etsy_listings.assert_awaited_once_with(status="active", limit=50)


async def test_etsy_listings_status_all_passes_none(client):
    """line 135-137: status=all → filter_status=None (passa None a get_etsy_listings)."""
    mock_mem = MagicMock()
    mock_mem.get_etsy_listings = AsyncMock(return_value=[])
    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await client.get("/api/etsy/listings?status=all")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    mock_mem.get_etsy_listings.assert_awaited_once_with(status=None, limit=50)


async def test_etsy_listings_limit_10(client):
    """line 135-137: limit=10 trasmesso a get_etsy_listings."""
    mock_mem = MagicMock()
    mock_mem.get_etsy_listings = AsyncMock(return_value=[])
    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await client.get("/api/etsy/listings?limit=10")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    mock_mem.get_etsy_listings.assert_awaited_once_with(status=None, limit=10)


# ---------------------------------------------------------------------------
# GET /api/etsy/niches  — lines 162, 169-173, 267-269
# ---------------------------------------------------------------------------

async def test_etsy_niches_invalid_confidence_422(client):
    """line 162: confidence invalida → 422 HTTPException."""
    mock_mem = MagicMock()
    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await client.get("/api/etsy/niches?confidence=invalid")
    finally:
        _state.memory = prev
    assert r.status_code == 422


async def test_etsy_niches_invalid_confidence_message(client):
    """line 162: messaggio dettaglio include 'confidence'."""
    mock_mem = MagicMock()
    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await client.get("/api/etsy/niches?confidence=xyz")
    finally:
        _state.memory = prev
    assert r.status_code == 422
    assert "confidence" in r.json()["detail"].lower()


async def test_etsy_niches_min_score(client):
    """lines 169-170: min_score aggiunto ai params della query → 200 niches=[]."""
    db, _ = _simple_db(fetchall_val=[])
    mem = _memory_with_db(db)
    mem.query_insights_by_type = AsyncMock(return_value=[])
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/niches?min_score=0.5")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert r.json()["niches"] == []


async def test_etsy_niches_confidence_high(client):
    """lines 172-173: confidence=high → WHERE clause aggiunta."""
    db, _ = _simple_db(fetchall_val=[])
    mem = _memory_with_db(db)
    mem.query_insights_by_type = AsyncMock(return_value=[])
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/niches?confidence=high")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert "niches" in r.json()


async def test_etsy_niches_confidence_medium(client):
    """confidence=medium → 200 senza eccezioni."""
    db, _ = _simple_db(fetchall_val=[])
    mem = _memory_with_db(db)
    mem.query_insights_by_type = AsyncMock(return_value=[])
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/niches?confidence=medium")
    finally:
        _state.memory = prev
    assert r.status_code == 200


async def test_etsy_niches_confidence_low(client):
    """confidence=low → 200 senza eccezioni."""
    db, _ = _simple_db(fetchall_val=[])
    mem = _memory_with_db(db)
    mem.query_insights_by_type = AsyncMock(return_value=[])
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/niches?confidence=low")
    finally:
        _state.memory = prev
    assert r.status_code == 200


async def test_etsy_niches_min_score_and_confidence_combined(client):
    """min_score + confidence validi → 200, entrambe le WHERE conditions attive."""
    db, _ = _simple_db(fetchall_val=[])
    mem = _memory_with_db(db)
    mem.query_insights_by_type = AsyncMock(return_value=[])
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/niches?min_score=0.3&confidence=high")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert "niches" in r.json()


async def test_etsy_niches_exception_500(client):
    """lines 267-269: eccezione durante get_db() → 500."""
    mem = MagicMock()
    mem.get_db = AsyncMock(side_effect=RuntimeError("db connection failed"))
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/niches")
    finally:
        _state.memory = prev
    assert r.status_code == 500


async def test_etsy_niches_with_warmup_candidates(client):
    """Part C: warmup_candidates da ChromaDB aggiunte se non duplicate → source_type presente."""
    db, _ = _simple_db(fetchall_val=[])
    mem = _memory_with_db(db)
    mem.query_insights_by_type = AsyncMock(return_value=[
        {
            "metadata": {
                "niche": "new-niche",
                "product_type": "svg",
                "score": "0.7",
                "status": "pending_warmup",
            }
        }
    ])
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/niches")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    niches = r.json()["niches"]
    assert len(niches) == 1
    assert niches[0]["niche"] == "new-niche"
    assert niches[0]["source_type"] == "warmup_candidate"


# ---------------------------------------------------------------------------
# GET /api/etsy/sections  — lines 289-293
# ---------------------------------------------------------------------------

async def test_etsy_sections_200(client):
    """lines 289-293: EtsySectionsService.get_sections_with_uncategorized_counts → 200."""
    raw = [
        {
            "section_id": "sec-1",
            "section_name": "Planners",
            "listing_count": 5,
            "last_listing_at": "2026-01-01T00:00:00",
            "pending_uncategorized": 2,
        }
    ]
    mem = _memory_with_db(MagicMock())
    prev = _state.memory
    _state.memory = mem
    with patch("apps.backend.core.etsy_sections_service.EtsySectionsService") as MockESS:
        mock_ess = MagicMock()
        mock_ess.get_sections_with_uncategorized_counts = AsyncMock(return_value=raw)
        MockESS.return_value = mock_ess
        try:
            r = await client.get("/api/etsy/sections")
        finally:
            _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert "sections" in data
    assert len(data["sections"]) == 1
    assert data["sections"][0]["section_id"] == "sec-1"
    assert data["sections"][0]["listing_count"] == 5


async def test_etsy_sections_exception_500(client):
    """exception in EtsySectionsService → 500."""
    mem = _memory_with_db(MagicMock())
    prev = _state.memory
    _state.memory = mem
    with patch("apps.backend.core.etsy_sections_service.EtsySectionsService") as MockESS:
        mock_ess = MagicMock()
        mock_ess.get_sections_with_uncategorized_counts = AsyncMock(
            side_effect=RuntimeError("db error")
        )
        MockESS.return_value = mock_ess
        try:
            r = await client.get("/api/etsy/sections")
        finally:
            _state.memory = prev
    assert r.status_code == 500
    assert "detail" in r.json()


# ---------------------------------------------------------------------------
# GET /api/etsy/bundles  — lines 305, 309-316
# ---------------------------------------------------------------------------

async def test_etsy_bundles_cache_hit(client):
    """line 305: cache valida → risposta diretta senza chiamare bundle_strategy."""
    prev_data = _etsy_router._bundles_cache["data"]
    prev_at = _etsy_router._bundles_cache["cached_at"]
    _etsy_router._bundles_cache["data"] = [{"bundle": "cached-bundle"}]
    _etsy_router._bundles_cache["cached_at"] = time.time()
    try:
        r = await client.get("/api/etsy/bundles")
    finally:
        _etsy_router._bundles_cache["data"] = prev_data
        _etsy_router._bundles_cache["cached_at"] = prev_at
    assert r.status_code == 200
    data = r.json()
    assert data["bundles"] == [{"bundle": "cached-bundle"}]
    assert data["cached_at"] is not None


async def test_etsy_bundles_with_strategy(client):
    """lines 309-316: bundle_strategy.check_all_niches() → risultati salvati in cache."""
    prev_data = _etsy_router._bundles_cache["data"]
    prev_at = _etsy_router._bundles_cache["cached_at"]
    _etsy_router._bundles_cache["data"] = None
    mock_strategy = MagicMock()
    mock_strategy.check_all_niches = AsyncMock(
        return_value=[{"niche": "svg-design", "bundle_ready": True}]
    )
    prev_strategy = _state.bundle_strategy
    _state.bundle_strategy = mock_strategy
    try:
        r = await client.get("/api/etsy/bundles")
    finally:
        _state.bundle_strategy = prev_strategy
        _etsy_router._bundles_cache["data"] = prev_data
        _etsy_router._bundles_cache["cached_at"] = prev_at
    assert r.status_code == 200
    data = r.json()
    assert len(data["bundles"]) == 1
    assert data["bundles"][0]["niche"] == "svg-design"


async def test_etsy_bundles_no_strategy_no_cache(client):
    """lines 307-308: bundle_strategy=None e cache vuota → bundles=[], cached_at=None."""
    prev_data = _etsy_router._bundles_cache["data"]
    prev_at = _etsy_router._bundles_cache["cached_at"]
    _etsy_router._bundles_cache["data"] = None
    prev_strategy = _state.bundle_strategy
    _state.bundle_strategy = None
    try:
        r = await client.get("/api/etsy/bundles")
    finally:
        _state.bundle_strategy = prev_strategy
        _etsy_router._bundles_cache["data"] = prev_data
        _etsy_router._bundles_cache["cached_at"] = prev_at
    assert r.status_code == 200
    data = r.json()
    assert data["bundles"] == []
    assert data["cached_at"] is None


# ---------------------------------------------------------------------------
# GET /api/etsy/ads-status  — lines 331-375
# ---------------------------------------------------------------------------

async def test_etsy_ads_status_with_db_full(client):
    """lines 331-375: 4 execute sequenziali → tutti i campi popolati."""
    cur_activated = _cursor(fetchone_val={"cnt": 5})
    cur_paused = _cursor(fetchone_val={"cnt": 2})
    cur_ctr = _cursor(fetchone_val={"avg_ctr": 0.045})
    cur_config = _cursor(fetchone_val={"value": "1715000000.0"})
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[cur_activated, cur_paused, cur_ctr, cur_config])
    mem = _memory_with_db(db)
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/ads-status")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert data["activated_count"] == 5
    assert data["paused_count"] == 2
    assert data["avg_ctr"] == pytest.approx(0.045, abs=1e-4)
    assert data["last_auto_manage_at"] == pytest.approx(1715000000.0)


async def test_etsy_ads_status_zero_ctr(client):
    """avg_ctr=None (nessun CTR calcolabile) → avg_ctr null nella risposta."""
    cur_activated = _cursor(fetchone_val={"cnt": 0})
    cur_paused = _cursor(fetchone_val={"cnt": 0})
    cur_ctr = _cursor(fetchone_val={"avg_ctr": None})
    cur_config = _cursor(fetchone_val=None)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[cur_activated, cur_paused, cur_ctr, cur_config])
    mem = _memory_with_db(db)
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/ads-status")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert data["avg_ctr"] is None
    assert data["last_auto_manage_at"] is None


async def test_etsy_ads_status_no_config_row(client):
    """config row mancante (fetchone=None) → last_auto_manage_at=None."""
    cur_activated = _cursor(fetchone_val={"cnt": 3})
    cur_paused = _cursor(fetchone_val={"cnt": 1})
    cur_ctr = _cursor(fetchone_val={"avg_ctr": 0.03})
    cur_config = _cursor(fetchone_val=None)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[cur_activated, cur_paused, cur_ctr, cur_config])
    mem = _memory_with_db(db)
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/ads-status")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert r.json()["last_auto_manage_at"] is None


async def test_etsy_ads_status_response_keys(client):
    """Risposta contiene esattamente le 4 chiavi del contratto."""
    cur = _cursor(fetchone_val={"cnt": 0})
    cur_ctr = _cursor(fetchone_val={"avg_ctr": None})
    cur_config = _cursor(fetchone_val=None)
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[cur, _cursor(fetchone_val={"cnt": 0}), cur_ctr, cur_config]
    )
    mem = _memory_with_db(db)
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/ads-status")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert "activated_count" in data
    assert "paused_count" in data
    assert "avg_ctr" in data
    assert "last_auto_manage_at" in data


async def test_etsy_ads_status_exception_500(client):
    """Exception nel get_db() → 500."""
    mem = MagicMock()
    mem.get_db = AsyncMock(side_effect=RuntimeError("connection refused"))
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/ads-status")
    finally:
        _state.memory = prev
    assert r.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/etsy/shop-optimizer  — lines 386-419
# ---------------------------------------------------------------------------

async def test_etsy_shop_optimizer_200_applied(client):
    """lines 386-419: last_title presente → status=applied."""
    config_rows = [
        {"key": "shop_optimizer.last_applied_title", "value": "Amazing Digital Downloads"},
        {"key": "shop_optimizer.last_applied_niches", "value": '["svg-design", "wedding"]'},
        {"key": "shop_optimizer.last_applied_at", "value": "1715000000.0"},
    ]
    db, _ = _simple_db(fetchall_val=config_rows)
    mem = _memory_with_db(db)
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/shop-optimizer")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "applied"
    assert data["last_title"] == "Amazing Digital Downloads"
    assert "svg-design" in data["last_niches"]
    assert data["last_applied_at"] == pytest.approx(1715000000.0)


async def test_etsy_shop_optimizer_200_never_applied(client):
    """last_title assente (config vuota) → status=never_applied."""
    db, _ = _simple_db(fetchall_val=[])
    mem = _memory_with_db(db)
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/shop-optimizer")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "never_applied"
    assert data["last_title"] is None
    assert data["last_niches"] == []


async def test_etsy_shop_optimizer_invalid_json_niches(client):
    """last_applied_niches con JSON invalido → last_niches=[] (graceful)."""
    config_rows = [
        {"key": "shop_optimizer.last_applied_title", "value": "My Shop"},
        {"key": "shop_optimizer.last_applied_niches", "value": "NOT_VALID_JSON{"},
    ]
    db, _ = _simple_db(fetchall_val=config_rows)
    mem = _memory_with_db(db)
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/shop-optimizer")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert r.json()["last_niches"] == []


async def test_etsy_shop_optimizer_response_keys(client):
    """Risposta ha le 4 chiavi attese: status, last_title, last_niches, last_applied_at."""
    db, _ = _simple_db(fetchall_val=[])
    mem = _memory_with_db(db)
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.get("/api/etsy/shop-optimizer")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert "last_title" in data
    assert "last_niches" in data
    assert "last_applied_at" in data


# ---------------------------------------------------------------------------
# POST /api/etsy/shop-optimizer/preview  — lines 436-448
# ---------------------------------------------------------------------------

async def test_etsy_shop_optimizer_preview_with_body(client):
    """lines 436-448: body con focus_niche → preview() chiamato con focus_niche."""
    mock_opt = MagicMock()
    mock_opt.preview = AsyncMock(
        return_value={
            "title": "Best SVG Downloads",
            "about": "We make SVGs",
            "niches": ["svg-design"],
            "changed": True,
        }
    )
    prev = _state.shop_optimizer
    _state.shop_optimizer = mock_opt
    try:
        r = await client.post(
            "/api/etsy/shop-optimizer/preview",
            json={"focus_niche": "svg-design"},
        )
    finally:
        _state.shop_optimizer = prev
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["title"] == "Best SVG Downloads"
    assert data["changed"] is True
    mock_opt.preview.assert_awaited_once_with(focus_niche="svg-design")


async def test_etsy_shop_optimizer_preview_no_body(client):
    """preview senza body (None) → preview(focus_niche=None)."""
    mock_opt = MagicMock()
    mock_opt.preview = AsyncMock(
        return_value={"title": None, "about": None, "niches": [], "changed": False}
    )
    prev = _state.shop_optimizer
    _state.shop_optimizer = mock_opt
    try:
        r = await client.post("/api/etsy/shop-optimizer/preview")
    finally:
        _state.shop_optimizer = prev
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    mock_opt.preview.assert_awaited_once_with(focus_niche=None)


async def test_etsy_shop_optimizer_preview_empty_body(client):
    """preview con {} (focus_niche=None) → preview(focus_niche=None)."""
    mock_opt = MagicMock()
    mock_opt.preview = AsyncMock(
        return_value={"title": "Shop", "about": "about", "niches": [], "changed": False}
    )
    prev = _state.shop_optimizer
    _state.shop_optimizer = mock_opt
    try:
        r = await client.post("/api/etsy/shop-optimizer/preview", json={})
    finally:
        _state.shop_optimizer = prev
    assert r.status_code == 200
    mock_opt.preview.assert_awaited_once_with(focus_niche=None)


async def test_etsy_shop_optimizer_preview_exception_500(client):
    """Exception in preview() → 500."""
    mock_opt = MagicMock()
    mock_opt.preview = AsyncMock(side_effect=RuntimeError("LLM error"))
    prev = _state.shop_optimizer
    _state.shop_optimizer = mock_opt
    try:
        r = await client.post("/api/etsy/shop-optimizer/preview", json={})
    finally:
        _state.shop_optimizer = prev
    assert r.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/etsy/style-guide-options  — lines 477-479
# ---------------------------------------------------------------------------

async def test_etsy_style_guide_options_with_records(client):
    """lines 477-479: ShopIdentityService.list_options() → 200 con opzioni."""
    from apps.backend.core.shop_identity_service import ShopIdentityRecord

    record = ShopIdentityRecord(
        id=1,
        aesthetic_name="Minimalist",
        palette_primary="#FFFFFF",
        palette_secondary="#000000",
        palette_accent="#CCCCCC",
        mockup_style="flat",
        tone="professional",
        logo_path=None,
        banner_path=None,
        approved_at="2026-01-01",
        approved_by="andrea",
        is_active=True,
    )
    mem = _memory_with_db(MagicMock())
    prev = _state.memory
    _state.memory = mem
    with patch("apps.backend.core.shop_identity_service.ShopIdentityService") as MockSIS:
        mock_svc = MagicMock()
        mock_svc.list_options = AsyncMock(return_value=[record])
        MockSIS.return_value = mock_svc
        try:
            r = await client.get("/api/etsy/style-guide-options")
        finally:
            _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert len(data["options"]) == 1
    assert data["options"][0]["aesthetic_name"] == "Minimalist"
    assert data["options"][0]["is_active"] is True


async def test_etsy_style_guide_options_exception_returns_empty(client):
    """lines 477-479: eccezione in list_options → 200 [] (graceful fallback)."""
    mem = _memory_with_db(MagicMock())
    prev = _state.memory
    _state.memory = mem
    with patch("apps.backend.core.shop_identity_service.ShopIdentityService") as MockSIS:
        mock_svc = MagicMock()
        mock_svc.list_options = AsyncMock(side_effect=RuntimeError("db error"))
        MockSIS.return_value = mock_svc
        try:
            r = await client.get("/api/etsy/style-guide-options")
        finally:
            _state.memory = prev
    assert r.status_code == 200
    assert r.json() == {"options": []}


# ---------------------------------------------------------------------------
# GET /api/etsy/shop-identity  — lines 508-510
# ---------------------------------------------------------------------------

async def test_etsy_shop_identity_with_active_record(client):
    """lines 508-510: get_active() ritorna record → identity popolata."""
    from apps.backend.core.shop_identity_service import ShopIdentityRecord

    record = ShopIdentityRecord(
        id=7,
        aesthetic_name="Boho Chic",
        palette_primary="#F5DEB3",
        palette_secondary="#8B4513",
        palette_accent="#DAA520",
        mockup_style="realistic",
        tone="casual",
        logo_path="/logos/boho.png",
        banner_path=None,
        approved_at="2026-03-15",
        approved_by="andrea",
        is_active=True,
    )
    mem = _memory_with_db(MagicMock())
    prev = _state.memory
    _state.memory = mem
    with patch("apps.backend.core.shop_identity_service.ShopIdentityService") as MockSIS:
        mock_svc = MagicMock()
        mock_svc.get_active = AsyncMock(return_value=record)
        MockSIS.return_value = mock_svc
        try:
            r = await client.get("/api/etsy/shop-identity")
        finally:
            _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert data["identity"]["id"] == 7
    assert data["identity"]["aesthetic_name"] == "Boho Chic"
    assert data["identity"]["is_active"] is True


async def test_etsy_shop_identity_no_active_record(client):
    """get_active() → None → identity=None."""
    mem = _memory_with_db(MagicMock())
    prev = _state.memory
    _state.memory = mem
    with patch("apps.backend.core.shop_identity_service.ShopIdentityService") as MockSIS:
        mock_svc = MagicMock()
        mock_svc.get_active = AsyncMock(return_value=None)
        MockSIS.return_value = mock_svc
        try:
            r = await client.get("/api/etsy/shop-identity")
        finally:
            _state.memory = prev
    assert r.status_code == 200
    assert r.json()["identity"] is None


async def test_etsy_shop_identity_exception_returns_none(client):
    """lines 508-510: eccezione in get_active() → 200 identity=None (fallback)."""
    mem = _memory_with_db(MagicMock())
    prev = _state.memory
    _state.memory = mem
    with patch("apps.backend.core.shop_identity_service.ShopIdentityService") as MockSIS:
        mock_svc = MagicMock()
        mock_svc.get_active = AsyncMock(side_effect=RuntimeError("db crash"))
        MockSIS.return_value = mock_svc
        try:
            r = await client.get("/api/etsy/shop-identity")
        finally:
            _state.memory = prev
    assert r.status_code == 200
    assert r.json()["identity"] is None


# ---------------------------------------------------------------------------
# POST /api/analytics/etsy-sync  — lines 554-555
# ---------------------------------------------------------------------------

_SYNC_BODY = {
    "listing_id": 123456,
    "views": 100,
    "favorites": 10,
    "num_orders": 3,
    "revenue_eur": 27.99,
}


async def test_etsy_analytics_sync_success(client):
    """lines 554-555: listing trovata → INSERT, commit, 200 {"ok": True}."""
    cur_select = _cursor(fetchone_val=("svg-design", "printable_pdf", "template_1", "blue"))
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[cur_select, MagicMock()])
    db.commit = AsyncMock()
    mem = _memory_with_db(db)
    prev_mem = _state.memory
    prev_ll = _state.learning_loop
    _state.memory = mem
    _state.learning_loop = None
    try:
        r = await client.post("/api/analytics/etsy-sync", json=_SYNC_BODY)
    finally:
        _state.memory = prev_mem
        _state.learning_loop = prev_ll
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["listing_id"] == 123456


async def test_etsy_analytics_sync_with_learning_loop(client):
    """line 553-554: update_niche_intelligence chiamata quando learning_loop è presente."""
    cur_select = _cursor(fetchone_val=("svg", "pdf", "t1", "blue"))
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[cur_select, MagicMock()])
    db.commit = AsyncMock()
    mem = _memory_with_db(db)
    mock_ll = MagicMock()
    mock_ll.update_niche_intelligence = AsyncMock()
    prev_mem = _state.memory
    prev_ll = _state.learning_loop
    _state.memory = mem
    _state.learning_loop = mock_ll
    try:
        r = await client.post("/api/analytics/etsy-sync", json=_SYNC_BODY)
    finally:
        _state.memory = prev_mem
        _state.learning_loop = prev_ll
    assert r.status_code == 200
    mock_ll.update_niche_intelligence.assert_awaited_once()


async def test_etsy_analytics_sync_learning_loop_error_ignored(client):
    """lines 554-555: update_niche_intelligence fallisce → 200 comunque (warning only)."""
    cur_select = _cursor(fetchone_val=("svg", "pdf", "t1", "blue"))
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[cur_select, MagicMock()])
    db.commit = AsyncMock()
    mem = _memory_with_db(db)
    mock_ll = MagicMock()
    mock_ll.update_niche_intelligence = AsyncMock(side_effect=RuntimeError("LLM timeout"))
    prev_mem = _state.memory
    prev_ll = _state.learning_loop
    _state.memory = mem
    _state.learning_loop = mock_ll
    try:
        r = await client.post("/api/analytics/etsy-sync", json=_SYNC_BODY)
    finally:
        _state.memory = prev_mem
        _state.learning_loop = prev_ll
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_etsy_analytics_sync_listing_not_found(client):
    """listing_id non in etsy_listings → 404 con listing_id nel detail."""
    cur_select = _cursor(fetchone_val=None)
    db = MagicMock()
    db.execute = AsyncMock(return_value=cur_select)
    mem = _memory_with_db(db)
    prev = _state.memory
    _state.memory = mem
    try:
        r = await client.post("/api/analytics/etsy-sync", json=_SYNC_BODY)
    finally:
        _state.memory = prev
    assert r.status_code == 404
    assert "123456" in r.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/etsy/niches/{niche}/competitor-analysis  — lines 585-586
# ---------------------------------------------------------------------------

async def test_competitor_analysis_available(client):
    """lines 585-586: cache ChromaDB con documento valido → available=True."""
    cached = [{"document": '{"top_shops": ["shop1"], "count": 5}'}]
    mock_mem = MagicMock()
    mock_mem.query_chromadb = AsyncMock(return_value=cached)
    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await client.get("/api/etsy/niches/svg-design/competitor-analysis")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    assert data["niche"] == "svg-design"
    assert "analysis" in data
    assert data["analysis"]["count"] == 5


async def test_competitor_analysis_bad_json_returns_empty_dict(client):
    """document con JSON invalido → analysis={} (linee 585-586, except branch)."""
    cached = [{"document": "NOT_VALID_JSON{{{"}]
    mock_mem = MagicMock()
    mock_mem.query_chromadb = AsyncMock(return_value=cached)
    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await client.get("/api/etsy/niches/svg-design/competitor-analysis")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    assert data["analysis"] == {}


# ---------------------------------------------------------------------------
# GET /api/etsy/clusters  — C.4
# ---------------------------------------------------------------------------

async def test_etsy_clusters_200_with_data(client):
    """GET /api/etsy/clusters con dati → lista cluster."""
    mock_mem = MagicMock()
    mock_mem.get_db = AsyncMock(return_value=MagicMock())
    prev = _state.memory
    _state.memory = mock_mem
    cluster_rows = [
        {"cluster_id": "c-1", "niche": "svg-design", "total": 10, "completed": 7},
        {"cluster_id": "c-2", "niche": "wedding", "total": 5, "completed": 2},
    ]
    with patch("apps.backend.api.routers.etsy.ProductionQueueService") as MockPQS:
        mock_pqs = MagicMock()
        mock_pqs._fetchall = AsyncMock(return_value=cluster_rows)
        MockPQS.return_value = mock_pqs
        try:
            r = await client.get("/api/etsy/clusters")
        finally:
            _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert len(data["clusters"]) == 2
    assert data["clusters"][0]["cluster_id"] == "c-1"
    assert data["clusters"][1]["niche"] == "wedding"


async def test_etsy_clusters_empty_rows(client):
    """GET /api/etsy/clusters senza righe → clusters=[]."""
    mock_mem = MagicMock()
    mock_mem.get_db = AsyncMock(return_value=MagicMock())
    prev = _state.memory
    _state.memory = mock_mem
    with patch("apps.backend.api.routers.etsy.ProductionQueueService") as MockPQS:
        mock_pqs = MagicMock()
        mock_pqs._fetchall = AsyncMock(return_value=[])
        MockPQS.return_value = mock_pqs
        try:
            r = await client.get("/api/etsy/clusters")
        finally:
            _state.memory = prev
    assert r.status_code == 200
    assert r.json()["clusters"] == []


# ---------------------------------------------------------------------------
# GET /api/etsy/clusters/{cluster_id}  — C.4
# ---------------------------------------------------------------------------

@dataclass
class _FakeClusterItem:
    """Minimal dataclass per soddisfare dataclasses.fields(items[0].__class__)."""
    id: int
    status: str
    niche: str


async def test_etsy_cluster_detail_200(client):
    """GET /api/etsy/clusters/{cluster_id} con items → 200."""
    mock_mem = MagicMock()
    mock_mem.get_db = AsyncMock(return_value=MagicMock())
    prev = _state.memory
    _state.memory = mock_mem

    item = MagicMock(spec=_FakeClusterItem)
    item.id = 42
    item.status = "published"
    item.niche = "svg-design"

    with patch("apps.backend.api.routers.etsy.ProductionQueueService") as MockPQS:
        mock_pqs = MagicMock()
        mock_pqs.get_cluster_items = AsyncMock(return_value=[item])
        MockPQS.return_value = mock_pqs
        try:
            r = await client.get("/api/etsy/clusters/cluster-abc")
        finally:
            _state.memory = prev

    assert r.status_code == 200
    data = r.json()
    assert data["cluster_id"] == "cluster-abc"
    assert isinstance(data["items"], list)
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == 42
    assert data["items"][0]["status"] == "published"


async def test_etsy_cluster_detail_empty_items_404(client):
    """GET /api/etsy/clusters/{cluster_id} con items vuoti → 404 con cluster_id nel detail."""
    mock_mem = MagicMock()
    mock_mem.get_db = AsyncMock(return_value=MagicMock())
    prev = _state.memory
    _state.memory = mock_mem
    with patch("apps.backend.api.routers.etsy.ProductionQueueService") as MockPQS:
        mock_pqs = MagicMock()
        mock_pqs.get_cluster_items = AsyncMock(return_value=[])
        MockPQS.return_value = mock_pqs
        try:
            r = await client.get("/api/etsy/clusters/nonexistent-id")
        finally:
            _state.memory = prev
    assert r.status_code == 404
    assert "nonexistent-id" in r.json()["detail"]
