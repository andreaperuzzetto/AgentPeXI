"""A.0: verifica che la colonna product_tier esista dopo la migrazione."""
from __future__ import annotations
import pytest
import aiosqlite


def _make_memory_base(tmp_path):
    """Instantiate MemoryBase directly (bypasses MemoryManager monkey-patches in test suite)."""
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
    return mm


@pytest.mark.asyncio
async def test_production_queue_has_product_tier(tmp_path):
    """product_tier colonna presente con DEFAULT 'core' dopo migrazione."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        cursor = await db.execute("PRAGMA table_info(production_queue)")
        cols = {row[1] for row in await cursor.fetchall()}

    assert "product_tier" in cols, "product_tier column missing after migration"


@pytest.mark.asyncio
async def test_product_tier_defaults_to_core(tmp_path):
    """Inserimento senza product_tier → DEFAULT 'core'."""
    import uuid
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        task_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO production_queue (task_id, product_type, niche, brief, status) "
            "VALUES (?, 'printable_pdf', 'test niche', '{}', 'pending_design')",
            (task_id,),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT product_tier FROM production_queue WHERE task_id = ?", (task_id,)
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == "core", f"Expected DEFAULT 'core', got {row[0]!r}"
