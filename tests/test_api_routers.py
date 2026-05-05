"""Tests per tutti gli API router — httpx.AsyncClient + ASGITransport.

Ogni test verifica:
  - Codice di stato HTTP corretto
  - Shape minima della risposta JSON
  - Comportamento fallback quando i singleton di state sono None
  - Validazione input (422 su parametri errati, 403 su auth mancante)

Lo stato è lasciato a None (default) per testare i percorsi di fallback
senza dover avviare il lifespan completo dell'app.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import apps.backend.api.state as _state
from apps.backend.api.routers import (
    autopilot,
    etsy,
    finance,
    memory_routes,
    personal,
    screen,
    system,
    wiki,
)

_PERSONAL_KEY = "test-key-router-tests"

_ROUTERS = [
    system.router,
    autopilot.router,
    etsy.router,
    finance.router,
    memory_routes.router,
    personal.router,
    screen.router,
    wiki.router,
]


@pytest.fixture(scope="module")
def app():
    """App FastAPI con tutti i router, verify_personal_key bypassata via override."""
    _app = FastAPI()
    for r in _ROUTERS:
        _app.include_router(r)
    _app.dependency_overrides[_state.verify_personal_key] = lambda: None
    yield _app
    _app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def unauth_app():
    """App senza override auth — per testare che le route richiedano autenticazione."""
    _app = FastAPI()
    for r in _ROUTERS:
        _app.include_router(r)
    yield _app


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture
async def unauth_client(unauth_app):
    """Client che non invia X-Personal-Key — deve ricevere 403."""
    async with AsyncClient(
        transport=ASGITransport(app=unauth_app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# system.py
# ---------------------------------------------------------------------------


async def test_health_check(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "timestamp" in data


async def test_health_no_auth_required(unauth_client):
    """/api/health è pubblico — non richiede X-Personal-Key."""
    r = await unauth_client.get("/api/health")
    assert r.status_code == 200


async def test_status_no_pepe(client):
    r = await client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "running"
    assert "agents" in data
    assert "mock_mode" in data


async def test_status_requires_auth(unauth_client):
    r = await unauth_client.get("/api/status")
    assert r.status_code == 403


async def test_mock_status_no_pepe(client):
    r = await client.get("/api/mock/status")
    assert r.status_code == 200
    assert r.json()["mock_mode"] is False


async def test_agents_endpoint(client):
    r = await client.get("/api/agents")
    assert r.status_code == 200


async def test_scheduler_endpoint(client):
    r = await client.get("/api/scheduler")
    assert r.status_code == 200


async def test_production_queue_endpoint(client):
    r = await client.get("/api/production-queue")
    assert r.status_code == 200


async def test_production_queue_status_enum(client):
    """Contract: production queue response has items list; any item's status must be in valid set."""
    _VALID_STATUSES = {
        "pending_design", "pending_approval", "approved",
        "scheduled", "published", "failed",
    }
    r = await client.get("/api/production-queue")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert isinstance(data["items"], list)
    for item in data["items"]:
        assert item["status"] in _VALID_STATUSES, (
            f"Unexpected status '{item['status']}' — not in contract set"
        )


async def test_costs_endpoint(client):
    r = await client.get("/api/costs")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# autopilot.py
# ---------------------------------------------------------------------------


async def test_autopilot_status_no_loop(client):
    r = await client.get("/api/autopilot/status")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "stopped"
    assert data["current_niche"] is None
    assert data["items_today"] == 0
    assert data["last_run_at"] is None


async def test_autopilot_start_no_loop(client):
    r = await client.post("/api/autopilot/start")
    assert r.status_code == 503
    assert "error" in r.json()


async def test_autopilot_pause_no_loop(client):
    r = await client.post("/api/autopilot/pause")
    assert r.status_code == 503


async def test_autopilot_stop_no_loop(client):
    r = await client.post("/api/autopilot/stop")
    assert r.status_code == 503


async def test_run_analytics_no_pepe(client):
    r = await client.post("/api/run/analytics")
    assert r.status_code == 503


async def test_autopilot_requires_auth(unauth_client):
    r = await unauth_client.get("/api/autopilot/status")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# etsy.py
# ---------------------------------------------------------------------------


async def test_etsy_listings_no_memory(client):
    """/api/etsy/listings ritorna lista vuota quando memory=None."""
    r = await client.get("/api/etsy/listings")
    assert r.status_code == 200
    assert r.json() == {"listings": []}


async def test_etsy_listings_invalid_status(client):
    r = await client.get("/api/etsy/listings?status=bogus")
    assert r.status_code == 422


async def test_etsy_listings_limit_validation(client):
    r = await client.get("/api/etsy/listings?limit=0")
    assert r.status_code == 422
    r2 = await client.get("/api/etsy/listings?limit=501")
    assert r2.status_code == 422


async def test_etsy_shop_no_api(client):
    r = await client.get("/api/etsy/shop")
    assert r.status_code == 503


async def test_etsy_auth_status_no_api(client):
    r = await client.post("/api/etsy/auth/status")
    assert r.status_code == 503


async def test_etsy_niches_no_memory(client):
    r = await client.get("/api/etsy/niches")
    assert r.status_code == 200
    assert "niches" in r.json()


