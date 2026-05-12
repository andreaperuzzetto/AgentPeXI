"""test_unit_gaps_r6b.py

~55 pytest test per colmare gap di copertura identificati nel round 6b.

Sezioni:
  1. api/routers/wiki.py        — pepe.wiki=None, exception paths, domain switch personal
  2. api/routers/system.py      — health exception, status queue, agents, costs, filters
  3. api/routers/autopilot.py   — _map_autopilot_status unit + exception paths POST
  4. api/routers/personal.py    — mcp/status aiohttp mock (200/401/exc), ask exception
  5. telegram/handlers/autopilot.py — loop=None, edit exception, bundle, cb_unknown

Pattern identico a test_api_routers.py e test_api_routers_round4.py:
  httpx.AsyncClient + ASGITransport, dependency_overrides[verify_personal_key].
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import apps.backend.api.state as _state
from apps.backend.api.routers import autopilot, personal, system, wiki

# ---------------------------------------------------------------------------
# Fixtures — pattern identico a test_api_routers.py
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app():
    _app = FastAPI()
    for r in [system.router, autopilot.router, personal.router, wiki.router]:
        _app.include_router(r)
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
# Helper: mock aiohttp.ClientSession
# ---------------------------------------------------------------------------


def _mock_aiohttp_cs(status: int | None = None, raise_exc: Exception | None = None):
    """Crea un mock per aiohttp.ClientSession.

    Se raise_exc è impostato, __aenter__ solleva quell'eccezione.
    Altrimenti il mock session.get(...) restituisce resp.status=status.
    """
    if raise_exc is not None:
        mock_cs_instance = AsyncMock()
        mock_cs_instance.__aenter__ = AsyncMock(side_effect=raise_exc)
        mock_cs_instance.__aexit__ = AsyncMock(return_value=False)
        return MagicMock(return_value=mock_cs_instance)

    mock_resp = MagicMock()
    mock_resp.status = status

    mock_get_ctx = AsyncMock()
    mock_get_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_get_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_get_ctx)

    mock_cs_instance = AsyncMock()
    mock_cs_instance.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cs_instance.__aexit__ = AsyncMock(return_value=False)

    return MagicMock(return_value=mock_cs_instance)


# ---------------------------------------------------------------------------
# Helper: costruisce un mock pepe con wiki e llm configurabili
# ---------------------------------------------------------------------------


def _make_pepe_wiki(
    get_stats_exc=None,
    query_exc=None,
    niche_exc=None,
    lint_exc=None,
    wiki=True,
    client=True,
    local_client=True,
):
    pepe = AsyncMock()
    if wiki:
        pepe.wiki = AsyncMock()
        pepe.wiki.get_stats = AsyncMock(
            side_effect=get_stats_exc,
            return_value={"files": 5},
        ) if get_stats_exc else AsyncMock(return_value={"files": 5})
        pepe.wiki.query = (
            AsyncMock(side_effect=query_exc) if query_exc
            else AsyncMock(return_value="risultato query")
        )
        pepe.wiki.get_niche_context = (
            AsyncMock(side_effect=niche_exc) if niche_exc
            else AsyncMock(return_value="contesto niche")
        )
        pepe.wiki.lint = (
            AsyncMock(side_effect=lint_exc) if lint_exc
            else AsyncMock(return_value="report lint")
        )
    else:
        pepe.wiki = None
    pepe.client = MagicMock() if client else None
    pepe._local_client = MagicMock() if local_client else None
    pepe.set_active_domain = MagicMock()
    return pepe


# ===========================================================================
# Section 1 — wiki.py (13 test)
# ===========================================================================


async def test_wiki_stats_wiki_none_503(client):
    """GET /api/wiki/stats → 503 quando pepe.wiki è None (pepe esiste, wiki no)."""
    pepe = _make_pepe_wiki(wiki=False)
    prev = _state.pepe
    _state.pepe = pepe
    try:
        r = await client.get("/api/wiki/stats")
    finally:
        _state.pepe = prev
    assert r.status_code == 503
    assert "WikiManager" in r.json()["error"]


async def test_wiki_stats_exception_500(client):
    """GET /api/wiki/stats → 500 se wiki.get_stats() solleva Exception."""
    pepe = _make_pepe_wiki(get_stats_exc=RuntimeError("db crash"))
    prev = _state.pepe
    _state.pepe = pepe
    try:
        r = await client.get("/api/wiki/stats")
    finally:
        _state.pepe = prev
    assert r.status_code == 500


async def test_wiki_query_wiki_none_503(client):
    """GET /api/wiki/query?q=test → 503 quando pepe.wiki è None."""
    pepe = _make_pepe_wiki(wiki=False)
    prev = _state.pepe
    _state.pepe = pepe
    try:
        r = await client.get("/api/wiki/query", params={"q": "test"})
    finally:
        _state.pepe = prev
    assert r.status_code == 503
    assert "WikiManager" in r.json()["error"]


async def test_wiki_query_llm_etsy_none_503(client):
    """GET /api/wiki/query?q=test&domain=etsy → 503 quando client e _local_client sono None."""
    pepe = _make_pepe_wiki(client=False, local_client=False)
    prev = _state.pepe
    _state.pepe = pepe
    try:
        r = await client.get("/api/wiki/query", params={"q": "test", "domain": "etsy"})
    finally:
        _state.pepe = prev
    assert r.status_code == 503
    assert "LLM" in r.json()["error"]


async def test_wiki_query_local_client_none_503(client):
    """GET /api/wiki/query?domain=personal → 503 quando _local_client è None."""
    pepe = _make_pepe_wiki(client=True, local_client=False)
    prev = _state.pepe
    _state.pepe = pepe
    try:
        r = await client.get("/api/wiki/query", params={"q": "test", "domain": "personal"})
    finally:
        _state.pepe = prev
    assert r.status_code == 503
    assert "LLM" in r.json()["error"]


async def test_wiki_query_domain_personal_success(client):
    """GET /api/wiki/query?domain=personal → usa _local_client, ritorna 200."""
    pepe = _make_pepe_wiki()
    prev = _state.pepe
    _state.pepe = pepe
    try:
        r = await client.get("/api/wiki/query", params={"q": "test", "domain": "personal"})
    finally:
        _state.pepe = prev
    assert r.status_code == 200
    data = r.json()
    assert data["domain"] == "personal"
    assert "result" in data


async def test_wiki_query_exception_500(client):
    """GET /api/wiki/query?q=test → 500 se wiki.query() solleva Exception."""
    pepe = _make_pepe_wiki(query_exc=RuntimeError("crash"))
    prev = _state.pepe
    _state.pepe = pepe
    try:
        r = await client.get("/api/wiki/query", params={"q": "test"})
    finally:
        _state.pepe = prev
    assert r.status_code == 500


async def test_wiki_niche_exception_500(client):
    """GET /api/wiki/niche/test-niche → 500 se wiki.get_niche_context() solleva."""
    pepe = _make_pepe_wiki(niche_exc=RuntimeError("crash"))
    prev = _state.pepe
    _state.pepe = pepe
    try:
        r = await client.get("/api/wiki/niche/test-niche")
    finally:
        _state.pepe = prev
    assert r.status_code == 500


async def test_wiki_lint_both_llm_none_503(client):
    """POST /api/wiki/lint → 503 quando sia client che _local_client sono None."""
    pepe = _make_pepe_wiki(client=False, local_client=False)
    prev = _state.pepe
    _state.pepe = pepe
    try:
        r = await client.post("/api/wiki/lint", json={"domain": "etsy"})
    finally:
        _state.pepe = prev
    assert r.status_code == 503
    assert "LLM" in r.json()["error"]


async def test_wiki_lint_exception_500(client):
    """POST /api/wiki/lint → 500 se wiki.lint() solleva Exception."""
    pepe = _make_pepe_wiki(lint_exc=RuntimeError("crash"))
    prev = _state.pepe
    _state.pepe = pepe
    try:
        r = await client.post("/api/wiki/lint", json={"domain": "etsy"})
    finally:
        _state.pepe = prev
    assert r.status_code == 500


async def test_wiki_lint_domain_personal_success(client):
    """POST /api/wiki/lint {domain: personal} → usa _local_client → 200."""
    pepe = _make_pepe_wiki()
    prev = _state.pepe
    _state.pepe = pepe
    try:
        r = await client.post("/api/wiki/lint", json={"domain": "personal"})
    finally:
        _state.pepe = prev
    assert r.status_code == 200
    data = r.json()
    assert data["domain"] == "personal"
    assert "report" in data


async def test_wiki_domain_personal_set_active_none(client):
    """POST /api/domain {domain: personal} → set_active_domain(None) chiamato."""
    pepe = _make_pepe_wiki()
    ws = AsyncMock()
    ws.broadcast = AsyncMock()
    prev_pepe, prev_ws = _state.pepe, _state.ws_manager
    _state.pepe = pepe
    _state.ws_manager = ws
    try:
        r = await client.post("/api/domain", json={"domain": "personal"})
    finally:
        _state.pepe = prev_pepe
        _state.ws_manager = prev_ws
    assert r.status_code == 200
    pepe.set_active_domain.assert_called_once_with(None)


async def test_wiki_domain_broadcast_called(client):
    """POST /api/domain → ws_manager.broadcast chiamato con type='domain_switched'."""
    pepe = _make_pepe_wiki()
    ws = AsyncMock()
    ws.broadcast = AsyncMock()
    prev_pepe, prev_ws = _state.pepe, _state.ws_manager
    _state.pepe = pepe
    _state.ws_manager = ws
    try:
        r = await client.post("/api/domain", json={"domain": "etsy"})
    finally:
        _state.pepe = prev_pepe
        _state.ws_manager = prev_ws
    assert r.status_code == 200
    ws.broadcast.assert_awaited_once()
    payload = ws.broadcast.call_args[0][0]
    assert payload["type"] == "domain_switched"
    assert payload["domain"] == "etsy"


# ===========================================================================
# Section 2 — system.py (13 test)
# ===========================================================================


async def test_health_db_execute_exception_503(client):
    """GET /api/health → 503 quando db.execute() solleva; checks['db'] inizia con 'error:'."""
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=Exception("connection refused"))
    mock_memory = AsyncMock()
    mock_memory.get_db = AsyncMock(return_value=mock_db)
    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/health")
    finally:
        _state.memory = prev
    assert r.status_code == 503
    data = r.json()
    assert "db" in data
    assert data["db"].startswith("error:")


async def test_health_db_ok_200(client):
    """GET /api/health → 200 quando db.execute() ha successo."""
    mock_cursor = AsyncMock()
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_cursor)
    mock_memory = AsyncMock()
    mock_memory.get_db = AsyncMock(return_value=mock_db)
    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/health")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert r.json()["db"] == "ok"


async def test_status_queue_size_from_pepe(client):
    """GET /api/status → queue_size restituisce il valore di pepe._queue.qsize()."""
    mock_pepe = MagicMock()
    mock_pepe._queue = MagicMock()
    mock_pepe._queue.qsize.return_value = 7
    mock_pepe.mock_mode = False
    mock_pepe.get_agent_statuses = MagicMock(return_value={"design": "idle"})
    mock_ws = MagicMock()
    mock_ws._connections = []
    prev_pepe, prev_ws = _state.pepe, _state.ws_manager
    _state.pepe = mock_pepe
    _state.ws_manager = mock_ws
    try:
        r = await client.get("/api/status")
    finally:
        _state.pepe = prev_pepe
        _state.ws_manager = prev_ws
    assert r.status_code == 200
    data = r.json()
    assert data["queue_size"] == 7
    assert data["status"] == "running"


async def test_agents_with_pepe_nonempty(client):
    """GET /api/agents → dict non vuoto con pepe mockato."""
    mock_pepe = MagicMock()
    mock_pepe.get_agent_statuses = MagicMock(
        return_value={"design": "idle", "analytics": "running"}
    )
    prev = _state.pepe
    _state.pepe = mock_pepe
    try:
        r = await client.get("/api/agents")
    finally:
        _state.pepe = prev
    assert r.status_code == 200
    data = r.json()
    assert "agents" in data
    assert data["agents"]["design"] == "idle"
    assert len(data["agents"]) == 2


async def test_listings_memory_none_returns_empty(client):
    """GET /api/listings con memory=None → {"listings": []} (branch memory guard)."""
    prev = _state.memory
    _state.memory = None
    try:
        r = await client.get("/api/listings")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert r.json() == {"listings": []}


async def test_scheduler_with_memory_data(client):
    """GET /api/scheduler → risposta con tasks e jobs entrambi popolati."""
    mock_memory = AsyncMock()
    mock_memory.get_scheduled_tasks = AsyncMock(return_value=[{"id": 1, "name": "cleanup"}])
    mock_scheduler = MagicMock()
    mock_scheduler.get_jobs = MagicMock(return_value=[{"id": "job-1", "name": "analytics"}])
    prev_mem, prev_sch = _state.memory, _state.scheduler
    _state.memory = mock_memory
    _state.scheduler = mock_scheduler
    try:
        r = await client.get("/api/scheduler")
    finally:
        _state.memory = prev_mem
        _state.scheduler = prev_sch
    assert r.status_code == 200
    data = r.json()
    assert len(data["tasks"]) == 1
    assert len(data["jobs"]) == 1


async def test_production_queue_with_status_filter(client):
    """GET /api/production-queue?status=approved → status='approved' passato a memory."""
    mock_memory = AsyncMock()
    mock_memory.get_production_queue = AsyncMock(return_value=[])
    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/production-queue", params={"status": "approved"})
    finally:
        _state.memory = prev
    assert r.status_code == 200
    mock_memory.get_production_queue.assert_called_once_with(status="approved", limit=50)


async def test_production_queue_all_filter_passes_none(client):
    """GET /api/production-queue?status=all → filter_status=None passato a memory."""
    mock_memory = AsyncMock()
    mock_memory.get_production_queue = AsyncMock(return_value=[])
    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/production-queue", params={"status": "all"})
    finally:
        _state.memory = prev
    assert r.status_code == 200
    mock_memory.get_production_queue.assert_called_once_with(status=None, limit=50)


async def test_tasks_pending_input_exception_500(client):
    """GET /api/tasks/pending-input → 500 se memory.get_pending_input_tasks() solleva."""
    mock_memory = AsyncMock()
    mock_memory.get_pending_input_tasks = AsyncMock(side_effect=RuntimeError("crash"))
    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/tasks/pending-input")
    finally:
        _state.memory = prev
    assert r.status_code == 500


async def test_tasks_pending_input_success(client):
    """GET /api/tasks/pending-input → 200 con lista task."""
    mock_memory = AsyncMock()
    mock_memory.get_pending_input_tasks = AsyncMock(
        return_value=[{"id": "t1", "agent": "design"}]
    )
    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/tasks/pending-input")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert len(r.json()["tasks"]) == 1


async def test_agent_steps_recent_with_agent_name(client):
    """GET /api/agents/steps/recent?agent_name=design → filtro agent_name passato a memory."""
    mock_memory = AsyncMock()
    mock_memory.get_recent_agent_steps = AsyncMock(return_value=[{"agent": "design"}])
    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/agents/steps/recent", params={"agent_name": "design"})
    finally:
        _state.memory = prev
    assert r.status_code == 200
    mock_memory.get_recent_agent_steps.assert_called_once_with(50, agent_name="design")


async def test_costs_days_14_with_memory(client):
    """GET /api/costs?days=14 → 200 con days=14 e budget_threshold_eur nel breakdown."""
    mock_memory = AsyncMock()
    mock_memory.get_cost_breakdown = AsyncMock(
        return_value={"total_usd": 5.0, "by_model": {}}
    )
    prev = _state.memory
    _state.memory = mock_memory
    try:
        r = await client.get("/api/costs", params={"days": 14})
    finally:
        _state.memory = prev
    assert r.status_code == 200
    data = r.json()
    assert data["days"] == 14
    assert "breakdown" in data
    assert "budget_threshold_eur" in data["breakdown"]
    mock_memory.get_cost_breakdown.assert_called_once_with(period_days=14)


async def test_costs_no_memory_empty(client):
    """GET /api/costs → 200 con breakdown={} quando memory=None."""
    prev = _state.memory
    _state.memory = None
    try:
        r = await client.get("/api/costs")
    finally:
        _state.memory = prev
    assert r.status_code == 200
    assert r.json()["breakdown"] == {}


# ===========================================================================
# Section 3 — autopilot.py unit + exception paths (9 test)
# ===========================================================================


def test_map_autopilot_paused_manual_returns_paused():
    """_map_autopilot_status('paused_manual') → 'paused'."""
    from apps.backend.api.routers.autopilot import _map_autopilot_status
    assert _map_autopilot_status("paused_manual") == "paused"


def test_map_autopilot_idle_returns_stopped():
    """_map_autopilot_status('idle') → 'stopped'."""
    from apps.backend.api.routers.autopilot import _map_autopilot_status
    assert _map_autopilot_status("idle") == "stopped"


def test_map_autopilot_paused_prefix_returns_paused():
    """_map_autopilot_status('paused_anything') → 'paused' (startswith check)."""
    from apps.backend.api.routers.autopilot import _map_autopilot_status
    assert _map_autopilot_status("paused_anything") == "paused"


def test_map_autopilot_running_returns_running():
    """_map_autopilot_status('running') → 'running'."""
    from apps.backend.api.routers.autopilot import _map_autopilot_status
    assert _map_autopilot_status("running") == "running"


def test_map_autopilot_empty_returns_stopped():
    """_map_autopilot_status('') → 'stopped'."""
    from apps.backend.api.routers.autopilot import _map_autopilot_status
    assert _map_autopilot_status("") == "stopped"


async def test_autopilot_status_items_today_from_db(client):
    """GET /api/autopilot/status → items_today ricavato dalla query DB."""
    mock_loop = AsyncMock()
    mock_loop._get_status = AsyncMock(return_value="running")
    mock_loop._state_get = AsyncMock(side_effect=["svg-planner", ""])
    mock_row = {"cnt": 3}
    mock_cursor = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value=mock_row)
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_cursor)
    mock_memory = AsyncMock()
    mock_memory.get_db = AsyncMock(return_value=mock_db)
    prev_loop, prev_mem = _state.autopilot_loop, _state.memory
    _state.autopilot_loop = mock_loop
    _state.memory = mock_memory
    try:
        r = await client.get("/api/autopilot/status")
    finally:
        _state.autopilot_loop = prev_loop
        _state.memory = prev_mem
    assert r.status_code == 200
    data = r.json()
    assert data["items_today"] == 3
    assert data["status"] == "running"


async def test_autopilot_start_resume_exception_500(client):
    """POST /api/autopilot/start → 500 se loop.resume() solleva Exception."""
    mock_loop = AsyncMock()
    mock_loop.resume = AsyncMock(side_effect=RuntimeError("crash"))
    prev = _state.autopilot_loop
    _state.autopilot_loop = mock_loop
    try:
        r = await client.post("/api/autopilot/start")
    finally:
        _state.autopilot_loop = prev
    assert r.status_code == 500


async def test_autopilot_pause_stop_exception_500(client):
    """POST /api/autopilot/pause → 500 se loop.stop() solleva Exception."""
    mock_loop = AsyncMock()
    mock_loop.stop = AsyncMock(side_effect=RuntimeError("crash"))
    prev = _state.autopilot_loop
    _state.autopilot_loop = mock_loop
    try:
        r = await client.post("/api/autopilot/pause")
    finally:
        _state.autopilot_loop = prev
    assert r.status_code == 500


async def test_autopilot_stop_final_exception_500(client):
    """POST /api/autopilot/stop → 500 se loop.stop(final=True) solleva Exception."""
    mock_loop = AsyncMock()
    mock_loop.stop = AsyncMock(side_effect=RuntimeError("crash"))
    prev = _state.autopilot_loop
    _state.autopilot_loop = mock_loop
    try:
        r = await client.post("/api/autopilot/stop")
    finally:
        _state.autopilot_loop = prev
    assert r.status_code == 500


# ===========================================================================
# Section 4 — personal.py (8 test)
# ===========================================================================


async def test_mcp_status_notion_ok(client):
    """GET /api/personal/mcp/status → notion='ok' con token e resp.status=200."""
    mock_cs = _mock_aiohttp_cs(status=200)
    with (
        patch("apps.backend.api.routers.personal.settings") as mock_settings,
        patch("apps.backend.api.routers.personal.aiohttp.ClientSession", mock_cs),
    ):
        mock_settings.NOTION_API_TOKEN = "fake-token"
        mock_settings.GOOGLE_REFRESH_TOKEN = ""
        r = await client.get("/api/personal/mcp/status")
    assert r.status_code == 200
    assert r.json()["notion"] == "ok"


async def test_mcp_status_notion_error_401(client):
    """GET /api/personal/mcp/status → notion='error_401' quando resp.status=401."""
    mock_cs = _mock_aiohttp_cs(status=401)
    with (
        patch("apps.backend.api.routers.personal.settings") as mock_settings,
        patch("apps.backend.api.routers.personal.aiohttp.ClientSession", mock_cs),
    ):
        mock_settings.NOTION_API_TOKEN = "fake-token"
        mock_settings.GOOGLE_REFRESH_TOKEN = ""
        r = await client.get("/api/personal/mcp/status")
    assert r.status_code == 200
    assert r.json()["notion"] == "error_401"


async def test_mcp_status_notion_exception_error(client):
    """GET /api/personal/mcp/status → notion='error' quando aiohttp solleva Exception."""
    mock_cs = _mock_aiohttp_cs(raise_exc=Exception("timeout"))
    with (
        patch("apps.backend.api.routers.personal.settings") as mock_settings,
        patch("apps.backend.api.routers.personal.aiohttp.ClientSession", mock_cs),
    ):
        mock_settings.NOTION_API_TOKEN = "fake-token"
        mock_settings.GOOGLE_REFRESH_TOKEN = ""
        r = await client.get("/api/personal/mcp/status")
    assert r.status_code == 200
    assert r.json()["notion"] == "error"


async def test_mcp_status_google_token_configured(client):
    """GET /api/personal/mcp/status → gmail='configured' con GOOGLE_REFRESH_TOKEN presente."""
    with patch("apps.backend.api.routers.personal.settings") as mock_settings:
        mock_settings.NOTION_API_TOKEN = ""
        mock_settings.GOOGLE_REFRESH_TOKEN = "refresh-token"
        r = await client.get("/api/personal/mcp/status")
    assert r.status_code == 200
    data = r.json()
    assert data["gmail"] == "configured"
    assert data["calendar"] == "configured"
    assert data["notion"] == "not_configured"


async def test_mcp_status_both_tokens_present(client):
    """GET /api/personal/mcp/status → notion='ok' e gmail='configured' con entrambi i token."""
    mock_cs = _mock_aiohttp_cs(status=200)
    with (
        patch("apps.backend.api.routers.personal.settings") as mock_settings,
        patch("apps.backend.api.routers.personal.aiohttp.ClientSession", mock_cs),
    ):
        mock_settings.NOTION_API_TOKEN = "fake-token"
        mock_settings.GOOGLE_REFRESH_TOKEN = "refresh-token"
        r = await client.get("/api/personal/mcp/status")
    assert r.status_code == 200
    data = r.json()
    assert data["notion"] == "ok"
    assert data["gmail"] == "configured"
    assert data["calendar"] == "configured"


async def test_mcp_status_notion_error_other_status(client):
    """GET /api/personal/mcp/status → notion='error_500' con resp.status=500."""
    mock_cs = _mock_aiohttp_cs(status=500)
    with (
        patch("apps.backend.api.routers.personal.settings") as mock_settings,
        patch("apps.backend.api.routers.personal.aiohttp.ClientSession", mock_cs),
    ):
        mock_settings.NOTION_API_TOKEN = "fake-token"
        mock_settings.GOOGLE_REFRESH_TOKEN = ""
        r = await client.get("/api/personal/mcp/status")
    assert r.status_code == 200
    assert r.json()["notion"] == "error_500"


async def test_personal_ask_pepe_none_503(client):
    """POST /api/personal/ask con pepe=None → 503."""
    prev = _state.pepe
    _state.pepe = None
    try:
        r = await client.post("/api/personal/ask", json={"text": "ciao"})
    finally:
        _state.pepe = prev
    assert r.status_code == 503
    assert "error" in r.json()


async def test_personal_ask_args_forwarded_correctly(client):
    """POST /api/personal/ask → handle_user_message chiamato con source e session_id corretti."""
    mock_pepe = AsyncMock()
    mock_pepe.handle_user_message = AsyncMock(return_value="risposta")
    prev = _state.pepe
    _state.pepe = mock_pepe
    try:
        r = await client.post("/api/personal/ask", json={"text": "ciao"})
    finally:
        _state.pepe = prev
    assert r.status_code == 200
    mock_pepe.handle_user_message.assert_awaited_once_with(
        "ciao",
        source="dashboard_voice",
        session_id="dashboard",
    )


# ===========================================================================
# Section 5 — telegram/handlers/autopilot.py (12 test)
# ===========================================================================

import apps.backend.telegram.handlers.autopilot as _tg_mod
from apps.backend.telegram.handlers.autopilot import (
    cb_unknown,
    handle_approval_callback,
    handle_bundle_callback,
)


def _make_callback_query_tg(data: str, cb_id: str = "cb-r6b-default", user_id: int = 100):
    query = AsyncMock()
    query.id = cb_id
    query.data = data
    query.from_user = MagicMock()
    query.from_user.id = user_id
    query.message = AsyncMock()
    return query


def _make_update_tg(callback_query=None):
    update = MagicMock()
    update.callback_query = callback_query
    return update


def _make_deps_tg(loop=None, memory=None):
    deps = MagicMock()
    deps.autopilot_loop = loop
    deps.memory = memory
    return deps


def _make_context_tg():
    ctx = MagicMock()
    ctx.args = []
    return ctx


async def test_approval_callback_query_none_early_return():
    """handle_approval_callback con query=None → ritorno anticipato senza errori."""
    update = _make_update_tg(callback_query=None)
    deps = _make_deps_tg(loop=AsyncMock())
    await handle_approval_callback(deps, update, _make_context_tg())
    # Passa se non solleva


async def test_approval_loop_none_calls_edit_markup(monkeypatch):
    """handle_approval_callback con loop=None → edit_message_reply_markup(reply_markup=None)."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    _tg_mod._processed_approvals.clear()
    deps = _make_deps_tg(loop=None)
    query = _make_callback_query_tg("approve:1", cb_id="cb-r6b-loop-none")
    update = _make_update_tg(callback_query=query)
    await handle_approval_callback(deps, update, _make_context_tg())
    query.edit_message_reply_markup.assert_called_once_with(reply_markup=None)


