"""tests/core/test_finance_routes_r5.py

~46 pytest-asyncio tests covering:
  - finance_tracker.py      → record_sale, _is_first_sale_in_niche (righe 133-190)
  - api/routers/memory_routes.py → /api/memory/graph, /api/memory/node (righe scoperte)
  - core/scheduler.py / _CoreMixin → job condizionali + get_jobs error path
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import apps.backend.api.state as _state
from apps.backend.api.routers import memory_routes
from apps.backend.core.finance_tracker import FinanceTracker

# ============================================================================
# Shared DB schema (identica a test_finance_tracker.py)
# ============================================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS revenue_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    etsy_listing_id TEXT    NOT NULL,
    order_id        TEXT    UNIQUE,
    niche           TEXT,
    product_type    TEXT,
    gross_eur       REAL    NOT NULL,
    etsy_fee_eur    REAL    NOT NULL,
    net_eur         REAL    NOT NULL,
    design_cost_eur REAL    DEFAULT 0.0,
    listing_fee_eur REAL    DEFAULT 0.18,
    sold_at         REAL    NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS production_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    status          TEXT    NOT NULL DEFAULT 'planned',
    published_at    REAL,
    llm_cost_usd    REAL    DEFAULT 0.0,
    image_cost_usd  REAL    DEFAULT 0.0,
    listing_fee_usd REAL    DEFAULT 0.20
);

CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at REAL
);
"""


# ============================================================================
# Section 1 — FinanceTracker.record_sale (righe 133-190)
# ============================================================================


@pytest.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_SCHEMA)
        await conn.commit()
        yield conn


@pytest.fixture
async def tracker(db):
    memory = MagicMock()
    memory.get_db = AsyncMock(return_value=db)
    return FinanceTracker(memory=memory)


@pytest.fixture
async def tracker_with_telegram(db):
    memory = MagicMock()
    memory.get_db = AsyncMock(return_value=db)
    telegram_mock = AsyncMock()
    ft = FinanceTracker(memory=memory, telegram_broadcaster=telegram_mock)
    ft._telegram_mock = telegram_mock
    return ft


async def test_record_sale_inserts_row_in_db(tracker, db):
    """record_sale() inserisce una riga in revenue_events."""
    await asyncio.wait_for(
        tracker.record_sale("listing_abc", "order_001", 12.0),
        timeout=5,
    )
    cursor = await db.execute("SELECT COUNT(*) AS n FROM revenue_events")
    row = await cursor.fetchone()
    assert row["n"] == 1


async def test_record_sale_returns_net_data_keys(tracker):
    """record_sale() ritorna un dict con le chiavi attese dal calcolo netto."""
    result = await asyncio.wait_for(
        tracker.record_sale("listing_abc", "order_002", 9.99),
        timeout=5,
    )
    for key in ("gross_eur", "net_eur", "margin_pct", "transaction_fee"):
        assert key in result, f"Chiave '{key}' mancante nel risultato di record_sale"


async def test_record_sale_is_first_sale_adds_listing_fee(tracker, db):
    """is_first_sale=True → listing_fee_eur > 0 nel record inserito."""
    await asyncio.wait_for(
        tracker.record_sale("listing_fee", "order_003", 10.0, is_first_sale=True),
        timeout=5,
    )
    cursor = await db.execute(
        "SELECT listing_fee_eur FROM revenue_events WHERE order_id='order_003'"
    )
    row = await cursor.fetchone()
    assert float(row["listing_fee_eur"]) > 0.0


async def test_record_sale_not_first_sale_no_listing_fee(tracker, db):
    """is_first_sale=False → listing_fee_eur = 0 nel record inserito."""
    await asyncio.wait_for(
        tracker.record_sale("listing_fee2", "order_004", 10.0, is_first_sale=False),
        timeout=5,
    )
    cursor = await db.execute(
        "SELECT listing_fee_eur FROM revenue_events WHERE order_id='order_004'"
    )
    row = await cursor.fetchone()
    assert float(row["listing_fee_eur"]) == 0.0


