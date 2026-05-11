"""tests/e2e/conftest.py — shared helpers for BLOCCO C E2E / A.3 gate tests.

All tests call _make_memory_base(tmp_path) directly (mirrors test_c2 pattern).
No async fixtures — avoids event-loop scoping issues with pytest-asyncio.
"""
from __future__ import annotations

import pytest

from apps.backend.core._memory._base import MemoryBase
from apps.backend.core.memory import MemoryManager


# ---------------------------------------------------------------------------
# ChromaDB isolation — prevent disk writes in CI
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _use_ephemeral_chromadb(monkeypatch):
    """Replace chromadb.PersistentClient with EphemeralClient in all e2e tests.

    Prevents test pollution across runs and eliminates disk writes in CI.
    """
    try:
        import chromadb
        monkeypatch.setattr(
            chromadb,
            "PersistentClient",
            lambda path=None, **kw: chromadb.EphemeralClient(),
        )
    except ImportError:
        pass


def _make_memory_base(tmp_path, mock_mode: bool = False):
    """Instantiate MemoryBase with tmp-dir DB (identical to test_c2 pattern)."""
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
    mm.mock_mode = mock_mode
    return mm


def _make_memory_manager(tmp_path, mock_mode: bool = False):
    """Instantiate MemoryManager with tmp-dir DB (includes all mixins like AgentLogsMixin)."""
    mm = MemoryManager.__new__(MemoryManager)
    mm._db_path = str(tmp_path / "test.db")
    mm._chromadb_path = str(tmp_path / "chromadb")
    mm._db = None
    mm._chroma_collection = None
    mm._screen_memory_collection = None
    mm._personal_memory_collection = None
    mm._shared_memory_collection = None
    mm._ws_broadcaster = None
    mm._bridge_callback = None
    mm.mock_mode = mock_mode
    return mm