async def test_approval_approve_edit_exception_no_crash(monkeypatch):
    """handle_approval_callback action='approve' con edit che solleva → no crash."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    _tg_mod._processed_approvals.clear()
    loop = AsyncMock()
    deps = _make_deps_tg(loop=loop)
    query = _make_callback_query_tg("approve:2", cb_id="cb-r6b-approve-exc")
    query.edit_message_reply_markup = AsyncMock(side_effect=Exception("API error"))
    update = _make_update_tg(callback_query=query)
    await handle_approval_callback(deps, update, _make_context_tg())
    loop.register_approval.assert_called_once_with(2, "approved")


async def test_approval_skip_edit_exception_no_crash(monkeypatch):
    """handle_approval_callback action='skip' con edit che solleva → no crash."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    _tg_mod._processed_approvals.clear()
    loop = AsyncMock()
    deps = _make_deps_tg(loop=loop)
    query = _make_callback_query_tg("skip:3", cb_id="cb-r6b-skip-exc")
    query.edit_message_reply_markup = AsyncMock(side_effect=Exception("API error"))
    update = _make_update_tg(callback_query=query)
    await handle_approval_callback(deps, update, _make_context_tg())
    loop.register_approval.assert_called_once_with(3, "skipped_user")


async def test_bundle_store_insight_exception_continues(monkeypatch):
    """handle_bundle_callback → store_insight() solleva → nessun crash, edit comunque chiamato."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    memory = AsyncMock()
    memory.store_insight = AsyncMock(side_effect=RuntimeError("chroma crash"))
    deps = _make_deps_tg(memory=memory)
    query = _make_callback_query_tg("bundle_approve:aabbcc001122")
    update = _make_update_tg(callback_query=query)
    await handle_bundle_callback(deps, update, _make_context_tg())
    query.edit_message_reply_markup.assert_called_once_with(reply_markup=None)


async def test_bundle_edit_exception_no_crash(monkeypatch):
    """handle_bundle_callback → edit_message_reply_markup() solleva → nessun crash."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    memory = AsyncMock()
    deps = _make_deps_tg(memory=memory)
    query = _make_callback_query_tg("bundle_approve:aabbcc002233")
    query.edit_message_reply_markup = AsyncMock(side_effect=Exception("telegram error"))
    update = _make_update_tg(callback_query=query)
    await handle_bundle_callback(deps, update, _make_context_tg())
    # Nessun assert esplicito — il test passa se non solleva