async def test_record_sale_idempotent_duplicate_order_id(tracker, db):
    """order_id UNIQUE previene doppio inserimento (INSERT OR IGNORE)."""
    await asyncio.wait_for(
        tracker.record_sale("listing_dup", "order_dup", 8.0),
        timeout=5,
    )
    await asyncio.wait_for(
        tracker.record_sale("listing_dup", "order_dup", 8.0),
        timeout=5,
    )
    cursor = await db.execute(
        "SELECT COUNT(*) AS n FROM revenue_events WHERE order_id='order_dup'"
    )
    row = await cursor.fetchone()
    assert row["n"] == 1


async def test_record_sale_stores_niche_and_product_type(tracker, db):
    """record_sale() persiste niche e product_type nel DB."""
    await asyncio.wait_for(
        tracker.record_sale(
            "listing_niche", "order_005", 7.0,
            niche="planner", product_type="printable_pdf",
        ),
        timeout=5,
    )
    cursor = await db.execute(
        "SELECT niche, product_type FROM revenue_events WHERE order_id='order_005'"
    )
    row = await cursor.fetchone()
    assert row["niche"] == "planner"
    assert row["product_type"] == "printable_pdf"


async def test_record_sale_first_niche_calls_telegram_broadcast(tracker_with_telegram):
    """Prima vendita in una niche → _telegram_broadcast viene chiamato."""
    await asyncio.wait_for(
        tracker_with_telegram.record_sale(
            "listing_tg", "order_006", 11.0, niche="wedding"
        ),
        timeout=5,
    )
    tracker_with_telegram._telegram_mock.assert_awaited_once()


async def test_record_sale_second_sale_same_niche_no_telegram(tracker_with_telegram):
    """Seconda vendita nella stessa niche → _telegram_broadcast non chiamato di nuovo."""
    await asyncio.wait_for(
        tracker_with_telegram.record_sale(
            "listing_s2a", "order_007", 11.0, niche="budget"
        ),
        timeout=5,
    )
    tracker_with_telegram._telegram_mock.reset_mock()
    await asyncio.wait_for(
        tracker_with_telegram.record_sale(
            "listing_s2b", "order_008", 11.0, niche="budget"
        ),
        timeout=5,
    )
    tracker_with_telegram._telegram_mock.assert_not_awaited()


async def test_record_sale_no_telegram_broadcaster_no_exception(tracker):
    """_telegram_broadcast=None → nessuna eccezione."""
    assert tracker._telegram_broadcast is None
    await asyncio.wait_for(
        tracker.record_sale("listing_notg", "order_009", 5.0, niche="svg"),
        timeout=5,
    )


async def test_record_sale_empty_niche_skips_telegram(tracker_with_telegram):
    """Niche vuota → nessuna chiamata a _telegram_broadcast."""
    await asyncio.wait_for(
        tracker_with_telegram.record_sale(
            "listing_en", "order_010", 6.0, niche=""
        ),
        timeout=5,
    )
    tracker_with_telegram._telegram_mock.assert_not_awaited()


async def test_record_sale_telegram_exception_does_not_propagate(db):
    """Eccezione in _telegram_broadcast viene ingoiata (best-effort)."""
    memory = MagicMock()
    memory.get_db = AsyncMock(return_value=db)
    failing_telegram = AsyncMock(side_effect=RuntimeError("telegram down"))
    ft = FinanceTracker(memory=memory, telegram_broadcaster=failing_telegram)
    result = await asyncio.wait_for(
        ft.record_sale("listing_ex", "order_011", 8.0, niche="planner"),
        timeout=5,
    )
    assert "net_eur" in result


async def test_is_first_sale_in_niche_no_prior_sales(tracker):
    """_is_first_sale_in_niche() → True quando non ci sono vendite precedenti."""
    result = await asyncio.wait_for(
        tracker._is_first_sale_in_niche("brand_new_niche"),
        timeout=5,
    )
    assert result is True