async def test_etsy_niches_response_model(client):
    """Contract: /api/etsy/niches response parses as NichesResponse; items have required typed fields."""
    from apps.backend.api.routers.etsy import NicheItemResponse, NichesResponse

    r = await client.get("/api/etsy/niches")
    assert r.status_code == 200

    # Endpoint response must be parseable as NichesResponse (Pydantic validates)
    parsed = NichesResponse(**r.json())
    assert isinstance(parsed.niches, list)

    # Verify each item conforms to the contract
    for item in parsed.niches:
        assert isinstance(item.niche, str) and item.niche
        assert isinstance(item.performance_score, float)
        assert item.confidence_level in ("high", "medium", "low")

    # Verify model round-trips correctly with a sample item
    sample = NicheItemResponse(
        niche="planner",
        product_type="printable_pdf",
        performance_score=0.75,
        confidence_level="high",
        avg_ctr=0.04,
        total_orders=None,
        total_listings=None,
        total_revenue_eur=None,
        last_updated_at=None,
        entry_score=0.6,
        tier=2,
        avg_price_eur=9.99,
        google_trend_score=72.0,
    )
    serialized = sample.model_dump()
    assert serialized["niche"] == "planner"
    assert serialized["performance_score"] == 0.75
    assert serialized["confidence_level"] == "high"
    assert serialized["audience_target"] is None
    assert serialized["expansion_potential"] is None


async def test_etsy_bundles_no_memory(client):
    r = await client.get("/api/etsy/bundles")
    assert r.status_code == 200
    assert "bundles" in r.json()


async def test_etsy_ads_status_no_agent(client):
    r = await client.get("/api/etsy/ads-status")
    assert r.status_code == 200
    data = r.json()
    assert data["activated_count"] == 0


async def test_etsy_requires_auth(unauth_client):
    r = await unauth_client.get("/api/etsy/listings")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# finance.py
# ---------------------------------------------------------------------------


async def test_finance_summary_no_tracker(client):
    r = await client.get("/api/finance/summary")
    assert r.status_code == 503
    assert "error" in r.json()


async def test_finance_summary_invalid_month(client):
    r = await client.get("/api/finance/summary?month=13")
    assert r.status_code == 422


async def test_finance_summary_invalid_year(client):
    r = await client.get("/api/finance/summary?year=1999")
    assert r.status_code == 422


async def test_finance_report_no_memory(client):
    r = await client.get("/api/finance/report")
    assert r.status_code in (200, 503)


async def test_finance_run_no_pepe(client):
    r = await client.post("/api/finance/run")
    assert r.status_code == 503


async def test_analytics_latest_no_memory(client):
    r = await client.get("/api/analytics/latest")
    assert r.status_code == 200


async def test_analytics_failures_no_memory(client):
    r = await client.get("/api/analytics/failures")
    assert r.status_code == 200


async def test_analytics_ladder_no_memory(client):
    r = await client.get("/api/analytics/ladder")
    assert r.status_code == 200
    data = r.json()
    assert "ok" in data
    assert "total" in data
    assert data["total"] == 0


async def test_finance_requires_auth(unauth_client):
    r = await unauth_client.get("/api/finance/summary")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# memory_routes.py
# ---------------------------------------------------------------------------


async def test_memory_stats_no_memory(client):
    r = await client.get("/api/memory/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["chroma"]["available"] is False


async def test_memory_graph_no_memory(client):
    r = await client.get("/api/memory/graph")
    assert r.status_code in (200, 503)


async def test_memory_graph_threshold_validation(client):
    r = await client.get("/api/memory/graph?threshold=1.5")
    assert r.status_code == 422
    r2 = await client.get("/api/memory/graph?threshold=-0.1")
    assert r2.status_code == 422


# ---------------------------------------------------------------------------
# personal.py
# ---------------------------------------------------------------------------


async def test_personal_reminders_no_memory(client):
    r = await client.get("/api/personal/reminders")
    assert r.status_code == 200
    assert r.json() == {"items": []}


async def test_personal_reminders_limit_validation(client):
    r = await client.get("/api/personal/reminders?limit=0")
    assert r.status_code == 422
    r2 = await client.get("/api/personal/reminders?limit=101")
    assert r2.status_code == 422


async def test_personal_recalls_no_memory(client):
    r = await client.get("/api/personal/recalls")
    assert r.status_code == 200
    assert r.json() == {"items": []}


async def test_personal_mcp_status_no_pepe(client):
    r = await client.get("/api/personal/mcp/status")
    assert r.status_code == 200


async def test_personal_stats_no_pepe(client):
    r = await client.get("/api/personal/stats")
    assert r.status_code == 200


async def test_personal_ollama_status(client):
    r = await client.get("/api/ollama/status")
    assert r.status_code == 200


async def test_personal_requires_auth(unauth_client):
    r = await unauth_client.get("/api/personal/reminders")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# screen.py
# ---------------------------------------------------------------------------


async def test_screen_status_no_watcher(client):
    r = await client.get("/api/screen/status")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False
    assert data["active"] is False


async def test_screen_toggle_no_watcher(client):
    r = await client.post("/api/screen/toggle")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False


async def test_screen_requires_auth(unauth_client):
    r = await unauth_client.get("/api/screen/status")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# wiki.py
# ---------------------------------------------------------------------------


async def test_wiki_stats_no_pepe(client):
    r = await client.get("/api/wiki/stats")
    assert r.status_code == 503
    assert "error" in r.json()


async def test_wiki_query_no_q(client):
    """Senza pepe il 503 ha priorità sul 400 (pepe check avviene prima)."""
    r = await client.get("/api/wiki/query")
    assert r.status_code == 503


async def test_wiki_query_no_pepe(client):
    r = await client.get("/api/wiki/query?q=svg+design")
    assert r.status_code == 503


async def test_wiki_niche_no_pepe(client):
    r = await client.get("/api/wiki/niche/svg-design")
    assert r.status_code == 503


async def test_wiki_lint_no_pepe(client):
    r = await client.post("/api/wiki/lint", json={"niche": "svg-design"})
    assert r.status_code in (200, 400, 503)


async def test_wiki_requires_auth(unauth_client):
    r = await unauth_client.get("/api/wiki/stats")
    assert r.status_code == 403