async def test_bundle_decline_stores_declined(monkeypatch):
    """handle_bundle_callback con bundle_decline → memory.store_insight con status='declined'."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    memory = AsyncMock()
    deps = _make_deps_tg(memory=memory)
    query = _make_callback_query_tg("bundle_decline:aabbcc003344")
    update = _make_update_tg(callback_query=query)
    await handle_bundle_callback(deps, update, _make_context_tg())
    memory.store_insight.assert_called_once()
    call_kwargs = memory.store_insight.call_args[1]
    assert call_kwargs["metadata"]["status"] == "declined"


async def test_bundle_no_memory_no_store_insight(monkeypatch):
    """handle_bundle_callback con memory=None → store_insight non chiamato."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: True,
    )
    deps = _make_deps_tg(memory=None)
    query = _make_callback_query_tg("bundle_approve:aabbcc004455")
    update = _make_update_tg(callback_query=query)
    await handle_bundle_callback(deps, update, _make_context_tg())
    query.edit_message_reply_markup.assert_called_once_with(reply_markup=None)


async def test_bundle_unauthorized_no_store_insight(monkeypatch):
    """handle_bundle_callback con utente non autorizzato → store_insight non chiamato."""
    monkeypatch.setattr(
        "apps.backend.telegram.handlers.autopilot.is_authorized",
        lambda uid: False,
    )
    memory = AsyncMock()
    deps = _make_deps_tg(memory=memory)
    query = _make_callback_query_tg("bundle_approve:aabbcc005566", user_id=999)
    update = _make_update_tg(callback_query=query)
    await handle_bundle_callback(deps, update, _make_context_tg())
    query.answer.assert_called_once_with("Non autorizzato.")
    memory.store_insight.assert_not_called()


async def test_bundle_query_none_early_return(monkeypatch):
    """handle_bundle_callback con query=None → ritorno anticipato senza errori."""
    update = _make_update_tg(callback_query=None)
    deps = _make_deps_tg(memory=AsyncMock())
    await handle_bundle_callback(deps, update, _make_context_tg())
    # Passa se non solleva


async def test_cb_unknown_answers_not_recognised():
    """cb_unknown con query valida → query.answer('Azione non riconosciuta') chiamato."""
    query = AsyncMock()
    query.data = "something:unknown"
    update = _make_update_tg(callback_query=query)
    await cb_unknown(update, _make_context_tg())
    query.answer.assert_called_once_with("Azione non riconosciuta")


async def test_cb_unknown_query_none_early_return():
    """cb_unknown con query=None → ritorno anticipato senza errori."""
    update = _make_update_tg(callback_query=None)
    await cb_unknown(update, _make_context_tg())
    # Passa se non solleva