async def test_is_first_sale_in_niche_with_prior_sale(tracker, db):
    """_is_first_sale_in_niche() → False quando esiste una vendita precedente."""
    now = time.time()
    await db.execute(
        "INSERT INTO revenue_events"
        " (etsy_listing_id, order_id, niche, gross_eur, etsy_fee_eur, net_eur, sold_at)"
        " VALUES (?,?,?,?,?,?,?)",
        ("l_prior", "prior_order", "existing_niche", 9.0, 1.0, 8.0, now),
    )
    await db.commit()
    result = await asyncio.wait_for(
        tracker._is_first_sale_in_niche("existing_niche", exclude_order="other_order"),
        timeout=5,
    )
    assert result is False


async def test_is_first_sale_in_niche_excludes_current_order(tracker, db):
    """exclude_order esclude il record appena inserito → ritorna True."""
    now = time.time()
    await db.execute(
        "INSERT INTO revenue_events"
        " (etsy_listing_id, order_id, niche, gross_eur, etsy_fee_eur, net_eur, sold_at)"
        " VALUES (?,?,?,?,?,?,?)",
        ("l_excl", "excl_order", "excl_niche", 5.0, 0.5, 4.5, now),
    )
    await db.commit()
    result = await asyncio.wait_for(
        tracker._is_first_sale_in_niche("excl_niche", exclude_order="excl_order"),
        timeout=5,
    )
    assert result is True


async def test_goal_progress_with_sales_pct_above_zero(tracker, db):
    """goal_progress() con vendite nel mese corrente → pct > 0."""
    now = time.time()
    await db.execute(
        "INSERT INTO revenue_events"
        " (etsy_listing_id, gross_eur, etsy_fee_eur, net_eur, sold_at)"
        " VALUES (?,?,?,?,?)",
        ("l_goal", 50.0, 5.0, 45.0, now),
    )
    await db.commit()
    result = await asyncio.wait_for(
        tracker.goal_progress(goal_eur=100.0),
        timeout=5,
    )
    assert result["pct"] > 0.0
    assert result["current_net_eur"] > 0.0


async def test_top_earners_multiple_sorted_by_net_desc(tracker, db):
    """top_earners() con più vendite → lista ordinata per net_eur decrescente."""
    now = time.time()
    await db.executemany(
        "INSERT INTO revenue_events"
        " (etsy_listing_id, niche, gross_eur, etsy_fee_eur, net_eur, sold_at)"
        " VALUES (?,?,?,?,?,?)",
        [
            ("l_cheap",     "planner", 5.0,  0.5, 4.5,  now),
            ("l_expensive", "svg",     20.0, 2.0, 18.0, now),
            ("l_mid",       "tracker", 10.0, 1.0, 9.0,  now),
        ],
    )
    await db.commit()
    result = await asyncio.wait_for(
        tracker.top_earners(limit=5, days=7),
        timeout=5,
    )
    assert result[0]["listing_id"] == "l_expensive"
    assert result[1]["net_eur"] >= result[2]["net_eur"]


# ============================================================================
# Section 2 — memory_routes.py (righe scoperte)
# ============================================================================


@pytest.fixture(scope="module")
def mem_app():
    _app = FastAPI()
    _app.include_router(memory_routes.router)
    _app.dependency_overrides[_state.verify_personal_key] = lambda: None
    yield _app
    _app.dependency_overrides.clear()


@pytest.fixture
async def mem_client(mem_app):
    async with AsyncClient(
        transport=ASGITransport(app=mem_app),
        base_url="http://test",
    ) as ac:
        yield ac


def _make_chroma_col(ids=None, documents=None, metadatas=None, embeddings=None):
    """Crea un mock ChromaDB collection con dati prestabiliti."""
    col = MagicMock()
    col.get.return_value = {
        "ids":        ids        or [],
        "documents":  documents  or [],
        "metadatas":  metadatas  or [],
        "embeddings": embeddings or [],
    }
    return col


