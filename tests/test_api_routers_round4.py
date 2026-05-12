"""test_api_routers_round4.py

~70 test pytest-asyncio che coprono endpoint e logiche NON ancora coperte da
test_api_routers.py e test_api_routers_extended.py.

Copre:
  - finance.py    : success paths, /api/analytics/ctr-ab, ladder con mock DB
  - wiki.py       : success paths + /api/domain
  - memory_routes : success paths + /api/memory/node
  - personal.py   : voice/collect, personal/ask, success paths
  - system.py     : /api/health no-memory, /api/domains/config, /api/listings,
                    /api/scheduler/jobs, /api/tasks/{task_id}/timeline
  - autopilot.py  : success paths con mock loop
  - state.py      : ConnectionManager + verify_personal_key unit tests
  - middleware.py : RequestIDMiddleware + RequestIDFilter unit tests

Pattern identico a test_api_routers.py:
  httpx.AsyncClient + ASGITransport, dependency_overrides[verify_personal_key].
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

import apps.backend.api.state as _state
from apps.backend.api.routers import (
    autopilot,
    finance,
    memory_routes,
    personal,
    system,
    wiki,
)

# ---------------------------------------------------------------------------
# Fixtures — stessa struttura di test_api_routers.py
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app():
    """App con tutti i router testati; verify_personal_key bypassata."""
    _app = FastAPI()
    for r in [system.router, autopilot.router, finance.router,
              memory_routes.router, personal.router, wiki.router]:
        _app.include_router(r)
    _app.dependency_overrides[_state.verify_personal_key] = lambda: None
    yield _app
    _app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def unauth_app():
    """App senza override auth — per testare 403."""
    _app = FastAPI()
    for r in [system.router, autopilot.router, finance.router,
              memory_routes.router, personal.router, wiki.router]:
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
    async with AsyncClient(
        transport=ASGITransport(app=unauth_app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Middleware — app separata con RequestIDMiddleware
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mw_app():
    from apps.backend.api.middleware import RequestIDMiddleware

    _app = FastAPI()
    _app.add_middleware(RequestIDMiddleware)

    @_app.get("/ping")
    async def _ping():
        return {"ok": True}

    return _app


@pytest.fixture
async def mw_client(mw_app):
    async with AsyncClient(
        transport=ASGITransport(app=mw_app),
        base_url="http://test",
    ) as ac:
        yield ac


# ===========================================================================
# Section 1 — finance.py / analytics
# ===========================================================================


async def test_finance_summary_200_with_tracker(client):
    """GET /api/finance/summary → 200 con tracker + memory mockati."""
    mock_tracker = AsyncMock()
    mock_tracker.monthly_summary = AsyncMock(
        return_value={"year": 2026, "month": 5, "n_sales": 3,
                      "gross_eur": 50.0, "net_eur": 40.0, "margin_pct": 80.0}
    )
    mock_tracker.pinterest_costs_month = AsyncMock(return_value=1.0)

    mock_cursor = AsyncMock()
    mock_cursor.fetchall = AsyncMock(return_value=[])
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_cursor)
    mock_memory = AsyncMock()
    mock_memory.get_db = AsyncMock(return_value=mock_db)

    prev_ft, prev_mem = _state.finance_tracker, _state.memory
    _state.finance_tracker = mock_tracker
    _state.memory = mock_memory
    try:
        r = await client.get("/api/finance/summary")
    finally:
        _state.finance_tracker = prev_ft
        _state.memory = prev_mem

    assert r.status_code == 200
    data = r.json()
    assert data["year"] == 2026
    assert "by_niche" in data
    assert isinstance(data["by_niche"], list)
    assert data["pinterest_costs_eur"] == 1.0


async def test_finance_summary_500_tracker_exception(client):
    """GET /api/finance/summary → 500 se finance_tracker.monthly_summary solleva."""
    mock_tracker = AsyncMock()
    mock_tracker.monthly_summary = AsyncMock(side_effect=RuntimeError("db crash"))

    prev = _state.finance_tracker
    _state.finance_tracker = mock_tracker
    try:
        r = await client.get("/api/finance/summary")
    finally:
        _state.finance_tracker = prev

    assert r.status_code == 500
    assert "detail" in r.json()


async def test_finance_report_days_param_7_with_memory(client):
    """GET /api/finance/report?days=7 → 200, days nel body."""
    mock_memory = AsyncMock()
    mock_memory.query_chromadb = AsyncMock(return_value=[])

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/finance/report?days=7")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    data = r.json()
    assert data["days"] == 7
    assert data["report"] is None


async def test_finance_report_days_validation_low(client):
    """GET /api/finance/report?days=0 → 422."""
    r = await client.get("/api/finance/report?days=0")
    assert r.status_code == 422


async def test_finance_report_days_validation_high(client):
    """GET /api/finance/report?days=366 → 422."""
    r = await client.get("/api/finance/report?days=366")
    assert r.status_code == 422


async def test_finance_report_with_memory_has_results(client):
    """GET /api/finance/report → 200, report non-None se ChromaDB restituisce dati."""
    mock_memory = AsyncMock()
    mock_memory.query_chromadb = AsyncMock(return_value=[{"type": "finance_report", "text": "..."}])

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/finance/report")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    assert r.json()["report"] is not None


async def test_finance_run_with_pepe_dispatches_task(client):
    """POST /api/finance/run → 200 con pepe mockato; verifica dispatch."""
    mock_pepe = AsyncMock()
    mock_pepe.dispatch_task = AsyncMock(return_value=None)

    prev = _state.pepe
    _state.pepe = mock_pepe
    try:
        r = await client.post("/api/finance/run", json={"period_days": 14})
    finally:
        _state.pepe = prev

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "dispatched"
    assert data["period_days"] == 14
    assert "task_id" in data
    mock_pepe.dispatch_task.assert_awaited_once()


async def test_analytics_ctr_ab_no_memory(client):
    """GET /api/analytics/ctr-ab → 200, results=[] quando memory=None."""
    prev = _state.memory
    _state.memory = None
    try:
        r = await client.get("/api/analytics/ctr-ab")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    assert r.json() == {"results": []}


async def test_analytics_ctr_ab_with_results(client):
    """GET /api/analytics/ctr-ab → 200, results popolati da ChromaDB mock."""
    raw = [{
        "metadata": {
            "niche": "svg-planner",
            "product_type": "printable_pdf",
            "template": "clean",
            "color_scheme": "pastel",
            "ctr": 0.05,
            "loser_template": "busy",
            "loser_color_scheme": "dark",
            "loser_ctr": 0.02,
            "date": "2026-05-01",
        }
    }]
    mock_memory = AsyncMock()
    mock_memory.query_chromadb = AsyncMock(return_value=raw)

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/analytics/ctr-ab")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 1
    assert results[0]["niche"] == "svg-planner"
    assert results[0]["winner"]["ctr"] == 0.05
    assert results[0]["loser"]["ctr"] == 0.02


async def test_analytics_ctr_ab_limit_validation(client):
    """GET /api/analytics/ctr-ab?limit=101 → 422."""
    r = await client.get("/api/analytics/ctr-ab?limit=101")
    assert r.status_code == 422


async def test_analytics_ctr_ab_requires_auth(unauth_client):
    """GET /api/analytics/ctr-ab senza auth → 403."""
    r = await unauth_client.get("/api/analytics/ctr-ab")
    assert r.status_code == 403


async def test_analytics_ctr_ab_exception_returns_500(client):
    """GET /api/analytics/ctr-ab → 500 se query_chromadb solleva."""
    mock_memory = AsyncMock()
    mock_memory.query_chromadb = AsyncMock(side_effect=RuntimeError("chroma down"))

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/analytics/ctr-ab")
    finally:
        _state.memory = prev

    assert r.status_code == 500


async def test_analytics_failures_with_memory_data(client):
    """GET /api/analytics/failures → 200 con dati da get_all_listing_analyses."""
    mock_memory = AsyncMock()
    mock_memory.get_all_listing_analyses = AsyncMock(
        return_value=[{"listing_id": 1, "reason": "low ctr"}]
    )

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/analytics/failures")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    data = r.json()
    assert len(data["failures"]) == 1
    assert data["failures"][0]["listing_id"] == 1


async def test_analytics_latest_with_memory_data(client):
    """GET /api/analytics/latest → 200, report presente."""
    mock_memory = AsyncMock()
    mock_memory.query_chromadb = AsyncMock(return_value=[{"type": "analytics_report"}])

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/analytics/latest")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    assert r.json()["report"] is not None


async def test_analytics_ladder_with_db_mock(client):
    """GET /api/analytics/ladder → 200 con DB mockato, valori reali."""
    mock_cursor1 = AsyncMock()
    mock_cursor1.fetchall = AsyncMock(return_value=[
        {"ladder_level": "ok", "cnt": 5},
        {"ladder_level": "views_low", "cnt": 2},
        {"ladder_level": "ctr_low", "cnt": 1},
    ])
    mock_cursor2 = AsyncMock()
    mock_cursor2.fetchone = AsyncMock(return_value={"last": 1_700_000_000.0})

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[mock_cursor1, mock_cursor2])
    mock_memory = AsyncMock()
    mock_memory.get_db = AsyncMock(return_value=mock_db)

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/analytics/ladder")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    data = r.json()
    assert data["ok"] == 5
    assert data["views_low"] == 2
    assert data["ctr_low"] == 1
    assert data["total"] == 8
    assert data["last_updated"] == 1_700_000_000.0


# ===========================================================================
# Section 2 — wiki.py
# ===========================================================================


def _make_mock_pepe_with_wiki(*, stats=None, query_result=None, niche_content=None,
                               lint_report=None):
    """Helper: crea mock pepe con wiki configurato."""
    mock_wiki = AsyncMock()
    mock_wiki.get_stats = AsyncMock(return_value=stats or {"etsy": 10, "personal": 5})
    mock_wiki.query = AsyncMock(return_value=query_result or "risultato query")
    mock_wiki.get_niche_context = AsyncMock(return_value=niche_content)
    mock_wiki.lint = AsyncMock(return_value=lint_report or {"broken_links": 0})

    mock_pepe = MagicMock()
    mock_pepe.wiki = mock_wiki
    mock_pepe.client = MagicMock()         # llm_etsy
    mock_pepe._local_client = MagicMock()  # llm_personal
    mock_pepe.set_active_domain = MagicMock()
    return mock_pepe


async def test_wiki_stats_with_pepe(client):
    """GET /api/wiki/stats → 200 con pepe.wiki.get_stats mockato."""
    mock_pepe = _make_mock_pepe_with_wiki(stats={"etsy": 12, "personal": 3})

    prev = _state.pepe
    _state.pepe = mock_pepe
    try:
        r = await client.get("/api/wiki/stats")
    finally:
        _state.pepe = prev

    assert r.status_code == 200
    assert r.json()["etsy"] == 12


async def test_wiki_query_with_pepe_returns_result(client):
    """GET /api/wiki/query?domain=etsy&q=mandala → 200 con risultato."""
    mock_pepe = _make_mock_pepe_with_wiki(query_result="info su mandala")

    prev = _state.pepe
    _state.pepe = mock_pepe
    try:
        r = await client.get("/api/wiki/query?domain=etsy&q=mandala")
    finally:
        _state.pepe = prev

    assert r.status_code == 200
    data = r.json()
    assert data["domain"] == "etsy"
    assert data["query"] == "mandala"
    assert data["result"] == "info su mandala"


async def test_wiki_query_empty_q_with_pepe_returns_400(client):
    """GET /api/wiki/query (q vuoto, pepe presente) → 400."""
    mock_pepe = _make_mock_pepe_with_wiki()

    prev = _state.pepe
    _state.pepe = mock_pepe
    try:
        r = await client.get("/api/wiki/query?q=")
    finally:
        _state.pepe = prev

    assert r.status_code == 400
    assert "error" in r.json()


async def test_wiki_niche_found(client):
    """GET /api/wiki/niche/mandala → 200 con content."""
    mock_pepe = _make_mock_pepe_with_wiki(niche_content="# Mandala\nArtwork...")

    prev = _state.pepe
    _state.pepe = mock_pepe
    try:
        r = await client.get("/api/wiki/niche/mandala")
    finally:
        _state.pepe = prev

    assert r.status_code == 200
    data = r.json()
    assert data["niche"] == "mandala"
    assert "Mandala" in data["content"]


async def test_wiki_niche_returns_404_when_not_found(client):
    """GET /api/wiki/niche/missing-niche → 404 se get_niche_context=None."""
    mock_pepe = _make_mock_pepe_with_wiki(niche_content=None)

    prev = _state.pepe
    _state.pepe = mock_pepe
    try:
        r = await client.get("/api/wiki/niche/missing-niche")
    finally:
        _state.pepe = prev

    assert r.status_code == 404
    assert "error" in r.json()


async def test_wiki_niche_invalid_chars_returns_400(client):
    """GET /api/wiki/niche/!bad@name → 400 (regex niche non valida)."""
    r = await client.get("/api/wiki/niche/!bad@name")
    assert r.status_code == 400
    assert "error" in r.json()


async def test_wiki_lint_with_pepe(client):
    """POST /api/wiki/lint → 200 con report da mock."""
    mock_pepe = _make_mock_pepe_with_wiki(lint_report={"broken_links": 2, "pending_raw": 0})

    prev = _state.pepe
    _state.pepe = mock_pepe
    try:
        r = await client.post("/api/wiki/lint", json={"domain": "etsy"})
    finally:
        _state.pepe = prev

    assert r.status_code == 200
    data = r.json()
    assert data["domain"] == "etsy"
    assert data["report"]["broken_links"] == 2


async def test_wiki_domain_switch_no_pepe(client):
    """POST /api/domain senza pepe → 503."""
    prev = _state.pepe
    _state.pepe = None
    try:
        r = await client.post("/api/domain", json={"domain": "etsy"})
    finally:
        _state.pepe = prev

    assert r.status_code == 503
    assert "error" in r.json()


async def test_wiki_domain_switch_etsy(client):
    """POST /api/domain {"domain": "etsy"} → 200, pepe.set_active_domain chiamato."""
    mock_pepe = _make_mock_pepe_with_wiki()

    with patch.object(_state.ws_manager, "broadcast", new_callable=AsyncMock) as mock_bcast:
        prev = _state.pepe
        _state.pepe = mock_pepe
        try:
            r = await client.post("/api/domain", json={"domain": "etsy"})
        finally:
            _state.pepe = prev

    assert r.status_code == 200
    assert r.json()["domain"] == "etsy"
    mock_bcast.assert_awaited_once()
    mock_pepe.set_active_domain.assert_called_once()


# ===========================================================================
# Section 3 — memory_routes.py
# ===========================================================================


async def test_memory_stats_with_chroma_available(client):
    """GET /api/memory/stats → 200 con chroma.available=True."""
    mock_memory = AsyncMock()
    mock_memory.get_chroma_stats = AsyncMock(
        return_value={"available": True, "count": 42}
    )

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/memory/stats")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    assert r.json()["chroma"]["available"] is True
    assert r.json()["chroma"]["count"] == 42


async def test_memory_graph_with_none_collections(client):
    """GET /api/memory/graph → 200 con collection=None; nodi=0, archi=0."""
    mock_memory = MagicMock()
    mock_memory._chroma_collection = None
    mock_memory._screen_memory_collection = None
    mock_memory._personal_memory_collection = None
    mock_memory._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/memory/graph")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    data = r.json()
    assert "nodes" in data and "edges" in data and "meta" in data
    assert data["meta"]["total_nodes"] == 0
    assert data["meta"]["total_edges"] == 0


async def test_memory_graph_valid_custom_threshold(client):
    """GET /api/memory/graph?threshold=0.5 → 200."""
    mock_memory = MagicMock()
    mock_memory._chroma_collection = None
    mock_memory._screen_memory_collection = None
    mock_memory._personal_memory_collection = None
    mock_memory._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/memory/graph?threshold=0.5")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    assert r.json()["meta"]["threshold"] == 0.5


async def test_memory_node_no_memory_503(client):
    """GET /api/memory/node/doc-1 → 503 quando memory=None."""
    prev = _state.memory
    _state.memory = None
    try:
        r = await client.get("/api/memory/node/doc-1")
    finally:
        _state.memory = prev

    assert r.status_code == 503
    assert "error" in r.json()


async def test_memory_node_found_200(client):
    """GET /api/memory/node/doc-1 → 200 con documento mockato."""
    mock_col = MagicMock()
    mock_col.get = MagicMock(return_value={
        "ids": ["doc-1"],
        "documents": ["Testo documento"],
        "metadatas": [{"type": "insight"}],
    })

    mock_memory = MagicMock()
    mock_memory._chroma_collection = mock_col
    mock_memory._screen_memory_collection = MagicMock()
    mock_memory._personal_memory_collection = MagicMock()
    mock_memory._shared_memory_collection = MagicMock()
    mock_memory.get_node_access_history = AsyncMock(return_value=[])

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/memory/node/doc-1?collection=pepe_memory")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "doc-1"
    assert data["document"] == "Testo documento"
    assert data["metadata"] == {"type": "insight"}
    assert data["collection"] == "pepe_memory"
    assert isinstance(data["access_history"], list)


async def test_memory_node_not_found_404(client):
    """GET /api/memory/node/missing → 404 se la collection non contiene il doc."""
    mock_col = MagicMock()
    mock_col.get = MagicMock(return_value={"ids": [], "documents": [], "metadatas": []})

    mock_memory = MagicMock()
    mock_memory._chroma_collection = mock_col
    mock_memory._screen_memory_collection = MagicMock()
    mock_memory._personal_memory_collection = MagicMock()
    mock_memory._shared_memory_collection = MagicMock()

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/memory/node/missing?collection=pepe_memory")
    finally:
        _state.memory = prev

    assert r.status_code == 404
    assert "error" in r.json()


async def test_memory_node_invalid_collection_503(client):
    """GET /api/memory/node/doc?collection=bogus → 503."""
    mock_memory = MagicMock()
    mock_memory._chroma_collection = MagicMock()
    mock_memory._screen_memory_collection = MagicMock()
    mock_memory._personal_memory_collection = MagicMock()
    mock_memory._shared_memory_collection = MagicMock()

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/memory/node/doc-1?collection=bogus_collection")
    finally:
        _state.memory = prev

    assert r.status_code == 503
    assert "error" in r.json()


# ===========================================================================
# Section 4 — personal.py
# ===========================================================================


async def test_personal_reminders_with_data(client):
    """GET /api/personal/reminders → 200 con items popolati."""
    mock_memory = AsyncMock()
    mock_memory.get_pending_reminders = AsyncMock(return_value=[
        {"id": 1, "text": "Buy milk", "trigger_at": "2026-05-15T09:00:00+00:00", "status": "pending"},
    ])

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/personal/reminders")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["message"] == "Buy milk"
    assert items[0]["status"] == "pending"


async def test_personal_recalls_with_data(client):
    """GET /api/personal/recalls → 200 con items popolati."""
    mock_memory = AsyncMock()
    mock_memory.get_personal_recalls = AsyncMock(return_value=[
        {"created_at": "2026-05-10T10:00:00+00:00", "agent": "recall", "query": "test q", "status": "ok"},
    ])

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/personal/recalls")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["query"] == "test q"
    assert items[0]["status"] == "ok"


async def test_personal_stats_with_memory(client):
    """GET /api/personal/stats → 200 con stats popolate."""
    mock_memory = AsyncMock()
    mock_memory.get_domain_agent_stats = AsyncMock(
        return_value={"recall": {"completed": 5, "failed": 1}}
    )

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/personal/stats?days=7")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    data = r.json()
    assert data["days"] == 7
    assert data["stats"]["recall"]["completed"] == 5


async def test_voice_collect_set_mode_positive(client):
    """POST /api/personal/voice/collect {"mode": "positive"} → 200."""
    with patch("apps.backend.voice.collector.set_mode"), \
         patch("apps.backend.voice.collector.get_status",
               return_value={"mode": "positive", "positive": 0, "negative": 0}):
        r = await client.post("/api/personal/voice/collect", json={"mode": "positive"})

    assert r.status_code == 200
    assert r.json()["mode"] == "positive"


async def test_voice_collect_set_mode_invalid_422(client):
    """POST /api/personal/voice/collect {"mode": "bad"} → 422."""
    r = await client.post("/api/personal/voice/collect", json={"mode": "bad"})
    assert r.status_code == 422


async def test_voice_collect_status_get(client):
    """GET /api/personal/voice/collect/status → 200."""
    with patch("apps.backend.voice.collector.get_status",
               return_value={"mode": "off", "positive": 5, "negative": 3}):
        r = await client.get("/api/personal/voice/collect/status")

    assert r.status_code == 200
    assert r.json()["mode"] == "off"


async def test_personal_ask_no_pepe_503(client):
    """POST /api/personal/ask → 503 quando pepe=None."""
    prev = _state.pepe
    _state.pepe = None
    try:
        r = await client.post("/api/personal/ask", json={"text": "ciao"})
    finally:
        _state.pepe = prev

    assert r.status_code == 503
    assert "error" in r.json()


async def test_personal_ask_with_pepe_200(client):
    """POST /api/personal/ask → 200 con risposta di pepe."""
    mock_pepe = AsyncMock()
    mock_pepe.handle_user_message = AsyncMock(return_value="Ciao! Come posso aiutarti?")

    prev = _state.pepe
    _state.pepe = mock_pepe
    try:
        r = await client.post("/api/personal/ask", json={"text": "ciao"})
    finally:
        _state.pepe = prev

    assert r.status_code == 200
    assert r.json()["response"] == "Ciao! Come posso aiutarti?"
    mock_pepe.handle_user_message.assert_awaited_once()


async def test_personal_ask_empty_text_422(client):
    """POST /api/personal/ask {"text": ""} → 422."""
    r = await client.post("/api/personal/ask", json={"text": "  "})
    assert r.status_code == 422


async def test_personal_ask_too_long_422(client):
    """POST /api/personal/ask con text > 4000 chars → 422."""
    r = await client.post("/api/personal/ask", json={"text": "x" * 4001})
    assert r.status_code == 422


async def test_personal_mcp_status_not_configured(client):
    """GET /api/personal/mcp/status → 200 con not_configured quando token assenti."""
    with patch("apps.backend.api.routers.personal.settings") as mock_settings:
        mock_settings.NOTION_API_TOKEN = ""
        mock_settings.GOOGLE_REFRESH_TOKEN = ""
        r = await client.get("/api/personal/mcp/status")

    assert r.status_code == 200
    data = r.json()
    assert data["notion"] == "not_configured"
    assert data["gmail"] == "not_configured"
    assert data["calendar"] == "not_configured"


# ===========================================================================
# Section 5 — system.py
# ===========================================================================


async def test_health_no_memory_503(client):
    """GET /api/health → 503 quando memory=None."""
    prev = _state.memory
    _state.memory = None
    try:
        r = await client.get("/api/health")
    finally:
        _state.memory = prev

    assert r.status_code == 503
    data = r.json()
    assert "db" in data
    assert data["db"] != "ok"


async def test_domains_config_200(client):
    """GET /api/domains/config → 200 con campi etsy e personal."""
    r = await client.get("/api/domains/config")
    assert r.status_code == 200
    data = r.json()
    assert "etsy" in data and "personal" in data
    assert "agents" in data["etsy"]
    assert isinstance(data["etsy"]["agents"], list)
    assert "agents" in data["personal"]


async def test_listings_no_memory_200(client):
    """GET /api/listings → 200 con listings=[] quando memory=None."""
    prev = _state.memory
    _state.memory = None
    try:
        r = await client.get("/api/listings")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    assert r.json() == {"listings": []}


async def test_listings_with_memory_200(client):
    """GET /api/listings → 200 con listings da memory mockata."""
    mock_memory = AsyncMock()
    mock_memory.get_etsy_listings = AsyncMock(
        return_value=[{"listing_id": 1, "title": "Test listing"}]
    )

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/listings")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    listings = r.json()["listings"]
    assert len(listings) == 1
    assert listings[0]["title"] == "Test listing"


async def test_scheduler_jobs_no_scheduler(client):
    """GET /api/scheduler/jobs → 200 con jobs=[] quando scheduler=None."""
    prev = _state.scheduler
    _state.scheduler = None
    try:
        r = await client.get("/api/scheduler/jobs")
    finally:
        _state.scheduler = prev

    assert r.status_code == 200
    assert r.json() == {"jobs": []}


async def test_scheduler_jobs_with_scheduler(client):
    """GET /api/scheduler/jobs → 200 con jobs da scheduler mockato."""
    mock_scheduler = MagicMock()
    mock_scheduler.get_jobs = MagicMock(return_value=[
        {"id": "job-1", "name": "analytics", "trigger": "cron", "next_run": None}
    ])

    prev = _state.scheduler
    _state.scheduler = mock_scheduler
    try:
        r = await client.get("/api/scheduler/jobs")
    finally:
        _state.scheduler = prev

    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["id"] == "job-1"


async def test_task_timeline_no_memory(client):
    """GET /api/tasks/task-42/timeline → 200 con timeline=[] quando memory=None."""
    prev = _state.memory
    _state.memory = None
    try:
        r = await client.get("/api/tasks/task-42/timeline")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    data = r.json()
    assert data["timeline"] == []


async def test_task_timeline_with_memory(client):
    """GET /api/tasks/task-42/timeline → 200 con steps da memory mockata."""
    mock_memory = AsyncMock()
    mock_memory.get_task_timeline = AsyncMock(
        return_value=[{"step": 1, "agent": "analytics", "status": "completed"}]
    )

    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/tasks/task-42/timeline")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "task-42"
    assert len(data["timeline"]) == 1
    assert data["timeline"][0]["agent"] == "analytics"


# ===========================================================================
# Section 6 — autopilot.py (success paths con mock loop)
# ===========================================================================


async def test_autopilot_status_running(client):
    """GET /api/autopilot/status → 200 con status=running da mock loop."""
    mock_loop = AsyncMock()
    mock_loop._get_status = AsyncMock(return_value="running")
    mock_loop._state_get = AsyncMock(side_effect=["svg-planner", "1700000000.0"])

    prev_loop, prev_mem = _state.autopilot_loop, _state.memory
    _state.autopilot_loop = mock_loop
    _state.memory = None  # semplifica: no DB query per items_today
    try:
        r = await client.get("/api/autopilot/status")
    finally:
        _state.autopilot_loop = prev_loop
        _state.memory = prev_mem

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "running"
    assert data["current_niche"] == "svg-planner"
    assert data["last_run_at"] == 1_700_000_000.0


async def test_autopilot_status_paused(client):
    """GET /api/autopilot/status → 200 con status=paused."""
    mock_loop = AsyncMock()
    mock_loop._get_status = AsyncMock(return_value="paused_manual")
    mock_loop._state_get = AsyncMock(side_effect=["", ""])

    prev_loop, prev_mem = _state.autopilot_loop, _state.memory
    _state.autopilot_loop = mock_loop
    _state.memory = None
    try:
        r = await client.get("/api/autopilot/status")
    finally:
        _state.autopilot_loop = prev_loop
        _state.memory = prev_mem

    assert r.status_code == 200
    assert r.json()["status"] == "paused"


async def test_autopilot_status_loop_exception(client):
    """GET /api/autopilot/status → 500 se loop._get_status solleva."""
    mock_loop = AsyncMock()
    mock_loop._get_status = AsyncMock(side_effect=RuntimeError("loop crashed"))

    prev = _state.autopilot_loop
    _state.autopilot_loop = mock_loop
    try:
        r = await client.get("/api/autopilot/status")
    finally:
        _state.autopilot_loop = prev

    assert r.status_code == 500


async def test_autopilot_start_with_loop(client):
    """POST /api/autopilot/start → 200 con status=running."""
    mock_loop = AsyncMock()
    mock_loop.resume = AsyncMock(return_value=None)

    prev = _state.autopilot_loop
    _state.autopilot_loop = mock_loop
    try:
        r = await client.post("/api/autopilot/start")
    finally:
        _state.autopilot_loop = prev

    assert r.status_code == 200
    assert r.json()["status"] == "running"
    mock_loop.resume.assert_awaited_once()


async def test_autopilot_pause_with_loop(client):
    """POST /api/autopilot/pause → 200 con status=paused."""
    mock_loop = AsyncMock()
    mock_loop.stop = AsyncMock(return_value=None)

    prev = _state.autopilot_loop
    _state.autopilot_loop = mock_loop
    try:
        r = await client.post("/api/autopilot/pause")
    finally:
        _state.autopilot_loop = prev

    assert r.status_code == 200
    assert r.json()["status"] == "paused"
    mock_loop.stop.assert_awaited_once()


async def test_autopilot_stop_with_loop(client):
    """POST /api/autopilot/stop → 200 con status=stopped."""
    mock_loop = AsyncMock()
    mock_loop.stop = AsyncMock(return_value=None)

    prev = _state.autopilot_loop
    _state.autopilot_loop = mock_loop
    try:
        r = await client.post("/api/autopilot/stop")
    finally:
        _state.autopilot_loop = prev

    assert r.status_code == 200
    assert r.json()["status"] == "stopped"
    mock_loop.stop.assert_awaited_once_with(final=True)


async def test_run_analytics_with_pepe(client):
    """POST /api/run/analytics → 200 con pepe mockato."""
    mock_pepe = MagicMock()
    mock_pepe.dispatch_task = AsyncMock(return_value=None)
    mock_pepe._fire = MagicMock(return_value=None)

    prev = _state.pepe
    _state.pepe = mock_pepe
    try:
        r = await client.post("/api/run/analytics")
    finally:
        _state.pepe = prev

    assert r.status_code == 200
    assert r.json()["status"] == "started"
    mock_pepe._fire.assert_called_once()


# ===========================================================================
# Section 7 — state.py: ConnectionManager unit tests
# ===========================================================================


from apps.backend.api.state import ConnectionManager  # noqa: E402


async def test_connection_manager_initial_empty():
    """ConnectionManager inizialmente ha 0 connessioni."""
    mgr = ConnectionManager()
    assert len(mgr._connections) == 0


async def test_connection_manager_connect():
    """connect() accetta il ws e lo aggiunge alle connessioni attive."""
    mgr = ConnectionManager()
    ws = AsyncMock()
    ws.accept = AsyncMock(return_value=None)

    await mgr.connect(ws)

    assert ws in mgr._connections
    ws.accept.assert_awaited_once_with(subprotocol=None)


async def test_connection_manager_disconnect():
    """disconnect() rimuove il ws dalle connessioni attive."""
    mgr = ConnectionManager()
    ws = AsyncMock()
    mgr._connections.append(ws)

    mgr.disconnect(ws)

    assert ws not in mgr._connections
    assert len(mgr._connections) == 0


async def test_connection_manager_broadcast():
    """broadcast() invia send_json a tutti i ws connessi."""
    mgr = ConnectionManager()
    # Usa call_count + call_args per evitare il warning Python 3.12 asyncmock internals
    send1, send2 = AsyncMock(return_value=None), AsyncMock(return_value=None)
    ws1, ws2 = MagicMock(), MagicMock()
    ws1.send_json, ws2.send_json = send1, send2
    mgr._connections = [ws1, ws2]

    event = {"type": "test", "data": 42}
    await mgr.broadcast(event)

    assert send1.call_count == 1
    assert send1.call_args[0][0] == event
    assert send2.call_count == 1
    assert send2.call_args[0][0] == event


async def test_connection_manager_broadcast_dead_ws():
    """broadcast() rimuove i ws che sollevano eccezione e continua con gli altri."""
    mgr = ConnectionManager()
    dead_send = AsyncMock(side_effect=Exception("connection closed"))
    live_send = AsyncMock(return_value=None)
    dead_ws, live_ws = MagicMock(), MagicMock()
    dead_ws.send_json, live_ws.send_json = dead_send, live_send
    mgr._connections = [dead_ws, live_ws]

    await mgr.broadcast({"type": "ping"})

    assert dead_ws not in mgr._connections
    assert live_ws in mgr._connections
    assert live_send.call_count == 1


# ===========================================================================
# Section 8 — state.py: verify_personal_key unit tests
# ===========================================================================


from apps.backend.api.state import verify_personal_key  # noqa: E402


async def test_verify_personal_key_valid():
    """verify_personal_key non solleva con chiave corretta."""
    mock_request = MagicMock()
    mock_request.headers = {"X-Personal-Key": "super-secret"}

    with patch("apps.backend.api.state.settings") as mock_settings:
        mock_settings.PERSONAL_API_KEY = "super-secret"
        # Non deve sollevare
        await verify_personal_key(mock_request)


async def test_verify_personal_key_wrong():
    """verify_personal_key solleva HTTPException 403 con chiave errata."""
    mock_request = MagicMock()
    mock_request.headers = {"X-Personal-Key": "wrong-key"}

    with patch("apps.backend.api.state.settings") as mock_settings:
        mock_settings.PERSONAL_API_KEY = "correct-key"
        with pytest.raises(HTTPException) as exc_info:
            await verify_personal_key(mock_request)

    assert exc_info.value.status_code == 403


async def test_verify_personal_key_missing():
    """verify_personal_key solleva 403 quando header assente."""
    mock_request = MagicMock()
    mock_request.headers = {}  # dict vuoto: .get("X-Personal-Key", "") → ""

    with patch("apps.backend.api.state.settings") as mock_settings:
        mock_settings.PERSONAL_API_KEY = "correct-key"
        with pytest.raises(HTTPException) as exc_info:
            await verify_personal_key(mock_request)

    assert exc_info.value.status_code == 403


async def test_verify_personal_key_not_configured():
    """verify_personal_key solleva 403 quando PERSONAL_API_KEY non configurata."""
    mock_request = MagicMock()
    mock_request.headers = {"X-Personal-Key": "any-key"}

    with patch("apps.backend.api.state.settings") as mock_settings:
        mock_settings.PERSONAL_API_KEY = ""
        with pytest.raises(HTTPException) as exc_info:
            await verify_personal_key(mock_request)

    assert exc_info.value.status_code == 403


# ===========================================================================
# Section 9 — middleware.py: RequestIDMiddleware + RequestIDFilter
# ===========================================================================


async def test_request_id_middleware_adds_header(mw_client):
    """RequestIDMiddleware aggiunge header X-Request-ID alla response."""
    r = await mw_client.get("/ping")
    assert r.status_code == 200
    assert "x-request-id" in r.headers


async def test_request_id_middleware_generates_valid_uuid(mw_client):
    """X-Request-ID generato automaticamente è un UUID valido."""
    r = await mw_client.get("/ping")
    req_id = r.headers["x-request-id"]
    assert req_id  # non vuoto
    uuid.UUID(req_id)  # lancia ValueError se non valido


async def test_request_id_middleware_passthrough_id(mw_client):
    """X-Request-ID dall'incoming request viene preservato nella response."""
    r = await mw_client.get("/ping", headers={"X-Request-ID": "my-custom-id-123"})
    assert r.headers["x-request-id"] == "my-custom-id-123"


def test_request_id_filter_injects_field():
    """RequestIDFilter.filter() inietta request_id nel log record."""
    from apps.backend.api.middleware import RequestIDFilter, request_id_ctx

    filt = RequestIDFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)

    token = request_id_ctx.set("injected-id")
    try:
        filt.filter(record)
    finally:
        request_id_ctx.reset(token)

    assert record.request_id == "injected-id"


def test_request_id_filter_returns_true():
    """RequestIDFilter.filter() ritorna True (non filtra il record)."""
    from apps.backend.api.middleware import RequestIDFilter

    filt = RequestIDFilter()
    record = logging.LogRecord("test", logging.DEBUG, "", 0, "msg", (), None)
    result = filt.filter(record)
    assert result is True
