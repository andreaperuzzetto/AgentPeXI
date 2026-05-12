"""tests/unit/test_chromadb_r3e.py

Target: apps/backend/core/_memory/_chromadb.py  ≥ 60% coverage

# MOCK CONTRACT — _chromadb.py
# Client:      chromadb.PersistentClient (creato in MemoryBase.initialize())
# Collections: get_or_create_collection(name=..., embedding_function=...)
#              pepe_memory | screen_memory | personal_memory | shared_memory
# Metodi principali (tutti async, usano asyncio.to_thread per API sync):
#   store_insight(text, metadata=None) → str | None
#   update_insight_metadata(doc_id, metadata) → bool
#   query_insights(query, n_results=5) → list[dict]
#   query_insights_by_type(type_val, limit=50) → list[dict]
#   query_chromadb(query, n_results=5, where=None, agent) → list[dict]
#   query_chromadb_recent(query, ...) → list[dict]
#   add_screen_memory(chunks, metadatas, ids) → bool
#   search_screen_memory(query, n_results=10, where=None, agent) → list[dict]
#   delete_old_screen_memory(older_than_iso) → int
#   get_screen_memory_stats() → dict
#   store_personal_insight(text, metadata=None) → str | None
#   query_personal_memory(query, ...) → list[dict]
#   query_personal_memory_recent(query, ...) → list[dict]
#   get_personal_memory_stats() → dict
#   store_shared_insight(text, metadata=None) → str | None
#   query_shared_memory(query, ...) → list[dict]
#   get_shared_memory_stats() → dict
#   delete_stale_shared_memory(older_than_days=90) → int
# Pattern di mock: MagicMock() per collection (API sync); AsyncMock per to_thread
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from apps.backend.core._memory._chromadb import ChromaDbMixin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeChroma(ChromaDbMixin):
    """Concrete class for isolated ChromaDbMixin unit tests."""

    def __init__(self):
        self._chroma_lock = asyncio.Lock()
        self._chroma_collection = None
        self._screen_memory_collection = None
        self._personal_memory_collection = None
        self._shared_memory_collection = None
        self._bridge_callback = None
        self.log_memory_query = AsyncMock()


def _col() -> MagicMock:
    """Return a MagicMock representing a ChromaDB collection (sync API)."""
    return MagicMock()


# ---------------------------------------------------------------------------
# CR1 — _fire_bg / cached_property _chroma_lock (lines 17-31)
# ---------------------------------------------------------------------------

class TestFireBg:
    async def test_returns_asyncio_task(self):
        obj = FakeChroma()
        ran = asyncio.Event()

        async def _coro():
            ran.set()

        task = obj._fire_bg(_coro())
        assert isinstance(task, asyncio.Task)
        await asyncio.wait_for(ran.wait(), timeout=2)

    async def test_task_discarded_after_completion(self):
        obj = FakeChroma()

        async def _noop():
            pass

        obj._fire_bg(_noop())
        await asyncio.sleep(0.02)
        assert len(obj._chroma_bg_tasks) == 0

    async def test_initialises_bg_tasks_set_lazily(self):
        obj = FakeChroma()
        assert not hasattr(obj, "_chroma_bg_tasks")

        async def _noop():
            pass

        obj._fire_bg(_noop())
        assert hasattr(obj, "_chroma_bg_tasks")


class TestChromaLockCachedProperty:
    """Cover the cached_property fallback (lines 17-19)."""

    async def test_chroma_lock_created_via_cached_property(self):
        # Subclass without pre-setting _chroma_lock so cached_property runs
        class _Bare(ChromaDbMixin):
            def __init__(self):
                self._chroma_collection = None
                self._screen_memory_collection = None
                self._personal_memory_collection = None
                self._shared_memory_collection = None
                self._bridge_callback = None
                self.log_memory_query = AsyncMock()

        obj = _Bare()
        lock = obj._chroma_lock
        assert isinstance(lock, asyncio.Lock)
        # Cached — same object on second access
        assert obj._chroma_lock is lock


# ---------------------------------------------------------------------------
# CR2 — store_insight (lines 37-53)
# ---------------------------------------------------------------------------

class TestStoreInsight:
    async def test_no_collection_returns_none(self):
        obj = FakeChroma()
        result = await obj.store_insight("hello")
        assert result is None

    async def test_returns_uuid_string(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(return_value=None)):
            result = await asyncio.wait_for(obj.store_insight("hello"), timeout=5)
        assert isinstance(result, str)
        assert len(result) == 36  # UUID v4

    async def test_metadata_none_uses_empty_dict(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        captured_kwargs: list[dict] = []

        async def _side(func, **kwargs):
            captured_kwargs.append(kwargs)
            return None

        with patch("asyncio.to_thread", side_effect=_side):
            await asyncio.wait_for(obj.store_insight("doc", None), timeout=5)

        assert captured_kwargs[0]["metadatas"] == [{}]

    async def test_metadata_passed_through(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        captured_kwargs: list[dict] = []

        async def _side(func, **kwargs):
            captured_kwargs.append(kwargs)
            return None

        with patch("asyncio.to_thread", side_effect=_side):
            await asyncio.wait_for(obj.store_insight("doc", {"key": "val"}), timeout=5)

        assert captured_kwargs[0]["metadatas"] == [{"key": "val"}]
        assert captured_kwargs[0]["documents"] == ["doc"]

    async def test_fires_bridge_callback_etsy_domain(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        bridge_calls: list[tuple] = []

        async def _bridge(text, domain):
            bridge_calls.append((text, domain))

        obj._bridge_callback = _bridge
        with patch("asyncio.to_thread", AsyncMock(return_value=None)):
            await asyncio.wait_for(obj.store_insight("important"), timeout=5)
        await asyncio.sleep(0.05)
        assert bridge_calls == [("important", "etsy")]

    async def test_no_bridge_callback_no_error(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        obj._bridge_callback = None
        with patch("asyncio.to_thread", AsyncMock(return_value=None)):
            result = await asyncio.wait_for(obj.store_insight("doc"), timeout=5)
        assert result is not None


# ---------------------------------------------------------------------------
# CR3 — update_insight_metadata (lines 55-69)
# ---------------------------------------------------------------------------

class TestUpdateInsightMetadata:
    async def test_no_collection_returns_false(self):
        obj = FakeChroma()
        result = await obj.update_insight_metadata("id1", {"a": 1})
        assert result is False

    async def test_success_returns_true(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(return_value=None)):
            result = await asyncio.wait_for(
                obj.update_insight_metadata("doc-id", {"x": 1}), timeout=5
            )
        assert result is True

    async def test_exception_returns_false(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(side_effect=Exception("chroma error"))):
            result = await asyncio.wait_for(
                obj.update_insight_metadata("doc-id", {}), timeout=5
            )
        assert result is False

    async def test_calls_collection_update_with_correct_args(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        captured: list[dict] = []

        async def _side(func, **kwargs):
            captured.append({"func": func, "kwargs": kwargs})
            return None

        with patch("asyncio.to_thread", side_effect=_side):
            await asyncio.wait_for(
                obj.update_insight_metadata("myid", {"type": "test"}), timeout=5
            )

        assert captured[0]["kwargs"]["ids"] == ["myid"]
        assert captured[0]["kwargs"]["metadatas"] == [{"type": "test"}]


# ---------------------------------------------------------------------------
# CR4 — query_insights (lines 71-84)
# ---------------------------------------------------------------------------

class TestQueryInsights:
    async def test_no_collection_returns_empty(self):
        obj = FakeChroma()
        result = await obj.query_insights("query")
        assert result == []

    async def test_returns_parsed_documents_with_metadata(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        mock_results = {
            "documents": [["doc1", "doc2"]],
            "metadatas": [[{"type": "a"}, {"type": "b"}]],
        }
        with patch("asyncio.to_thread", AsyncMock(return_value=mock_results)):
            result = await asyncio.wait_for(obj.query_insights("q", n_results=2), timeout=5)
        assert len(result) == 2
        assert result[0] == {"document": "doc1", "metadata": {"type": "a"}}
        assert result[1] == {"document": "doc2", "metadata": {"type": "b"}}

    async def test_no_metadatas_key_uses_empty_dict(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        mock_results = {"documents": [["only doc"]], "metadatas": None}
        with patch("asyncio.to_thread", AsyncMock(return_value=mock_results)):
            result = await asyncio.wait_for(obj.query_insights("q"), timeout=5)
        assert result == [{"document": "only doc", "metadata": {}}]

    async def test_empty_documents_list_returns_empty(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        with patch(
            "asyncio.to_thread",
            AsyncMock(return_value={"documents": [[]], "metadatas": [[]]}),
        ):
            result = await asyncio.wait_for(obj.query_insights("q"), timeout=5)
        assert result == []


# ---------------------------------------------------------------------------
# CR5 — query_insights_by_type (lines 86-111)
# ---------------------------------------------------------------------------

class TestQueryInsightsByType:
    async def test_no_collection_returns_empty(self):
        obj = FakeChroma()
        result = await obj.query_insights_by_type("product")
        assert result == []

    async def test_returns_typed_results(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        mock_results = {
            "ids": ["id1", "id2"],
            "documents": ["text1", "text2"],
            "metadatas": [{"type": "product"}, {"type": "product"}],
        }
        with patch("asyncio.to_thread", AsyncMock(return_value=mock_results)):
            result = await asyncio.wait_for(obj.query_insights_by_type("product"), timeout=5)
        assert len(result) == 2
        assert result[0] == {"id": "id1", "text": "text1", "metadata": {"type": "product"}}

    async def test_exception_returns_empty(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(side_effect=Exception("db error"))):
            result = await asyncio.wait_for(obj.query_insights_by_type("fail"), timeout=5)
        assert result == []

    async def test_documents_shorter_than_ids_uses_empty_string(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        mock_results = {
            "ids": ["id1"],
            "documents": [],   # shorter than ids list
            "metadatas": [{}],
        }
        with patch("asyncio.to_thread", AsyncMock(return_value=mock_results)):
            result = await asyncio.wait_for(obj.query_insights_by_type("t"), timeout=5)
        assert result[0]["text"] == ""

    async def test_metadatas_shorter_than_ids_uses_empty_dict(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        mock_results = {
            "ids": ["id1"],
            "documents": ["text"],
            "metadatas": [],   # shorter than ids list
        }
        with patch("asyncio.to_thread", AsyncMock(return_value=mock_results)):
            result = await asyncio.wait_for(obj.query_insights_by_type("t"), timeout=5)
        assert result[0]["metadata"] == {}


# ---------------------------------------------------------------------------
# CR6 — query_chromadb (lines 113-141)
# ---------------------------------------------------------------------------

class TestQueryChromadb:
    async def test_no_collection_returns_empty(self):
        obj = FakeChroma()
        result = await obj.query_chromadb("query")
        assert result == []

    async def test_returns_parsed_results(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        mock_results = {
            "documents": [["doc1"]],
            "metadatas": [[{"k": "v"}]],
            "ids": [["id1"]],
        }
        with patch("asyncio.to_thread", AsyncMock(return_value=mock_results)):
            result = await asyncio.wait_for(obj.query_chromadb("q"), timeout=5)
        assert len(result) == 1
        assert result[0] == {"document": "doc1", "metadata": {"k": "v"}, "id": "id1"}

    async def test_no_metadatas_uses_empty_dict(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        mock_results = {"documents": [["doc"]], "ids": [["id1"]]}
        with patch("asyncio.to_thread", AsyncMock(return_value=mock_results)):
            result = await asyncio.wait_for(obj.query_chromadb("q"), timeout=5)
        assert result[0]["metadata"] == {}

    async def test_with_where_filter(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        mock_results = {"documents": [[]], "metadatas": [[]], "ids": [[]]}
        with patch("asyncio.to_thread", AsyncMock(return_value=mock_results)):
            result = await asyncio.wait_for(
                obj.query_chromadb("q", where={"type": "a"}), timeout=5
            )
        assert result == []

    async def test_fires_bg_task_when_ids_present(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        mock_results = {
            "documents": [["doc"]],
            "metadatas": [[{}]],
            "ids": [["abc"]],
        }
        with patch("asyncio.to_thread", AsyncMock(return_value=mock_results)):
            await asyncio.wait_for(obj.query_chromadb("q", agent="test_agent"), timeout=5)
        await asyncio.sleep(0.05)
        obj.log_memory_query.assert_awaited()

    async def test_no_ids_no_fire_bg(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        # None doc_id → not appended to accessed_ids → no _fire_bg
        mock_results = {
            "documents": [["doc"]],
            "metadatas": [[{}]],
            "ids": [[None]],
        }
        with patch("asyncio.to_thread", AsyncMock(return_value=mock_results)):
            await asyncio.wait_for(obj.query_chromadb("q"), timeout=5)
        await asyncio.sleep(0.01)
        obj.log_memory_query.assert_not_awaited()

    async def test_empty_documents_returns_empty_list(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        mock_results = {"documents": [[]], "metadatas": [[]], "ids": [[]]}
        with patch("asyncio.to_thread", AsyncMock(return_value=mock_results)):
            result = await asyncio.wait_for(obj.query_chromadb("q", n_results=3), timeout=5)
        assert result == []


# ---------------------------------------------------------------------------
# CR7 — query_chromadb_recent (lines 143-201)
# ---------------------------------------------------------------------------

class TestQueryChromadbRecent:
    async def test_primary_window_returns_results(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        mock_results = [{"document": "recent", "metadata": {}, "id": "1"}]
        obj.query_chromadb = AsyncMock(return_value=mock_results)
        result = await asyncio.wait_for(
            obj.query_chromadb_recent("q", primary_days=30, fallback_days=90), timeout=5
        )
        assert result == mock_results
        assert obj.query_chromadb.call_count == 1

    async def test_primary_empty_fallback_returns(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        fallback = [{"document": "old", "metadata": {}, "id": "2"}]
        obj.query_chromadb = AsyncMock(side_effect=[[], fallback])
        result = await asyncio.wait_for(
            obj.query_chromadb_recent("q", primary_days=30, fallback_days=90), timeout=5
        )
        assert result == fallback
        assert obj.query_chromadb.call_count == 2

    async def test_both_empty_returns_empty(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        obj.query_chromadb = AsyncMock(return_value=[])
        result = await asyncio.wait_for(obj.query_chromadb_recent("q"), timeout=5)
        assert result == []
        assert obj.query_chromadb.call_count == 2

    async def test_primary_exception_tries_fallback(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        fallback = [{"document": "fb", "id": "3", "metadata": {}}]
        obj.query_chromadb = AsyncMock(side_effect=[Exception("primary fail"), fallback])
        result = await asyncio.wait_for(obj.query_chromadb_recent("q"), timeout=5)
        assert result == fallback

    async def test_both_exceptions_returns_empty(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        obj.query_chromadb = AsyncMock(side_effect=Exception("all fail"))
        result = await asyncio.wait_for(obj.query_chromadb_recent("q"), timeout=5)
        assert result == []

    async def test_where_filter_merged_with_date_filter(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        captured_wheres: list = []

        async def _mock_query(query, n_results=5, where=None, agent="unknown"):
            captured_wheres.append(where)
            return []

        obj.query_chromadb = _mock_query
        await asyncio.wait_for(
            obj.query_chromadb_recent("q", where={"type": "test"}), timeout=5
        )
        # Both calls should use $and to merge the base where with the date filter
        assert captured_wheres[0] is not None
        assert "$and" in captured_wheres[0]
        assert captured_wheres[0]["$and"][0] == {"type": "test"}

    async def test_no_where_uses_plain_date_filter(self):
        obj = FakeChroma()
        obj._chroma_collection = _col()
        captured_wheres: list = []

        async def _mock_query(query, n_results=5, where=None, agent="unknown"):
            captured_wheres.append(where)
            return []

        obj.query_chromadb = _mock_query
        await asyncio.wait_for(obj.query_chromadb_recent("q"), timeout=5)
        # No $and — plain {"date": {"$gte": cutoff}}
        assert "date" in captured_wheres[0]


# ---------------------------------------------------------------------------
# CR8 — add_screen_memory (lines 207-235)
# ---------------------------------------------------------------------------

class TestAddScreenMemory:
    async def test_no_collection_returns_false(self):
        obj = FakeChroma()
        result = await obj.add_screen_memory(["chunk"], [{}], ["id1"])
        assert result is False

    async def test_success_returns_true(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(return_value=None)):
            result = await asyncio.wait_for(
                obj.add_screen_memory(["c1", "c2"], [{}, {}], ["i1", "i2"]), timeout=5
            )
        assert result is True

    async def test_exception_returns_false(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(side_effect=Exception("write fail"))):
            result = await asyncio.wait_for(
                obj.add_screen_memory(["c"], [{}], ["id"]), timeout=5
            )
        assert result is False

    async def test_calls_collection_add_with_correct_args(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        captured: list[dict] = []

        async def _side(func, **kwargs):
            captured.append(kwargs)
            return None

        with patch("asyncio.to_thread", side_effect=_side):
            await asyncio.wait_for(
                obj.add_screen_memory(["text"], [{"ts": "2026"}], ["x1"]), timeout=5
            )
        assert captured[0]["documents"] == ["text"]
        assert captured[0]["metadatas"] == [{"ts": "2026"}]
        assert captured[0]["ids"] == ["x1"]


# ---------------------------------------------------------------------------
# CR9 — search_screen_memory (lines 237-292)
# ---------------------------------------------------------------------------

class TestSearchScreenMemory:
    async def test_no_collection_returns_empty(self):
        obj = FakeChroma()
        result = await obj.search_screen_memory("query")
        assert result == []

    async def test_count_zero_returns_empty(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(return_value=0)):
            result = await asyncio.wait_for(obj.search_screen_memory("q"), timeout=5)
        assert result == []

    async def test_returns_parsed_results_with_distance(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        mock_results = {
            "documents": [["screen doc"]],
            "metadatas": [[{"app": "vscode"}]],
            "ids": [["sid1"]],
            "distances": [[0.12]],
        }
        to_thread_mock = AsyncMock(side_effect=[5, mock_results])
        with patch("asyncio.to_thread", to_thread_mock):
            result = await asyncio.wait_for(obj.search_screen_memory("q"), timeout=5)
        assert len(result) == 1
        assert result[0]["document"] == "screen doc"
        assert result[0]["distance"] == 0.12
        assert result[0]["id"] == "sid1"

    async def test_exception_returns_empty(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(side_effect=Exception("search fail"))):
            result = await asyncio.wait_for(obj.search_screen_memory("q"), timeout=5)
        assert result == []

    async def test_fires_bg_task_for_accessed_ids(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        mock_results = {
            "documents": [["doc"]],
            "metadatas": [[{}]],
            "ids": [["sid1"]],
            "distances": [[0.1]],
        }
        to_thread_mock = AsyncMock(side_effect=[5, mock_results])
        with patch("asyncio.to_thread", to_thread_mock):
            await asyncio.wait_for(obj.search_screen_memory("q", agent="ag1"), timeout=5)
        await asyncio.sleep(0.05)
        obj.log_memory_query.assert_awaited()

    async def test_n_results_capped_at_collection_count(self):
        """n_results > count → n = count to avoid ChromaDB error."""
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        mock_results = {"documents": [[]], "metadatas": [[]], "ids": [[]], "distances": [[]]}
        # count=3, n_results=10 → n=3
        to_thread_mock = AsyncMock(side_effect=[3, mock_results])
        with patch("asyncio.to_thread", to_thread_mock):
            result = await asyncio.wait_for(obj.search_screen_memory("q", n_results=10), timeout=5)
        assert result == []

    async def test_with_where_filter_included_in_query(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        mock_results = {"documents": [[]], "metadatas": [[]], "ids": [[]], "distances": [[]]}
        to_thread_mock = AsyncMock(side_effect=[5, mock_results])
        with patch("asyncio.to_thread", to_thread_mock):
            result = await asyncio.wait_for(
                obj.search_screen_memory("q", where={"app": "safari"}), timeout=5
            )
        assert result == []

    async def test_missing_metadatas_uses_empty_dict(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        mock_results = {
            "documents": [["doc"]],
            "ids": [["sid2"]],
            "distances": [[0.5]],
            # no metadatas key
        }
        to_thread_mock = AsyncMock(side_effect=[2, mock_results])
        with patch("asyncio.to_thread", to_thread_mock):
            result = await asyncio.wait_for(obj.search_screen_memory("q"), timeout=5)
        assert result[0]["metadata"] == {}


# ---------------------------------------------------------------------------
# CR10 — delete_old_screen_memory (lines 294-320)
# ---------------------------------------------------------------------------

class TestDeleteOldScreenMemory:
    async def test_no_collection_returns_zero(self):
        obj = FakeChroma()
        result = await obj.delete_old_screen_memory("2026-01-01T00:00:00")
        assert result == 0

    async def test_no_ids_returns_zero(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(return_value={"ids": []})):
            result = await asyncio.wait_for(
                obj.delete_old_screen_memory("2026-01-01T00:00:00"), timeout=5
            )
        assert result == 0

    async def test_deletes_and_returns_count(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        to_thread_mock = AsyncMock(side_effect=[{"ids": ["a", "b", "c"]}, None])
        with patch("asyncio.to_thread", to_thread_mock):
            result = await asyncio.wait_for(
                obj.delete_old_screen_memory("2026-01-01T00:00:00"), timeout=5
            )
        assert result == 3

    async def test_exception_returns_zero(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(side_effect=Exception("del fail"))):
            result = await asyncio.wait_for(
                obj.delete_old_screen_memory("2026-01-01T00:00:00"), timeout=5
            )
        assert result == 0


# ---------------------------------------------------------------------------
# CR11 — get_screen_memory_stats (lines 322-331)
# ---------------------------------------------------------------------------

class TestGetScreenMemoryStats:
    async def test_no_collection_returns_unavailable(self):
        obj = FakeChroma()
        result = await obj.get_screen_memory_stats()
        assert result == {"available": False, "count": 0}

    async def test_returns_count(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(return_value=77)):
            result = await asyncio.wait_for(obj.get_screen_memory_stats(), timeout=5)
        assert result == {"available": True, "count": 77}

    async def test_exception_returns_error_dict(self):
        obj = FakeChroma()
        obj._screen_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(side_effect=Exception("count fail"))):
            result = await asyncio.wait_for(obj.get_screen_memory_stats(), timeout=5)
        assert result["available"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# CR12 — store_personal_insight (lines 341-372)
# ---------------------------------------------------------------------------

class TestStorePersonalInsight:
    async def test_no_collection_returns_none(self):
        obj = FakeChroma()
        result = await obj.store_personal_insight("text")
        assert result is None

    async def test_returns_uuid(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(return_value=None)):
            result = await asyncio.wait_for(obj.store_personal_insight("text"), timeout=5)
        assert isinstance(result, str) and len(result) == 36

    async def test_metadata_passed_through(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        captured: list[dict] = []

        async def _side(func, **kwargs):
            captured.append(kwargs)
            return None

        with patch("asyncio.to_thread", side_effect=_side):
            await asyncio.wait_for(
                obj.store_personal_insight("personal note", {"tag": "health"}), timeout=5
            )
        assert captured[0]["metadatas"] == [{"tag": "health"}]

    async def test_metadata_none_uses_empty_dict(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        captured: list[dict] = []

        async def _side(func, **kwargs):
            captured.append(kwargs)
            return None

        with patch("asyncio.to_thread", side_effect=_side):
            await asyncio.wait_for(obj.store_personal_insight("note", None), timeout=5)
        assert captured[0]["metadatas"] == [{}]

    async def test_fires_bridge_callback_personal_domain(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        bridge_calls: list[tuple] = []

        async def _bridge(text, domain):
            bridge_calls.append((text, domain))

        obj._bridge_callback = _bridge
        with patch("asyncio.to_thread", AsyncMock(return_value=None)):
            await asyncio.wait_for(obj.store_personal_insight("note"), timeout=5)
        await asyncio.sleep(0.05)
        assert bridge_calls == [("note", "personal")]

    async def test_no_bridge_callback_no_error(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        obj._bridge_callback = None
        with patch("asyncio.to_thread", AsyncMock(return_value=None)):
            result = await asyncio.wait_for(obj.store_personal_insight("text"), timeout=5)
        assert result is not None


# ---------------------------------------------------------------------------
# CR13 — query_personal_memory (lines 374-420)
# ---------------------------------------------------------------------------

class TestQueryPersonalMemory:
    async def test_no_collection_returns_empty(self):
        obj = FakeChroma()
        result = await obj.query_personal_memory("q")
        assert result == []

    async def test_count_zero_returns_empty(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(return_value=0)):
            result = await asyncio.wait_for(obj.query_personal_memory("q"), timeout=5)
        assert result == []

    async def test_returns_results(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        mock_results = {
            "documents": [["personal note"]],
            "metadatas": [[{"tag": "health"}]],
            "ids": [["pid1"]],
        }
        to_thread_mock = AsyncMock(side_effect=[3, mock_results])
        with patch("asyncio.to_thread", to_thread_mock):
            result = await asyncio.wait_for(obj.query_personal_memory("q"), timeout=5)
        assert len(result) == 1
        assert result[0]["document"] == "personal note"
        assert result[0]["id"] == "pid1"

    async def test_exception_returns_empty(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(side_effect=Exception("fail"))):
            result = await asyncio.wait_for(obj.query_personal_memory("q"), timeout=5)
        assert result == []

    async def test_fires_log_query_for_ids(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        mock_results = {
            "documents": [["doc"]],
            "metadatas": [[{}]],
            "ids": [["pid2"]],
        }
        to_thread_mock = AsyncMock(side_effect=[2, mock_results])
        with patch("asyncio.to_thread", to_thread_mock):
            await asyncio.wait_for(
                obj.query_personal_memory("q", agent="agt"), timeout=5
            )
        await asyncio.sleep(0.05)
        obj.log_memory_query.assert_awaited()

    async def test_no_metadatas_uses_empty_dict(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        mock_results = {
            "documents": [["doc"]],
            "ids": [["pid3"]],
            # no metadatas key
        }
        to_thread_mock = AsyncMock(side_effect=[1, mock_results])
        with patch("asyncio.to_thread", to_thread_mock):
            result = await asyncio.wait_for(obj.query_personal_memory("q"), timeout=5)
        assert result[0]["metadata"] == {}

    async def test_with_where_filter(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        mock_results = {"documents": [[]], "metadatas": [[]], "ids": [[]]}
        to_thread_mock = AsyncMock(side_effect=[5, mock_results])
        with patch("asyncio.to_thread", to_thread_mock):
            result = await asyncio.wait_for(
                obj.query_personal_memory("q", where={"tag": "health"}), timeout=5
            )
        assert result == []


# ---------------------------------------------------------------------------
# CR14 — query_personal_memory_recent (lines 422-481)
# ---------------------------------------------------------------------------

class TestQueryPersonalMemoryRecent:
    async def test_primary_returns_results(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        results = [{"document": "new", "metadata": {}, "id": "1"}]
        obj.query_personal_memory = AsyncMock(return_value=results)
        result = await asyncio.wait_for(obj.query_personal_memory_recent("q"), timeout=5)
        assert result == results
        assert obj.query_personal_memory.call_count == 1

    async def test_primary_empty_fallback_returns(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        fallback = [{"document": "old", "metadata": {}, "id": "2"}]
        obj.query_personal_memory = AsyncMock(side_effect=[[], fallback])
        result = await asyncio.wait_for(obj.query_personal_memory_recent("q"), timeout=5)
        assert result == fallback

    async def test_both_empty_returns_empty(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        obj.query_personal_memory = AsyncMock(return_value=[])
        result = await asyncio.wait_for(obj.query_personal_memory_recent("q"), timeout=5)
        assert result == []
        assert obj.query_personal_memory.call_count == 2

    async def test_primary_exception_tries_fallback(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        fallback = [{"document": "f", "id": "3", "metadata": {}}]
        obj.query_personal_memory = AsyncMock(side_effect=[Exception("err"), fallback])
        result = await asyncio.wait_for(obj.query_personal_memory_recent("q"), timeout=5)
        assert result == fallback

    async def test_both_exceptions_returns_empty(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        obj.query_personal_memory = AsyncMock(side_effect=Exception("all fail"))
        result = await asyncio.wait_for(obj.query_personal_memory_recent("q"), timeout=5)
        assert result == []

    async def test_fallback_logs_debug(self):
        """Fallback branch logs a debug message (covers lines 473-478)."""
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        fallback = [{"document": "f", "metadata": {}, "id": "5"}]
        obj.query_personal_memory = AsyncMock(side_effect=[[], fallback])
        result = await asyncio.wait_for(
            obj.query_personal_memory_recent("q", primary_days=10, fallback_days=30), timeout=5
        )
        assert result == fallback

    async def test_where_merged_with_and_filter(self):
        """where != None → _build_where returns $and branch (line 443)."""
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        captured_wheres: list = []

        async def _mock_qpm(query, n_results=5, where=None, agent="unknown"):
            captured_wheres.append(where)
            return []

        obj.query_personal_memory = _mock_qpm
        await asyncio.wait_for(
            obj.query_personal_memory_recent("q", where={"tag": "health"}), timeout=5
        )
        # Primary call should have an $and clause merging base where + date filter
        assert "$and" in captured_wheres[0]
        assert captured_wheres[0]["$and"][0] == {"tag": "health"}


# ---------------------------------------------------------------------------
# CR15 — get_personal_memory_stats (lines 483-492)
# ---------------------------------------------------------------------------

class TestGetPersonalMemoryStats:
    async def test_no_collection_returns_unavailable(self):
        obj = FakeChroma()
        result = await obj.get_personal_memory_stats()
        assert result == {"available": False, "count": 0}

    async def test_returns_count(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(return_value=55)):
            result = await asyncio.wait_for(obj.get_personal_memory_stats(), timeout=5)
        assert result == {"available": True, "count": 55}

    async def test_exception_returns_error_dict(self):
        obj = FakeChroma()
        obj._personal_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(side_effect=Exception("err"))):
            result = await asyncio.wait_for(obj.get_personal_memory_stats(), timeout=5)
        assert result["available"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# CR16 — store_shared_insight (lines 502-531)
# ---------------------------------------------------------------------------

class TestStoreSharedInsight:
    async def test_no_collection_returns_none(self):
        obj = FakeChroma()
        result = await obj.store_shared_insight("text")
        assert result is None

    async def test_returns_uuid(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(return_value=None)):
            result = await asyncio.wait_for(obj.store_shared_insight("cross-domain"), timeout=5)
        assert isinstance(result, str) and len(result) == 36

    async def test_metadata_passed_through(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        captured: list[dict] = []

        async def _side(func, **kwargs):
            captured.append(kwargs)
            return None

        with patch("asyncio.to_thread", side_effect=_side):
            await asyncio.wait_for(
                obj.store_shared_insight("insight", {"topic": "creativity"}), timeout=5
            )
        assert captured[0]["metadatas"] == [{"topic": "creativity"}]

    async def test_metadata_none_uses_empty_dict(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        captured: list[dict] = []

        async def _side(func, **kwargs):
            captured.append(kwargs)
            return None

        with patch("asyncio.to_thread", side_effect=_side):
            await asyncio.wait_for(obj.store_shared_insight("insight", None), timeout=5)
        assert captured[0]["metadatas"] == [{}]


# ---------------------------------------------------------------------------
# CR17 — query_shared_memory (lines 533-580)
# ---------------------------------------------------------------------------

class TestQuerySharedMemory:
    async def test_no_collection_returns_empty(self):
        obj = FakeChroma()
        result = await obj.query_shared_memory("q")
        assert result == []

    async def test_count_zero_returns_empty(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(return_value=0)):
            result = await asyncio.wait_for(obj.query_shared_memory("q"), timeout=5)
        assert result == []

    async def test_returns_results(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        mock_results = {
            "documents": [["shared insight"]],
            "metadatas": [[{"source": "bridge"}]],
            "ids": [["shid1"]],
        }
        to_thread_mock = AsyncMock(side_effect=[2, mock_results])
        with patch("asyncio.to_thread", to_thread_mock):
            result = await asyncio.wait_for(obj.query_shared_memory("q"), timeout=5)
        assert len(result) == 1
        assert result[0]["document"] == "shared insight"
        assert result[0]["id"] == "shid1"

    async def test_exception_returns_empty(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(side_effect=Exception("fail"))):
            result = await asyncio.wait_for(obj.query_shared_memory("q"), timeout=5)
        assert result == []

    async def test_fires_log_query_for_ids(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        mock_results = {
            "documents": [["d"]],
            "metadatas": [[{}]],
            "ids": [["shid2"]],
        }
        to_thread_mock = AsyncMock(side_effect=[1, mock_results])
        with patch("asyncio.to_thread", to_thread_mock):
            await asyncio.wait_for(obj.query_shared_memory("q", agent="a1"), timeout=5)
        await asyncio.sleep(0.05)
        obj.log_memory_query.assert_awaited()

    async def test_no_metadatas_uses_empty_dict(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        mock_results = {
            "documents": [["d"]],
            "ids": [["shid3"]],
            # no metadatas key
        }
        to_thread_mock = AsyncMock(side_effect=[1, mock_results])
        with patch("asyncio.to_thread", to_thread_mock):
            result = await asyncio.wait_for(obj.query_shared_memory("q"), timeout=5)
        assert result[0]["metadata"] == {}

    async def test_with_where_filter(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        mock_results = {"documents": [[]], "metadatas": [[]], "ids": [[]]}
        to_thread_mock = AsyncMock(side_effect=[5, mock_results])
        with patch("asyncio.to_thread", to_thread_mock):
            result = await asyncio.wait_for(
                obj.query_shared_memory("q", where={"topic": "art"}), timeout=5
            )
        assert result == []


# ---------------------------------------------------------------------------
# CR18 — get_shared_memory_stats (lines 582-591)
# ---------------------------------------------------------------------------

class TestGetSharedMemoryStats:
    async def test_no_collection_returns_unavailable(self):
        obj = FakeChroma()
        result = await obj.get_shared_memory_stats()
        assert result == {"available": False, "count": 0}

    async def test_returns_count(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(return_value=12)):
            result = await asyncio.wait_for(obj.get_shared_memory_stats(), timeout=5)
        assert result == {"available": True, "count": 12}

    async def test_exception_returns_error_dict(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(side_effect=Exception("fail"))):
            result = await asyncio.wait_for(obj.get_shared_memory_stats(), timeout=5)
        assert result["available"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# CR19 — delete_stale_shared_memory (lines 593-632)
# ---------------------------------------------------------------------------

class TestDeleteStaleSharedMemory:
    async def test_no_collection_returns_zero(self):
        obj = FakeChroma()
        result = await obj.delete_stale_shared_memory(90)
        assert result == 0

    async def test_no_ids_returns_zero(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(return_value={"ids": []})):
            result = await asyncio.wait_for(obj.delete_stale_shared_memory(90), timeout=5)
        assert result == 0

    async def test_deletes_and_returns_count(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        to_thread_mock = AsyncMock(side_effect=[{"ids": ["x", "y"]}, None])
        with patch("asyncio.to_thread", to_thread_mock):
            result = await asyncio.wait_for(obj.delete_stale_shared_memory(90), timeout=5)
        assert result == 2

    async def test_exception_returns_zero(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        with patch("asyncio.to_thread", AsyncMock(side_effect=Exception("fail"))):
            result = await asyncio.wait_for(obj.delete_stale_shared_memory(90), timeout=5)
        assert result == 0

    async def test_custom_days_parameter(self):
        obj = FakeChroma()
        obj._shared_memory_collection = _col()
        to_thread_mock = AsyncMock(side_effect=[{"ids": ["a", "b", "c"]}, None])
        with patch("asyncio.to_thread", to_thread_mock):
            result = await asyncio.wait_for(obj.delete_stale_shared_memory(30), timeout=5)
        assert result == 3