async def test_memory_graph_no_memory_returns_503(mem_client):
    """GET /api/memory/graph → 503 se state.memory=None."""
    prev = _state.memory
    _state.memory = None
    try:
        r = await mem_client.get("/api/memory/graph")
    finally:
        _state.memory = prev
    assert r.status_code == 503
    assert "error" in r.json()


async def test_memory_graph_with_chroma_collection_returns_nodes(mem_client):
    """GET /api/memory/graph con collection mock → nodi nella risposta."""
    col = _make_chroma_col(
        ids=["id1", "id2"],
        documents=["doc1", "doc2"],
        metadatas=[{"source": "etsy"}, {"title": "budget planner"}],
        embeddings=[[0.1, 0.2], [0.3, 0.4]],
    )
    mock_mem = MagicMock()
    mock_mem._chroma_collection = col
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/graph")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    data = r.json()
    assert "nodes" in data and "edges" in data
    assert len(data["nodes"]) == 2


async def test_memory_graph_node_fields_present(mem_client):
    """_add_nodes() popola i campi id, label, collection, zone, document, metadata."""
    col = _make_chroma_col(
        ids=["nodeA"],
        documents=["testo nodo A"],
        metadatas=[{"title": "Node Title"}],
        embeddings=[None],
    )
    mock_mem = MagicMock()
    mock_mem._chroma_collection = col
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/graph")
    finally:
        _state.memory = prev

    node = r.json()["nodes"][0]
    for field in ("id", "label", "collection", "zone", "document", "metadata"):
        assert field in node, f"Campo '{field}' mancante nel nodo"


async def test_memory_graph_label_uses_metadata_title(mem_client):
    """_add_nodes() usa meta['title'] come label se presente."""
    col = _make_chroma_col(
        ids=["n1"],
        documents=["doc"],
        metadatas=[{"title": "My Title"}],
        embeddings=[None],
    )
    mock_mem = MagicMock()
    mock_mem._chroma_collection = col
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/graph")
    finally:
        _state.memory = prev
    assert r.json()["nodes"][0]["label"] == "My Title"


async def test_memory_graph_screen_memory_code_app_zone_personal(mem_client):
    """screen_memory con app='VSCode' → zone='personal'."""
    col_screen = _make_chroma_col(
        ids=["sc1"],
        documents=["screen doc"],
        metadatas=[{"app": "VSCode"}],
        embeddings=[None],
    )
    mock_mem = MagicMock()
    mock_mem._chroma_collection = None
    mock_mem._screen_memory_collection = col_screen
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/graph")
    finally:
        _state.memory = prev

    node = r.json()["nodes"][0]
    assert node["zone"] == "personal"
    assert node["collection"] == "screen_memory"


async def test_memory_graph_screen_memory_non_code_app_zone_memory(mem_client):
    """screen_memory con app='Finder' (non-code) → zone='memory'."""
    col_screen = _make_chroma_col(
        ids=["sc2"],
        documents=["screen doc"],
        metadatas=[{"app": "Finder"}],
        embeddings=[None],
    )
    mock_mem = MagicMock()
    mock_mem._chroma_collection = None
    mock_mem._screen_memory_collection = col_screen
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/graph")
    finally:
        _state.memory = prev
    assert r.json()["nodes"][0]["zone"] == "memory"


async def test_memory_graph_edges_created_for_similar_embeddings(mem_client):
    """Due nodi con embeddings quasi identici → almeno un edge con threshold=0.5."""
    e1 = [1.0, 0.0, 0.0]
    e2 = [1.0, 0.01, 0.0]
    col = _make_chroma_col(
        ids=["e1", "e2"],
        documents=["d1", "d2"],
        metadatas=[{}, {}],
        embeddings=[e1, e2],
    )
    mock_mem = MagicMock()
    mock_mem._chroma_collection = col
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/graph?threshold=0.5")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    assert len(r.json()["edges"]) >= 1


