"""B-01: verifica che le tabelle pinterest_queue e pinterest_boards esistano dopo init."""
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


# ---------------------------------------------------------------------------
# pinterest_queue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pinterest_queue_table_exists(tmp_path):
    """La tabella pinterest_queue esiste dopo init."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pinterest_queue'"
        )
        row = await cursor.fetchone()

    assert row is not None, "pinterest_queue table missing after init"


@pytest.mark.asyncio
async def test_pinterest_queue_columns(tmp_path):
    """pinterest_queue ha tutte le colonne previste dallo schema."""
    expected = {
        "id", "production_queue_id", "pin_variant", "image_path",
        "title", "description", "board_id", "scheduled_at", "published_at",
        "pinterest_pin_id", "status", "delivery_method",
        "cost_image_gen", "cost_llm", "created_at",
    }
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        cursor = await db.execute("PRAGMA table_info(pinterest_queue)")
        cols = {row[1] for row in await cursor.fetchall()}

    missing = expected - cols
    assert not missing, f"Colonne mancanti in pinterest_queue: {missing}"


@pytest.mark.asyncio
async def test_pinterest_queue_defaults(tmp_path):
    """Inserimento minimo in pinterest_queue → DEFAULT status='pending', delivery_method='direct'."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        await db.execute(
            """
            INSERT INTO pinterest_queue
                (pin_variant, image_path, title, description, board_id, scheduled_at)
            VALUES (1, '/img/test.png', 'Test title', 'Test desc', 'board123',
                    '2026-05-10 12:00:00')
            """
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT status, delivery_method, cost_image_gen, cost_llm FROM pinterest_queue LIMIT 1"
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == "pending",  f"status DEFAULT atteso 'pending', ottenuto {row[0]!r}"
    assert row[1] == "direct",   f"delivery_method DEFAULT atteso 'direct', ottenuto {row[1]!r}"
    assert row[2] == 0.0,        f"cost_image_gen DEFAULT atteso 0.0, ottenuto {row[2]!r}"
    assert row[3] == 0.0,        f"cost_llm DEFAULT atteso 0.0, ottenuto {row[3]!r}"


# ---------------------------------------------------------------------------
# pinterest_boards
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pinterest_boards_table_exists(tmp_path):
    """La tabella pinterest_boards esiste dopo init."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pinterest_boards'"
        )
        row = await cursor.fetchone()

    assert row is not None, "pinterest_boards table missing after init"


@pytest.mark.asyncio
async def test_pinterest_boards_columns(tmp_path):
    """pinterest_boards ha tutte le colonne previste dallo schema."""
    expected = {
        "board_id", "board_name", "section_key", "board_type",
        "created_at", "pin_count", "is_active",
    }
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        cursor = await db.execute("PRAGMA table_info(pinterest_boards)")
        cols = {row[1] for row in await cursor.fetchall()}

    missing = expected - cols
    assert not missing, f"Colonne mancanti in pinterest_boards: {missing}"


@pytest.mark.asyncio
async def test_pinterest_boards_defaults(tmp_path):
    """Inserimento minimo in pinterest_boards → DEFAULT board_type='section', is_active=1, pin_count=0."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        await db.execute(
            """
            INSERT INTO pinterest_boards (board_id, board_name, section_key)
            VALUES ('board_001', 'Party Printables', 'party')
            """
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT board_type, is_active, pin_count FROM pinterest_boards WHERE board_id='board_001'"
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == "section", f"board_type DEFAULT atteso 'section', ottenuto {row[0]!r}"
    assert row[1] == 1,         f"is_active DEFAULT atteso 1, ottenuto {row[1]!r}"
    assert row[2] == 0,         f"pin_count DEFAULT atteso 0, ottenuto {row[2]!r}"


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pinterest_queue_indexes_exist(tmp_path):
    """Gli indici su pinterest_queue esistono dopo init."""
    mm = _make_memory_base(tmp_path)
    await mm.init()

    async with aiosqlite.connect(mm._db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='pinterest_queue'"
        )
        indexes = {row[0] for row in await cursor.fetchall()}

    assert "idx_pq_status_scheduled" in indexes, f"idx_pq_status_scheduled mancante. Trovati: {indexes}"
    assert "idx_pq_board_id" in indexes, f"idx_pq_board_id mancante. Trovati: {indexes}"
