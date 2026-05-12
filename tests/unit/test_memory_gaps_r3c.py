"""Coverage tests targeting residual gaps in memory mixins.

Targets:
  _base.py       lines 476, 577-578, 615-619, 639-652, 669-670, 686-687, 730-733, 736-738
  _oauth.py      lines 64-66
  _reminders.py  lines 104-109
  _etsy_listings.py lines 82-84
  _learning.py   lines 80-81
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.backend.core._memory._base import MemoryBase, _VoyageEmbeddingFunction
from apps.backend.core._memory._etsy_listings import EtsyListingsMixin
from apps.backend.core._memory._learning import LearningMixin
from apps.backend.core._memory._oauth import OAuthMixin
from apps.backend.core._memory._reminders import RemindersMixin


# ---------------------------------------------------------------------------
# Concrete test classes
# ---------------------------------------------------------------------------

class FakeMemBase(MemoryBase):
    pass


class FakeOAuth(OAuthMixin):
    """OAuthMixin with a stub _fernet() so tests can override it per-instance."""

    def _fernet(self):  # noqa: D102
        raise NotImplementedError("inject via obj._fernet = lambda: mock")


class FakeReminders(RemindersMixin):
    pass


class FakeEtsyListings(EtsyListingsMixin):
    pass


class FakeLearning(LearningMixin):
    pass


# ---------------------------------------------------------------------------
# Mock helpers — same _DualCursorMock contract as other memory test files
# ---------------------------------------------------------------------------

class _DualCursorMock:
    """Supports both `cursor = await db.execute(...)` and `async with db.execute(...) as cur:`."""

    def __init__(self, cursor: MagicMock) -> None:
        self._cursor = cursor

    def __await__(self):
        async def _c():
            return self._cursor
        return _c().__await__()

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *args):
        return False


def _make_cursor(fetchone=None, fetchall=None, lastrowid: int = 1) -> MagicMock:
    c = MagicMock()
    c.fetchone = AsyncMock(return_value=fetchone)
    c.fetchall = AsyncMock(return_value=fetchall if fetchall is not None else [])
    c.lastrowid = lastrowid
    return c


def _make_db(fetchone=None, fetchall=None):
    """Return (db_mock, cursor_mock) — dual-mode: supports both await and async-with."""
    cur = _make_cursor(fetchone=fetchone, fetchall=fetchall)
    db = MagicMock()
    db.execute = MagicMock(side_effect=lambda *a, **kw: _DualCursorMock(cur))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.close = AsyncMock()
    return db, cur


def _make_ctx_db(fetchone=None):
    """Return db mock using the raw ctx_mgr pattern for `async with db.execute() as cur:`."""
    cur_mock = _make_cursor(fetchone=fetchone)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=cur_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    db = MagicMock()
    db.execute = MagicMock(return_value=ctx)
    db.commit = AsyncMock()
    return db, cur_mock


# ---------------------------------------------------------------------------
# init() DB mock helper — routes SQL to correct behaviour per test scenario
# ---------------------------------------------------------------------------

def _make_init_db(
    fail_migration_msg: str | None = None,
    fail_index: bool = False,
    fail_cleanup: bool = False,
) -> MagicMock:
    """Mock DB for MemoryBase.init() scenarios.

    execute() is an async callable that discriminates SQL by prefix so each
    test can trigger exactly one error branch in isolation.
    """
    db = MagicMock()
    db.commit = AsyncMock()
    db.executescript = AsyncMock()
    db.close = AsyncMock()

    async def execute_side(sql, *args, **kw):
        # Schema setup and WAL pragmas — always succeed
        if sql.startswith("PRAGMA") or sql.startswith("BEGIN"):
            return MagicMock()
        # Migration statements
        if sql.startswith("ALTER TABLE") or (
            sql.startswith("UPDATE") and "production_queue" in sql
        ):
            if fail_migration_msg:
                raise Exception(fail_migration_msg)
            return MagicMock()
        # Index creation statements
        if sql.startswith("CREATE INDEX"):
            if fail_index:
                raise Exception("index create failed")
            return MagicMock()
        # Cleanup agent_logs
        if "agent_logs" in sql and "UPDATE" in sql:
            if fail_cleanup:
                raise Exception("cleanup agent_logs failed")
            return MagicMock()
        return MagicMock()

    db.execute = execute_side
    return db


# ===========================================================================
# SECTION 1 — _base.py gaps
# ===========================================================================

# ---------------------------------------------------------------------------
# Line 476 — MemoryBase._fernet() returns get_fernet()
# ---------------------------------------------------------------------------

class TestFernet:
    def test_fernet_delegates_to_get_fernet(self):
        obj = FakeMemBase()
        sentinel = object()
        with patch("apps.backend.core._memory._base.get_fernet", return_value=sentinel) as m:
            result = obj._fernet()
        m.assert_called_once()
        assert result is sentinel


# ---------------------------------------------------------------------------
# Lines 577-578 — init(): migration fails with non-duplicate error → re-raise
# ---------------------------------------------------------------------------

class TestInitMigrationNonDupError:
    async def test_migration_non_dup_error_raises(self):
        obj = FakeMemBase()
        db = _make_init_db(fail_migration_msg="fatal schema error XYZ")

        with patch("aiosqlite.connect", new_callable=AsyncMock, return_value=db), \
             patch("os.makedirs"):
            with pytest.raises(Exception, match="fatal schema error XYZ"):
                await asyncio.wait_for(obj.init(), timeout=5)


# ---------------------------------------------------------------------------
# Lines 615-619 — init(): index creation error is logged and swallowed
# ---------------------------------------------------------------------------

class TestInitIndexError:
    async def test_index_error_swallowed_init_completes(self):
        obj = FakeMemBase()
        db = _make_init_db(fail_index=True)

        with patch("aiosqlite.connect", new_callable=AsyncMock, return_value=db), \
             patch("os.makedirs"):
            # Must not raise — index errors are non-fatal
            await asyncio.wait_for(obj.init(), timeout=5)

        assert obj._db is db


# ---------------------------------------------------------------------------
# Lines 639-652 — init(): ChromaDB collections created on successful import
# ---------------------------------------------------------------------------

class TestInitChromaDBCollectionsCreated:
    async def test_screen_personal_shared_collections_assigned(self):
        obj = FakeMemBase()
        db = _make_init_db()

        mock_collection = MagicMock()
        mock_chroma = MagicMock()
        mock_chroma.PersistentClient.return_value.get_or_create_collection.return_value = (
            mock_collection
        )
        mock_voyage = MagicMock()

        with patch("aiosqlite.connect", new_callable=AsyncMock, return_value=db), \
             patch("os.makedirs"), \
             patch.dict(sys.modules, {"chromadb": mock_chroma, "voyageai": mock_voyage}):
            await asyncio.wait_for(obj.init(), timeout=5)

        assert obj._screen_memory_collection is mock_collection
        assert obj._personal_memory_collection is mock_collection
        assert obj._shared_memory_collection is mock_collection


# ---------------------------------------------------------------------------
# Lines 669-670 — init(): cleanup agent_logs execute fails → logged, swallowed
# ---------------------------------------------------------------------------

class TestInitCleanupError:
    async def test_cleanup_error_swallowed_init_completes(self):
        obj = FakeMemBase()
        db = _make_init_db(fail_cleanup=True)

        with patch("aiosqlite.connect", new_callable=AsyncMock, return_value=db), \
             patch("os.makedirs"):
            await asyncio.wait_for(obj.init(), timeout=5)

        assert obj._db is db


# ---------------------------------------------------------------------------
# Lines 686-687 — close(): WAL checkpoint exception is logged and swallowed
# ---------------------------------------------------------------------------

class TestCloseWALCheckpointFails:
    async def test_wal_checkpoint_exception_swallowed_db_closed(self):
        obj = FakeMemBase()
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=Exception("wal checkpoint failed"))
        db.close = AsyncMock()
        obj._db = db

        await asyncio.wait_for(obj.close(), timeout=5)

        assert obj._db is None
        db.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Lines 730-733 — _VoyageEmbeddingFunction._get_client() lazy initialisation
# ---------------------------------------------------------------------------

class TestVoyageGetClient:
    def test_get_client_creates_client_when_none(self):
        mock_voyage = MagicMock()
        mock_client = MagicMock()
        mock_voyage.Client.return_value = mock_client

        with patch.dict(sys.modules, {"voyageai": mock_voyage}):
            vef = _VoyageEmbeddingFunction(api_key="test_key", model="voyage-test")
            assert vef._client is None
            client = vef._get_client()

        assert client is mock_client
        mock_voyage.Client.assert_called_once_with(api_key="test_key")

    def test_get_client_returns_cached_client(self):
        mock_client = MagicMock()
        vef = _VoyageEmbeddingFunction(api_key="k", model="m")
        vef._client = mock_client

        result = vef._get_client()

        assert result is mock_client


# ---------------------------------------------------------------------------
# Lines 736-738 — _VoyageEmbeddingFunction.__call__() returns embeddings
# ---------------------------------------------------------------------------

class TestVoyageCall:
    def test_call_embeds_input_and_returns_embeddings(self):
        mock_client = MagicMock()
        mock_client.embed.return_value.embeddings = [[0.1, 0.2, 0.3]]

        vef = _VoyageEmbeddingFunction(api_key="k", model="my-model")
        vef._client = mock_client  # skip lazy init

        result = vef(["hello world"])

        assert result == [[0.1, 0.2, 0.3]]
        mock_client.embed.assert_called_once_with(["hello world"], model="my-model")


# ===========================================================================
# SECTION 2 — _oauth.py lines 64-66: decrypt exception re-raised
# ===========================================================================

class TestOAuthDecryptFails:
    async def test_get_oauth_tokens_decrypt_exception_reraises(self):
        obj = FakeOAuth()
        db, _ = _make_db(
            fetchone={
                "provider": "etsy",
                "access_token_encrypted": "bad_data",
                "refresh_token_encrypted": "bad_data2",
            }
        )
        obj._db = db

        bad_fernet = MagicMock()
        bad_fernet.decrypt.side_effect = Exception("invalid token, Fernet key mismatch")
        obj._fernet = lambda: bad_fernet  # inject directly on instance

        with pytest.raises(Exception, match="invalid token"):
            await asyncio.wait_for(obj.get_oauth_tokens("etsy"), timeout=5)


# ===========================================================================
# SECTION 3 — _reminders.py lines 104-109: get_reminder_notion_id_by_id()
# ===========================================================================

class TestGetReminderNotionIdById:
    async def test_returns_notion_page_id_when_row_found(self):
        obj = FakeReminders()
        db, _ = _make_ctx_db(fetchone={"notion_page_id": "notion-abc-123"})
        obj._db = db

        result = await asyncio.wait_for(obj.get_reminder_notion_id_by_id(42), timeout=5)

        assert result == "notion-abc-123"

    async def test_returns_none_when_row_not_found(self):
        obj = FakeReminders()
        db, _ = _make_ctx_db(fetchone=None)
        obj._db = db

        result = await asyncio.wait_for(obj.get_reminder_notion_id_by_id(99), timeout=5)

        assert result is None


# ===========================================================================
# SECTION 4 — other minor gaps
# ===========================================================================

# ---------------------------------------------------------------------------
# _etsy_listings.py lines 82-84: update_etsy_listing_stats() rollback on error
# ---------------------------------------------------------------------------

class TestUpdateEtsyListingStatsRollback:
    async def test_execute_failure_triggers_rollback_and_reraise(self):
        obj = FakeEtsyListings()

        db = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        call_n = [0]

        async def execute_side(sql, *args, **kw):
            call_n[0] += 1
            if call_n[0] == 1:
                return MagicMock()  # BEGIN IMMEDIATE succeeds
            raise Exception("DB write failed")

        db.execute = execute_side
        obj._db = db

        with pytest.raises(Exception, match="DB write failed"):
            await asyncio.wait_for(
                obj.update_etsy_listing_stats("lid1", 10, 5, 2, 9.99, "active", "2026-01-01"),
                timeout=5,
            )

        db.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# _learning.py lines 80-81: upsert_learning() save_learning_evaluation fails silently
# ---------------------------------------------------------------------------

class TestUpsertLearningEvalFails:
    async def test_save_evaluation_exception_is_swallowed(self):
        """Row exists, |weight_delta| >= threshold, save_learning_evaluation raises → logged, not raised."""
        obj = FakeLearning()
        existing_row = {"id": 7, "weight": 0.5, "occurrences": 3}
        db, _ = _make_db(fetchone=existing_row)
        obj._db = db

        obj.save_learning_evaluation = AsyncMock(side_effect=Exception("eval DB error"))

        # Must not raise
        await asyncio.wait_for(
            obj.upsert_learning("agent_x", "tone", "friendly", "positive", 0.1),
            timeout=5,
        )

        obj.save_learning_evaluation.assert_awaited_once()