async def test_memory_graph_no_embeddings_produces_no_edges(mem_client):
    """Nodi senza embeddings validi → nessun edge."""
    col = _make_chroma_col(
        ids=["n1", "n2"],
        documents=["d1", "d2"],
        metadatas=[{}, {}],
        embeddings=[None, None],
    )
    mock_mem = MagicMock()
    mock_mem._chroma_collection = col
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/graph")
    finally:
        _state.memory = prev
    assert r.json()["edges"] == []


async def test_memory_graph_meta_has_collection_counts(mem_client):
    """GET /api/memory/graph → meta contiene contatori per collection."""
    col = _make_chroma_col(ids=["x"], documents=["y"], metadatas=[{}], embeddings=[None])
    mock_mem = MagicMock()
    mock_mem._chroma_collection = col
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/graph")
    finally:
        _state.memory = prev

    meta = r.json()["meta"]
    assert meta["etsy_count"] == 1
    assert meta["screen_count"] == 0
    assert "total_nodes" in meta


async def test_memory_graph_fetch_collection_exception_continues(mem_client):
    """Eccezione in collection.get() → _fetch_collection ritorna [] e continua."""
    col_bad = MagicMock()
    col_bad.get.side_effect = RuntimeError("chroma error")
    mock_mem = MagicMock()
    mock_mem._chroma_collection = col_bad
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/graph")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    assert r.json()["nodes"] == []


async def test_memory_graph_connection_count_populated_on_nodes(mem_client):
    """Nodi con edge → campo 'connections' > 0 sui nodi coinvolti."""
    e1 = [1.0, 0.0]
    e2 = [0.99, 0.14]
    col = _make_chroma_col(
        ids=["c1", "c2"],
        documents=["d1", "d2"],
        metadatas=[{}, {}],
        embeddings=[e1, e2],
    )
    mock_mem = MagicMock()
    mock_mem._chroma_collection = col
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/graph?threshold=0.5")
    finally:
        _state.memory = prev

    total_connections = sum(n["connections"] for n in r.json()["nodes"])
    assert total_connections > 0


async def test_memory_graph_all_four_collections_merged(mem_client):
    """Nodi da quattro collection diverse vengono uniti in nodes[]."""
    mock_mem = MagicMock()
    mock_mem._chroma_collection          = _make_chroma_col(ids=["etsy_1"],     documents=["d"], metadatas=[{}], embeddings=[None])
    mock_mem._screen_memory_collection   = _make_chroma_col(ids=["screen_1"],   documents=["d"], metadatas=[{"app": "Finder"}], embeddings=[None])
    mock_mem._personal_memory_collection = _make_chroma_col(ids=["personal_1"], documents=["d"], metadatas=[{}], embeddings=[None])
    mock_mem._shared_memory_collection   = _make_chroma_col(ids=["shared_1"],   documents=["d"], metadatas=[{}], embeddings=[None])

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/graph")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    assert len(r.json()["nodes"]) == 4


async def test_memory_node_no_memory_returns_503(mem_client):
    """GET /api/memory/node/{id} → 503 se state.memory=None."""
    prev = _state.memory
    _state.memory = None
    try:
        r = await mem_client.get("/api/memory/node/some_doc")
    finally:
        _state.memory = prev
    assert r.status_code == 503


async def test_memory_node_found_returns_200(mem_client):
    """GET /api/memory/node/{id} con doc presente → 200 con campi attesi."""
    col = MagicMock()
    col.get.return_value = {
        "ids":       ["doc_123"],
        "documents": ["contenuto documento"],
        "metadatas": [{"source": "etsy"}],
    }
    mock_mem = MagicMock()
    mock_mem._chroma_collection = col
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None
    mock_mem.get_node_access_history = AsyncMock(return_value=[])

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/node/doc_123?collection=pepe_memory")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "doc_123"
    assert data["document"] == "contenuto documento"
    assert data["collection"] == "pepe_memory"


async def test_memory_node_not_found_returns_404(mem_client):
    """GET /api/memory/node/{id} con doc assente → 404."""
    col = MagicMock()
    col.get.return_value = {"ids": [], "documents": [], "metadatas": []}
    mock_mem = MagicMock()
    mock_mem._chroma_collection = col
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/node/missing_doc?collection=pepe_memory")
    finally:
        _state.memory = prev

    assert r.status_code == 404
    assert "error" in r.json()


