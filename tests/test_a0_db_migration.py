"""A.0: verifica che la colonna product_tier esista dopo la migrazione."""
from __future__ import annotations
import pytest
import aiosqlite


@pytest.mark.asyncio
async def test_production_queue_has_product_tier(tmp_path):
    """product_tier colonna presente con DEFAULT 'core' dopo migrazione."""
    from apps.backend.core.memory import MemoryManager

    db_path = str(tmp_path / "test.db")
    mm = MemoryManager.__new__(MemoryManager)
    mm._db_path = db_path
    mm._chromadb_path = str(tmp_path / "chromadb")
    mm._db = None
    mm._chroma_collection = None
    mm._screen_memory_collection = None
    mm._personal_memory_collection = None
    mm._shared_memory_collection = None
    mm._ws_broadcaster = None
    mm._bridge_callback = None
    mm.mock_mode = False
    await mm.init()

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(production_queue)")
        cols = {row[1] for row in await cursor.fetchall()}

    assert "product_tier" in cols, "product_tier column missing after migration"


@pytest.mark.asyncio
async def test_product_tier_defaults_to_core(tmp_path):
    """Inserimento senza product_tier → DEFAULT 'core'."""
    from apps.backend.core.memory import MemoryManager
    import uuid

    db_path = str(tmp_path / "test.db")
    mm = MemoryManager.__new__(MemoryManager)
    mm._db_path = db_path
    mm._chromadb_path = str(tmp_path / "chromadb")
    mm._db = None
    mm._chroma_collection = None
    mm._screen_memory_collection = None
    mm._personal_memory_collection = None
    mm._shared_memory_collection = None
    mm._ws_broadcaster = None
    mm._bridge_callback = None
    mm.mock_mode = False
    await mm.init()

    async with aiosqlite.connect(db_path) as db:
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
