"""C.4 — Cross-referencing Automatico: TDD test suite (written before implementation).

All tests must FAIL (RED) before C.4 is implemented.

Coverage:
  1  : < 2 listing published → nessun PATCH, nessuna notifica Telegram
  2  : ≥ 2 listing published → PATCH chiamato su entrambi
  3  : cross-ref block format corretto (separatori, → arrows, etsy.com URL)
  4  : cross-ref blocco sostituito (non appeso) se già presente in descrizione
  5  : mock mode → skip silenzioso, nessun errore
  6  : Etsy PATCH failure → log error, non raise (publish non viene annullato)
  7  : max 5 cross-ref per listing (cluster con 7 listing → solo 5 link)
  8  : set_etsy_listing_url → colonna aggiornata in DB
  9  : GET /api/etsy/clusters → lista cluster con counts
  10 : GET /api/etsy/clusters/{id} → dettaglio con items
  11 : GET /api/etsy/clusters/nonexistent → 404
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

# ---------------------------------------------------------------------------
# Imports under test — RED until files are created
# ---------------------------------------------------------------------------
from apps.backend.agents._publisher._crossref_mixin import _CrossrefMixin
from apps.backend.api.routers.etsy import get_clusters, get_cluster_detail
from apps.backend.core.production_queue import ProductionQueueService, ProductionQueueItem


# ---------------------------------------------------------------------------
# Shared schema (same as test_production_queue.py)
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS production_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL DEFAULT (hex(randomblob(8))),
    product_type TEXT NOT NULL DEFAULT 'printable_pdf',
    niche TEXT NOT NULL DEFAULT '',
    brief TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending_design',
    keywords TEXT,
    entry_score REAL DEFAULT 0.0,
    design_prompt TEXT,
    image_url TEXT,
    thumbnail_path TEXT,
    listing_title TEXT,
    listing_description TEXT,
    listing_tags TEXT,
    listing_price REAL,
    approval_sent_at REAL,
    approval_message_id INTEGER,
    approval_chat_id INTEGER,
    skip_reason TEXT,
    skip_count_user INTEGER DEFAULT 0,
    skip_count_timeout INTEGER DEFAULT 0,
    error_message TEXT,
    scheduled_publish_at REAL,
    published_at REAL,
    etsy_listing_id TEXT,
    llm_cost_usd REAL DEFAULT 0.0,
    image_cost_usd REAL DEFAULT 0.0,
    listing_fee_usd REAL DEFAULT 0.20,
    ads_activated INTEGER DEFAULT 0,
    ads_paused INTEGER DEFAULT 0,
    loop_run_id TEXT,
    ab_price_variant TEXT,
    file_paths TEXT,
    product_tier TEXT DEFAULT 'core',
    cluster_id TEXT,
    release_order INTEGER NOT NULL DEFAULT 0,
    etsy_listing_url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pq_item(
    item_id: int,
    etsy_listing_id: str | None,
    status: str = "completed",
    listing_title: str = "Test Listing",
    listing_description: str = "A nice description.",
    cluster_id: str = "abc123",
    etsy_listing_url: str | None = None,
) -> ProductionQueueItem:
    """Build a minimal ProductionQueueItem for tests."""
    item = MagicMock(spec=ProductionQueueItem)
    item.id = item_id
    item.etsy_listing_id = etsy_listing_id
    item.status = status
    item.listing_title = listing_title
    item.listing_description = listing_description
    item.cluster_id = cluster_id
    item.etsy_listing_url = etsy_listing_url
    item.niche = "adhd planner"
    item.product_tier = "core"
    item.release_order = 1
    return item


class FakeCrossref(_CrossrefMixin):
    """Minimal stand-in for PublisherAgent with only _CrossrefMixin needed attrs."""

    def __init__(
        self,
        mock_mode: bool = False,
        etsy_api: MagicMock | None = None,
        telegram: AsyncMock | None = None,
    ) -> None:
        self.memory = MagicMock()
        self.memory.mock_mode = mock_mode
        self.memory.get_db = AsyncMock(return_value=MagicMock())
        self.etsy_api = etsy_api or MagicMock()
        self.etsy_api.patch_listing_description = AsyncMock()
        self._telegram_broadcast = telegram

    async def _notify_telegram(self, message: str) -> None:
        if self._telegram_broadcast:
            await self._telegram_broadcast(message)


# ---------------------------------------------------------------------------
# DB fixture (in-memory)
# ---------------------------------------------------------------------------

@pytest.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_SCHEMA)
        await conn.commit()
        yield conn


@pytest.fixture
async def queue(db):
    return ProductionQueueService(db)


# ---------------------------------------------------------------------------
# Test 1: < 2 listing published → no PATCH, no Telegram
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crossref_skip_when_fewer_than_two_published():
    """Gate: only 1 completed listing → no PATCH, no Telegram notification."""
    one_item = [_make_pq_item(1, etsy_listing_id="111")]
    telegram = AsyncMock()
    agent = FakeCrossref(telegram=telegram)

    with patch(
        "apps.backend.agents._publisher._crossref_mixin.ProductionQueueService"
    ) as MockPQ:
        mock_pq = AsyncMock()
        mock_pq.get_cluster_items = AsyncMock(return_value=one_item)
        MockPQ.return_value = mock_pq

        await agent._update_cluster_crossrefs(
            cluster_id="abc123",
            new_listing_id="111",
            new_listing_title="My Listing",
            new_listing_url="etsy.com/listing/111",
        )

    agent.etsy_api.patch_listing_description.assert_not_called()
    telegram.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: ≥ 2 published → PATCH called on both listing descriptions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crossref_patches_both_listings_when_two_published():
    """With 2 completed listings PATCH must be called once per listing."""
    items = [
        _make_pq_item(1, etsy_listing_id="111", listing_title="Listing A"),
        _make_pq_item(2, etsy_listing_id="222", listing_title="Listing B"),
    ]
    agent = FakeCrossref()

    with patch(
        "apps.backend.agents._publisher._crossref_mixin.ProductionQueueService"
    ) as MockPQ:
        mock_pq = AsyncMock()
        mock_pq.get_cluster_items = AsyncMock(return_value=items)
        mock_pq.set_etsy_listing_url = AsyncMock()
        MockPQ.return_value = mock_pq

        await agent._update_cluster_crossrefs(
            cluster_id="abc123",
            new_listing_id="111",
            new_listing_title="Listing A",
            new_listing_url="etsy.com/listing/111",
        )

    assert agent.etsy_api.patch_listing_description.call_count == 2


# ---------------------------------------------------------------------------
# Test 3: cross-ref block format — separators, arrows, etsy.com URLs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crossref_block_format_contains_separator_and_arrows():
    """Verify the injected text uses ─ separators and → arrows with etsy.com URLs."""
    items = [
        _make_pq_item(1, etsy_listing_id="111", listing_title="Listing A", listing_description="Desc A"),
        _make_pq_item(2, etsy_listing_id="222", listing_title="Listing B", listing_description="Desc B"),
    ]
    agent = FakeCrossref()
    captured_descriptions: list[str] = []

    async def capture_patch(listing_id: str, description: str) -> None:
        captured_descriptions.append(description)

    agent.etsy_api.patch_listing_description = capture_patch

    with patch(
        "apps.backend.agents._publisher._crossref_mixin.ProductionQueueService"
    ) as MockPQ:
        mock_pq = AsyncMock()
        mock_pq.get_cluster_items = AsyncMock(return_value=items)
        mock_pq.set_etsy_listing_url = AsyncMock()
        MockPQ.return_value = mock_pq

        await agent._update_cluster_crossrefs(
            cluster_id="abc123",
            new_listing_id="111",
            new_listing_title="Listing A",
            new_listing_url="etsy.com/listing/111",
        )

    assert len(captured_descriptions) == 2
    for desc in captured_descriptions:
        # Must contain separator
        assert "─" * 25 in desc, "separator missing"
        # Must contain at least one → arrow
        assert "→" in desc, "arrow missing"
        # Must contain etsy.com/listing/ URL
        assert "etsy.com/listing/" in desc, "URL missing"


# ---------------------------------------------------------------------------
# Test 4: cross-ref block replaced (not appended) if already present
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crossref_block_replaced_not_appended():
    """If a cross-ref block already exists, it must be replaced — not duplicated."""
    sep = "─" * 25
    old_crossref = f"\n{sep}\nYou might also like from our shop:\n→ Old Item — etsy.com/listing/999\n{sep}"
    base_desc = "Original description."
    items = [
        _make_pq_item(
            1, etsy_listing_id="111",
            listing_description=base_desc + old_crossref,
        ),
        _make_pq_item(2, etsy_listing_id="222", listing_description="Desc B"),
    ]
    agent = FakeCrossref()
    captured: list[str] = []

    async def capture_patch(listing_id: str, description: str) -> None:
        captured.append((listing_id, description))

    agent.etsy_api.patch_listing_description = capture_patch

    with patch(
        "apps.backend.agents._publisher._crossref_mixin.ProductionQueueService"
    ) as MockPQ:
        mock_pq = AsyncMock()
        mock_pq.get_cluster_items = AsyncMock(return_value=items)
        mock_pq.set_etsy_listing_url = AsyncMock()
        MockPQ.return_value = mock_pq

        await agent._update_cluster_crossrefs(
            cluster_id="abc123",
            new_listing_id="222",
            new_listing_title="Listing B",
            new_listing_url="etsy.com/listing/222",
        )

    # Find the description for listing 111 (the one that had old crossref)
    desc_111 = next(desc for lid, desc in captured if lid == "111")
    # Old item (999) must NOT appear → it was replaced
    assert "listing/999" not in desc_111
    # New cross-ref must appear only once (not doubled)
    assert desc_111.count(sep) == 2  # exactly one crossref block (2 separators)


# ---------------------------------------------------------------------------
# Test 5: mock mode → silent skip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crossref_skips_in_mock_mode():
    """When memory.mock_mode=True, cross-ref must return early with no side effects."""
    agent = FakeCrossref(mock_mode=True)

    with patch(
        "apps.backend.agents._publisher._crossref_mixin.ProductionQueueService"
    ) as MockPQ:
        await agent._update_cluster_crossrefs(
            cluster_id="abc123",
            new_listing_id="111",
            new_listing_title="Listing A",
            new_listing_url="etsy.com/listing/111",
        )
        MockPQ.assert_not_called()

    agent.etsy_api.patch_listing_description.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6: Etsy PATCH failure → log error, no raise
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crossref_etsy_patch_failure_does_not_raise():
    """If PATCH fails for a listing, error is logged but no exception propagates."""
    items = [
        _make_pq_item(1, etsy_listing_id="111"),
        _make_pq_item(2, etsy_listing_id="222"),
    ]
    agent = FakeCrossref()
    agent.etsy_api.patch_listing_description = AsyncMock(
        side_effect=RuntimeError("Etsy API 500")
    )

    with patch(
        "apps.backend.agents._publisher._crossref_mixin.ProductionQueueService"
    ) as MockPQ:
        mock_pq = AsyncMock()
        mock_pq.get_cluster_items = AsyncMock(return_value=items)
        mock_pq.set_etsy_listing_url = AsyncMock()
        MockPQ.return_value = mock_pq

        # Must NOT raise — failure is swallowed and logged
        await agent._update_cluster_crossrefs(
            cluster_id="abc123",
            new_listing_id="111",
            new_listing_title="Listing A",
            new_listing_url="etsy.com/listing/111",
        )


# ---------------------------------------------------------------------------
# Test 7: max 5 cross-ref per listing (cluster of 7 → only 5 links)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crossref_max_five_links_per_listing():
    """Cluster of 7 published listings: each listing must reference at most 5 others."""
    items = [
        _make_pq_item(i, etsy_listing_id=str(100 + i), listing_title=f"Listing {i}")
        for i in range(7)
    ]
    agent = FakeCrossref()
    captured: list[str] = []

    async def capture_patch(listing_id: str, description: str) -> None:
        captured.append(description)

    agent.etsy_api.patch_listing_description = capture_patch

    with patch(
        "apps.backend.agents._publisher._crossref_mixin.ProductionQueueService"
    ) as MockPQ:
        mock_pq = AsyncMock()
        mock_pq.get_cluster_items = AsyncMock(return_value=items)
        mock_pq.set_etsy_listing_url = AsyncMock()
        MockPQ.return_value = mock_pq

        await agent._update_cluster_crossrefs(
            cluster_id="abc123",
            new_listing_id="100",
            new_listing_title="Listing 0",
            new_listing_url="etsy.com/listing/100",
        )

    # Each captured description must have ≤ 5 "→" arrows
    for desc in captured:
        arrow_count = desc.count("→")
        assert arrow_count <= 5, f"Too many cross-ref arrows: {arrow_count}"


# ---------------------------------------------------------------------------
# Test 8: set_etsy_listing_url updates the DB column
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_etsy_listing_url_updates_db(queue, db):
    """set_etsy_listing_url must write etsy_listing_url to the DB row."""
    # Insert a row
    await db.execute(
        "INSERT INTO production_queue (niche, cluster_id, etsy_listing_id) VALUES (?, ?, ?)",
        ("adhd planner", "abc123", "999"),
    )
    await db.commit()
    cur = await db.execute("SELECT id FROM production_queue WHERE etsy_listing_id='999'")
    row = await cur.fetchone()
    item_id = row["id"]

    # Should raise AttributeError before implementation
    await queue.set_etsy_listing_url(item_id=item_id, url="etsy.com/listing/999")

    cur = await db.execute(
        "SELECT etsy_listing_url FROM production_queue WHERE id=?", (item_id,)
    )
    row = await cur.fetchone()
    assert row["etsy_listing_url"] == "etsy.com/listing/999"


# ---------------------------------------------------------------------------
# Test 9: GET /api/etsy/clusters → list with counts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_clusters_returns_cluster_list():
    """GET /clusters must return {"clusters": [...]} with total/completed counts."""
    mock_memory = MagicMock()
    mock_db = AsyncMock()
    mock_memory.get_db = AsyncMock(return_value=mock_db)

    fake_rows = [
        {"cluster_id": "abc123", "total": 3, "completed": 2, "niche": "adhd planner"},
        {"cluster_id": "def456", "total": 2, "completed": 0, "niche": "bullet journal"},
    ]

    with patch(
        "apps.backend.api.routers.etsy.ProductionQueueService"
    ) as MockPQ:
        mock_pq = MagicMock()
        mock_pq._fetchall = AsyncMock(return_value=fake_rows)
        MockPQ.return_value = mock_pq

        result = await get_clusters(memory=mock_memory)

    assert "clusters" in result
    assert len(result["clusters"]) == 2
    assert result["clusters"][0]["cluster_id"] == "abc123"


# ---------------------------------------------------------------------------
# Test 10: GET /api/etsy/clusters/{id} → detail with items
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_cluster_detail_returns_items():
    """GET /clusters/{id} must return {"cluster_id": ..., "items": [...]}."""
    mock_memory = MagicMock()
    mock_db = AsyncMock()
    mock_memory.get_db = AsyncMock(return_value=mock_db)

    fake_items = [
        _make_pq_item(1, etsy_listing_id="111", listing_title="Listing A"),
        _make_pq_item(2, etsy_listing_id="222", listing_title="Listing B"),
    ]

    with patch(
        "apps.backend.api.routers.etsy.ProductionQueueService"
    ) as MockPQ:
        mock_pq = MagicMock()
        mock_pq.get_cluster_items = AsyncMock(return_value=fake_items)
        MockPQ.return_value = mock_pq

        result = await get_cluster_detail(cluster_id="abc123", memory=mock_memory)

    assert result["cluster_id"] == "abc123"
    assert len(result["items"]) == 2
    assert result["items"][0]["listing_title"] == "Listing A"


# ---------------------------------------------------------------------------
# Test 11: GET /api/etsy/clusters/nonexistent → 404
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_cluster_detail_404_when_not_found():
    """GET /clusters/{id} with unknown cluster_id must raise HTTPException 404."""
    from fastapi import HTTPException

    mock_memory = MagicMock()
    mock_db = AsyncMock()
    mock_memory.get_db = AsyncMock(return_value=mock_db)

    with patch(
        "apps.backend.api.routers.etsy.ProductionQueueService"
    ) as MockPQ:
        mock_pq = MagicMock()
        mock_pq.get_cluster_items = AsyncMock(return_value=[])
        MockPQ.return_value = mock_pq

        with pytest.raises(HTTPException) as exc_info:
            await get_cluster_detail(cluster_id="nonexistent", memory=mock_memory)

    assert exc_info.value.status_code == 404