async def test_memory_node_collection_attribute_none_returns_503(mem_client):
    """Collection attribute è None → 503 (non disponibile)."""
    mock_mem = MagicMock()
    mock_mem._chroma_collection = None
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/node/some_doc?collection=pepe_memory")
    finally:
        _state.memory = prev
    assert r.status_code == 503


async def test_memory_node_get_exception_returns_500(mem_client):
    """Eccezione in collection.get() → 500."""
    col = MagicMock()
    col.get.side_effect = RuntimeError("db crash")
    mock_mem = MagicMock()
    mock_mem._chroma_collection = col
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/node/bad_doc?collection=pepe_memory")
    finally:
        _state.memory = prev
    assert r.status_code == 500


async def test_memory_node_access_history_included_in_response(mem_client):
    """GET /api/memory/node → access_history è presente nella risposta."""
    col = MagicMock()
    col.get.return_value = {
        "ids":       ["hist_node"],
        "documents": ["doc"],
        "metadatas": [{}],
    }
    history = [{"agent": "research", "query_text": "mandala", "queried_at": 1234567.0}]
    mock_mem = MagicMock()
    mock_mem._chroma_collection = col
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = None
    mock_mem.get_node_access_history = AsyncMock(return_value=history)

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/node/hist_node?collection=pepe_memory")
    finally:
        _state.memory = prev
    assert r.json()["access_history"] == history


async def test_memory_node_shared_memory_collection_works(mem_client):
    """GET /api/memory/node?collection=shared_memory → 200."""
    col = MagicMock()
    col.get.return_value = {
        "ids":       ["shared_1"],
        "documents": ["shared doc"],
        "metadatas": [{"type": "insight"}],
    }
    mock_mem = MagicMock()
    mock_mem._chroma_collection = None
    mock_mem._screen_memory_collection = None
    mock_mem._personal_memory_collection = None
    mock_mem._shared_memory_collection = col
    mock_mem.get_node_access_history = AsyncMock(return_value=[])

    prev = _state.memory
    _state.memory = mock_mem
    try:
        r = await mem_client.get("/api/memory/node/shared_1?collection=shared_memory")
    finally:
        _state.memory = prev

    assert r.status_code == 200
    assert r.json()["collection"] == "shared_memory"


# ============================================================================
# Section 3 — Scheduler._CoreMixin: job condizionali + get_jobs error path
# ============================================================================


def _make_sched(**overrides):
    """Factory: Scheduler con tutti i collaboratori mockati e _scheduler=MagicMock."""
    from apps.backend.core.scheduler import Scheduler  # noqa: PLC0415

    sched = Scheduler.__new__(Scheduler)
    sched.memory                = AsyncMock()
    sched._ws_broadcast         = None
    sched._telegram_broadcast   = None
    sched.pepe                  = None
    sched.storage               = None
    sched.research_agent        = None
    sched.design_agent          = None
    sched.publisher_agent       = None
    sched.analytics_agent       = None
    sched.finance_agent         = None
    sched.screen_watcher        = None
    sched.production_queue      = None
    sched.budget_manager        = None
    sched.publication_policy    = None
    sched.autopilot_loop        = None
    sched.etsy_client           = None
    sched.shop_optimizer        = None
    sched.etsy_ads_manager      = None
    sched.learning_loop         = None
    sched.pinterest_agent       = None
    sched._scheduler            = MagicMock()
    sched._scheduler.add_job    = MagicMock()
    sched._job_status           = {}
    sched._job_status_lock      = threading.Lock()
    sched._internal_jobs        = {"ssd_health_check", "agent_status_sync"}
    sched._notify_telegram      = AsyncMock()
    sched._broadcast            = AsyncMock()

    for k, v in overrides.items():
        setattr(sched, k, v)
    return sched


def _registered_ids(sched) -> list[str]:
    """Estrae gli 'id' da tutti i call args di add_job."""
    return [c.kwargs.get("id") for c in sched._scheduler.add_job.call_args_list]


def test_register_analytics_poll_when_analytics_agent_set():
    """analytics_agent presente → job 'analytics_poll' registrato."""
    sched = _make_sched(analytics_agent=MagicMock())
    sched._register_builtin_jobs()
    assert "analytics_poll" in _registered_ids(sched)


def test_register_analytics_poll_skipped_when_none():
    """analytics_agent=None → job 'analytics_poll' NON registrato."""
    sched = _make_sched(analytics_agent=None)
    sched._register_builtin_jobs()
    assert "analytics_poll" not in _registered_ids(sched)


def test_register_etsy_ads_manager_when_set():
    """etsy_ads_manager presente → job 'etsy_ads_manager' registrato."""
    sched = _make_sched(etsy_ads_manager=MagicMock())
    sched._register_builtin_jobs()
    assert "etsy_ads_manager" in _registered_ids(sched)


def test_register_etsy_ads_manager_skipped_when_none():
    """etsy_ads_manager=None → job 'etsy_ads_manager' NON registrato."""
    sched = _make_sched(etsy_ads_manager=None)
    sched._register_builtin_jobs()
    assert "etsy_ads_manager" not in _registered_ids(sched)


def test_register_screen_cleanup_when_watcher_set():
    """screen_watcher presente → job 'screen_cleanup' registrato."""
    sched = _make_sched(screen_watcher=MagicMock())
    sched._register_builtin_jobs()
    assert "screen_cleanup" in _registered_ids(sched)


def test_register_screen_cleanup_skipped_when_none():
    """screen_watcher=None → job 'screen_cleanup' NON registrato."""
    sched = _make_sched(screen_watcher=None)
    sched._register_builtin_jobs()
    assert "screen_cleanup" not in _registered_ids(sched)


def test_register_shop_optimizer_when_set():
    """shop_optimizer presente → job 'shop_optimizer' registrato."""
    sched = _make_sched(shop_optimizer=MagicMock())
    sched._register_builtin_jobs()
    assert "shop_optimizer" in _registered_ids(sched)


def test_register_shop_optimizer_skipped_when_none():
    """shop_optimizer=None → job 'shop_optimizer' NON registrato."""
    sched = _make_sched(shop_optimizer=None)
    sched._register_builtin_jobs()
    assert "shop_optimizer" not in _registered_ids(sched)


def test_get_jobs_returns_empty_list_when_scheduler_raises():
    """get_jobs() → [] se _scheduler.get_jobs() solleva eccezione (righe 385-386)."""
    sched = _make_sched()
    sched._scheduler.get_jobs.side_effect = RuntimeError("scheduler down")
    result = sched.get_jobs()
    assert result == []


def test_get_jobs_filters_out_internal_jobs():
    """get_jobs() non restituisce job in _internal_jobs (riga 390-391)."""
    sched = _make_sched()
    internal_job = MagicMock()
    internal_job.id = "ssd_health_check"
    internal_job.name = "Health check SSD"
    internal_job.next_run_time = None
    sched._scheduler.get_jobs.return_value = [internal_job]
    result = sched.get_jobs()
    assert result == []


def test_get_jobs_returns_non_internal_jobs_with_correct_shape():
    """get_jobs() include job non-interni con struttura {id, name, status, ...} (righe 392-400)."""
    sched = _make_sched()
    custom_job = MagicMock()
    custom_job.id = "my_custom_job"
    custom_job.name = "Custom Job"
    custom_job.next_run_time = None
    custom_job.trigger = MagicMock(__str__=lambda self: "interval[1h]")
    sched._scheduler.get_jobs.return_value = [custom_job]
    result = sched.get_jobs()
    assert len(result) == 1
    assert result[0]["id"] == "my_custom_job"
    assert result[0]["name"] == "Custom Job"
    assert "status" in result[0]
    assert "next_run" in result[0]
