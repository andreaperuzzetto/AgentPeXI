"""B-07 — Pinterest delivery mixin + APScheduler job.

TDD: questi test devono essere RED prima dell'implementazione.

Copertura:
  Part 1 – DeliveryMixin routing (tailwind / direct)
  Part 2 – _deliver_via_tailwind (file I/O, JSON fields, return value)
  Part 3 – _deliver_via_direct (create_pin call, return value)
  Part 4 – Scheduler _run_pinterest_publisher (DB interaction, status updates)
  Part 5 – Job registration (pinterest_publisher in _register_builtin_jobs)
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_PIN_ROW = {
    "id": 1,
    "pin_variant": 1,
    "image_path": "/storage/uploads/pin_1.jpg",
    "title": "The Planner That Finally Works for ADHD Brains",
    "description": "Struggling with ADHD? This planner breaks your day into manageable chunks.",
    "board_id": "board_planners_001",
    "board_name": "ADHD Planner Ideas | Digital Printables for Focus",
    "scheduled_at": "2026-01-01T10:00:00+00:00",
    "delivery_method": "tailwind",
    "status": "pending",
}


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_agent(memory=None, pinterest_api=None):
    """PinterestAgent senza dipendenze reali."""
    from apps.backend.agents.pinterest import PinterestAgent  # noqa: PLC0415

    agent = PinterestAgent.__new__(PinterestAgent)
    agent.name = "pinterest"
    agent.model = "claude-haiku-4-5-20251001"
    agent.client = MagicMock()
    agent.memory = memory or MagicMock()
    agent._ws_broadcast = None
    agent._task_id = "test-task"
    agent._step_counter = 0
    agent._llm_call_count = 0
    agent._tool_call_count = 0
    agent._total_cost = 0.0
    agent._total_tokens = 0
    agent.pinterest_api = pinterest_api or MagicMock()
    return agent


async def _make_memory_base(tmp_path):
    """Crea MemoryBase reale con schema B-01 (pinterest_queue + pinterest_boards)."""
    from apps.backend.core._memory._base import MemoryBase  # noqa: PLC0415

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
    await mm.init()
    mm._db = await aiosqlite.connect(mm._db_path)
    mm._db.row_factory = aiosqlite.Row
    return mm


def _make_scheduler_mixin(memory=None, pinterest_agent=None):
    """Crea un oggetto minimale che eredita _PinterestMixin."""
    from apps.backend.core._scheduler._scheduler_pinterest_mixin import _PinterestMixin  # noqa: PLC0415

    class _FakeSched(_PinterestMixin):
        pass

    sched = _FakeSched()
    sched.memory = memory or MagicMock()
    sched.pinterest_agent = pinterest_agent
    sched._notify_telegram = AsyncMock()
    return sched


# ---------------------------------------------------------------------------
# Part 1 — DeliveryMixin: routing by PINTEREST_DELIVERY_METHOD
# ---------------------------------------------------------------------------

class TestDeliverPinRouting:
    """deliver_pin dispatches to tailwind (default) or direct (env override)."""

    @pytest.mark.asyncio
    async def test_deliver_pin_defaults_to_tailwind(self, tmp_path):
        """When PINTEREST_DELIVERY_METHOD is not set, routes to tailwind and returns 'tailwind_queued'."""
        agent = _make_agent()
        pin = dict(_PIN_ROW)

        env = {k: v for k, v in os.environ.items() if k != "PINTEREST_DELIVERY_METHOD"}
        with patch.dict(os.environ, env, clear=True):
            with patch("apps.backend.agents._pinterest._delivery_mixin.settings") as mock_cfg:
                mock_cfg.STORAGE_PATH = str(tmp_path)
                result = await agent.deliver_pin(pin)

        assert result == "tailwind_queued"

    @pytest.mark.asyncio
    async def test_deliver_pin_routes_to_direct_when_env_set(self):
        """When PINTEREST_DELIVERY_METHOD=direct, routes to direct and returns pin_id."""
        mock_api = MagicMock()
        mock_api.create_pin = AsyncMock(return_value={"id": "pin_abc123"})
        agent = _make_agent(pinterest_api=mock_api)

        with patch("apps.backend.agents._pinterest._delivery_mixin.settings") as mock_cfg:
            mock_cfg.PINTEREST_DELIVERY_METHOD = "direct"
            result = await agent.deliver_pin(dict(_PIN_ROW))

        assert result == "pin_abc123"

    @pytest.mark.asyncio
    async def test_deliver_pin_tailwind_explicit(self, tmp_path):
        """When PINTEREST_DELIVERY_METHOD=tailwind, also routes to tailwind."""
        agent = _make_agent()

        with patch("apps.backend.agents._pinterest._delivery_mixin.settings") as mock_cfg:
            mock_cfg.PINTEREST_DELIVERY_METHOD = "tailwind"
            mock_cfg.STORAGE_PATH = str(tmp_path)
            result = await agent.deliver_pin(dict(_PIN_ROW))

        assert result == "tailwind_queued"


# ---------------------------------------------------------------------------
# Part 2 — _deliver_via_tailwind
# ---------------------------------------------------------------------------

class TestDeliverViaTailwind:
    """_deliver_via_tailwind writes JSON to STORAGE_PATH/tailwind_queue/YYYY-MM-DD/pin_{id}.json."""

    @pytest.mark.asyncio
    async def test_creates_json_file(self, tmp_path):
        """Creates pin_{id}.json inside tailwind_queue/YYYY-MM-DD/."""
        agent = _make_agent()
        pin = dict(_PIN_ROW)

        with patch("apps.backend.agents._pinterest._delivery_mixin.settings") as mock_cfg:
            mock_cfg.STORAGE_PATH = str(tmp_path)
            await agent._deliver_via_tailwind(pin)

        files = list((tmp_path / "tailwind_queue").rglob("pin_1.json"))
        assert len(files) == 1

    @pytest.mark.asyncio
    async def test_json_contains_required_fields(self, tmp_path):
        """JSON payload has title, description, image_path, board_name, scheduled_at."""
        agent = _make_agent()
        pin = dict(_PIN_ROW)

        with patch("apps.backend.agents._pinterest._delivery_mixin.settings") as mock_cfg:
            mock_cfg.STORAGE_PATH = str(tmp_path)
            await agent._deliver_via_tailwind(pin)

        files = list((tmp_path / "tailwind_queue").rglob("pin_1.json"))
        data = json.loads(files[0].read_text())

        assert data["title"] == pin["title"]
        assert data["description"] == pin["description"]
        assert data["image_path"] == pin["image_path"]
        assert data["board_name"] == pin["board_name"]
        assert "scheduled_at" in data

    @pytest.mark.asyncio
    async def test_returns_tailwind_queued(self, tmp_path):
        """Returns exactly the string 'tailwind_queued'."""
        agent = _make_agent()

        with patch("apps.backend.agents._pinterest._delivery_mixin.settings") as mock_cfg:
            mock_cfg.STORAGE_PATH = str(tmp_path)
            result = await agent._deliver_via_tailwind(dict(_PIN_ROW))

        assert result == "tailwind_queued"

    @pytest.mark.asyncio
    async def test_directory_named_after_scheduled_at_date(self, tmp_path):
        """Sub-directory under tailwind_queue/ is named YYYY-MM-DD from scheduled_at."""
        agent = _make_agent()
        pin = dict(_PIN_ROW, id=99, scheduled_at="2026-06-15T14:00:00+00:00")

        with patch("apps.backend.agents._pinterest._delivery_mixin.settings") as mock_cfg:
            mock_cfg.STORAGE_PATH = str(tmp_path)
            await agent._deliver_via_tailwind(pin)

        date_dir = tmp_path / "tailwind_queue" / "2026-06-15"
        assert date_dir.exists(), f"Expected dir at {date_dir}"


# ---------------------------------------------------------------------------
# Part 3 — _deliver_via_direct
# ---------------------------------------------------------------------------

class TestDeliverViaDirect:
    """_deliver_via_direct calls pinterest_api.create_pin and returns pin_id."""

    @pytest.mark.asyncio
    async def test_calls_create_pin(self):
        """Passes board_id, title, description, image_path to pinterest_api.create_pin."""
        mock_api = MagicMock()
        mock_api.create_pin = AsyncMock(return_value={"id": "pin_xyz789"})
        agent = _make_agent(pinterest_api=mock_api)

        await agent._deliver_via_direct(dict(_PIN_ROW))

        mock_api.create_pin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_pin_id_from_api_response(self):
        """Returns the 'id' field from create_pin response."""
        mock_api = MagicMock()
        mock_api.create_pin = AsyncMock(return_value={"id": "pin_xyz789"})
        agent = _make_agent(pinterest_api=mock_api)

        result = await agent._deliver_via_direct(dict(_PIN_ROW))

        assert result == "pin_xyz789"


# ---------------------------------------------------------------------------
# Part 4 — Scheduler: _run_pinterest_publisher
# ---------------------------------------------------------------------------

class TestSchedulerPinterestPublisher:
    """_run_pinterest_publisher queries pending due pins and delivers them."""

    @pytest.mark.asyncio
    async def test_no_op_when_pinterest_agent_is_none(self, tmp_path):
        """Returns early without DB access if pinterest_agent is None."""
        sched = _make_scheduler_mixin(pinterest_agent=None)
        # Should not raise even though memory.get_db would return None
        await sched._run_pinterest_publisher()

    @pytest.mark.asyncio
    async def test_skips_pins_not_yet_due(self, tmp_path):
        """Pins with scheduled_at in the future are not delivered."""
        mm = await _make_memory_base(tmp_path)
        mock_agent = MagicMock()
        mock_agent.deliver_pin = AsyncMock(return_value="tailwind_queued")

        future_ts = "2099-01-01T00:00:00+00:00"
        await mm._db.execute(
            "INSERT INTO pinterest_queue (pin_variant, image_path, title, description, board_id, scheduled_at) VALUES (?,?,?,?,?,?)",
            (1, "/img.jpg", "T", "D", "b001", future_ts),
        )
        await mm._db.commit()

        sched = _make_scheduler_mixin(pinterest_agent=mock_agent, memory=mm)
        await sched._run_pinterest_publisher()

        mock_agent.deliver_pin.assert_not_awaited()
        await mm._db.close()

    @pytest.mark.asyncio
    async def test_delivers_pending_due_pins(self, tmp_path):
        """Calls deliver_pin once for each pending pin with scheduled_at <= now."""
        mm = await _make_memory_base(tmp_path)
        mock_agent = MagicMock()
        mock_agent.deliver_pin = AsyncMock(return_value="tailwind_queued")

        past_ts = "2020-01-01T00:00:00+00:00"
        await mm._db.execute(
            "INSERT INTO pinterest_queue (pin_variant, image_path, title, description, board_id, scheduled_at) VALUES (?,?,?,?,?,?)",
            (1, "/img.jpg", "T", "D", "b001", past_ts),
        )
        await mm._db.commit()

        sched = _make_scheduler_mixin(pinterest_agent=mock_agent, memory=mm)
        await sched._run_pinterest_publisher()

        mock_agent.deliver_pin.assert_awaited_once()
        await mm._db.close()

    @pytest.mark.asyncio
    async def test_updates_status_to_published(self, tmp_path):
        """On success, sets status='published'."""
        mm = await _make_memory_base(tmp_path)
        mock_agent = MagicMock()
        mock_agent.deliver_pin = AsyncMock(return_value="tailwind_queued")

        past_ts = "2020-01-01T00:00:00+00:00"
        async with mm._db.execute(
            "INSERT INTO pinterest_queue (pin_variant, image_path, title, description, board_id, scheduled_at) VALUES (?,?,?,?,?,?)",
            (1, "/img.jpg", "T", "D", "b001", past_ts),
        ) as cur:
            row_id = cur.lastrowid
        await mm._db.commit()

        sched = _make_scheduler_mixin(pinterest_agent=mock_agent, memory=mm)
        await sched._run_pinterest_publisher()

        async with mm._db.execute("SELECT status FROM pinterest_queue WHERE id=?", (row_id,)) as cur:
            row = await cur.fetchone()
        assert row["status"] == "published"
        await mm._db.close()

    @pytest.mark.asyncio
    async def test_stores_pinterest_pin_id(self, tmp_path):
        """On success, stores the pin_id returned by deliver_pin."""
        mm = await _make_memory_base(tmp_path)
        mock_agent = MagicMock()
        mock_agent.deliver_pin = AsyncMock(return_value="pin_delivered_XYZ")

        past_ts = "2020-01-01T00:00:00+00:00"
        async with mm._db.execute(
            "INSERT INTO pinterest_queue (pin_variant, image_path, title, description, board_id, scheduled_at) VALUES (?,?,?,?,?,?)",
            (1, "/img.jpg", "T", "D", "b001", past_ts),
        ) as cur:
            row_id = cur.lastrowid
        await mm._db.commit()

        sched = _make_scheduler_mixin(pinterest_agent=mock_agent, memory=mm)
        await sched._run_pinterest_publisher()

        async with mm._db.execute("SELECT pinterest_pin_id FROM pinterest_queue WHERE id=?", (row_id,)) as cur:
            row = await cur.fetchone()
        assert row["pinterest_pin_id"] == "pin_delivered_XYZ"
        await mm._db.close()

    @pytest.mark.asyncio
    async def test_sets_published_at(self, tmp_path):
        """On success, sets published_at to a non-null timestamp."""
        mm = await _make_memory_base(tmp_path)
        mock_agent = MagicMock()
        mock_agent.deliver_pin = AsyncMock(return_value="tailwind_queued")

        past_ts = "2020-01-01T00:00:00+00:00"
        async with mm._db.execute(
            "INSERT INTO pinterest_queue (pin_variant, image_path, title, description, board_id, scheduled_at) VALUES (?,?,?,?,?,?)",
            (1, "/img.jpg", "T", "D", "b001", past_ts),
        ) as cur:
            row_id = cur.lastrowid
        await mm._db.commit()

        sched = _make_scheduler_mixin(pinterest_agent=mock_agent, memory=mm)
        await sched._run_pinterest_publisher()

        async with mm._db.execute("SELECT published_at FROM pinterest_queue WHERE id=?", (row_id,)) as cur:
            row = await cur.fetchone()
        assert row["published_at"] is not None
        await mm._db.close()

    @pytest.mark.asyncio
    async def test_increments_board_pin_count(self, tmp_path):
        """On success, increments pin_count in pinterest_boards for the board."""
        mm = await _make_memory_base(tmp_path)
        mock_agent = MagicMock()
        mock_agent.deliver_pin = AsyncMock(return_value="tailwind_queued")

        await mm._db.execute(
            "INSERT INTO pinterest_boards (board_id, board_name, section_key, pin_count) VALUES (?,?,?,?)",
            ("b001", "My Board", "section_a", 5),
        )
        past_ts = "2020-01-01T00:00:00+00:00"
        await mm._db.execute(
            "INSERT INTO pinterest_queue (pin_variant, image_path, title, description, board_id, scheduled_at) VALUES (?,?,?,?,?,?)",
            (1, "/img.jpg", "T", "D", "b001", past_ts),
        )
        await mm._db.commit()

        sched = _make_scheduler_mixin(pinterest_agent=mock_agent, memory=mm)
        await sched._run_pinterest_publisher()

        async with mm._db.execute("SELECT pin_count FROM pinterest_boards WHERE board_id=?", ("b001",)) as cur:
            row = await cur.fetchone()
        assert row["pin_count"] == 6
        await mm._db.close()

    @pytest.mark.asyncio
    async def test_marks_failed_and_notifies_on_error(self, tmp_path):
        """On delivery error, sets status='failed' and sends Telegram notification."""
        mm = await _make_memory_base(tmp_path)
        mock_agent = MagicMock()
        mock_agent.deliver_pin = AsyncMock(side_effect=Exception("Network timeout"))

        past_ts = "2020-01-01T00:00:00+00:00"
        async with mm._db.execute(
            "INSERT INTO pinterest_queue (pin_variant, image_path, title, description, board_id, scheduled_at) VALUES (?,?,?,?,?,?)",
            (1, "/img.jpg", "T", "D", "b001", past_ts),
        ) as cur:
            row_id = cur.lastrowid
        await mm._db.commit()

        sched = _make_scheduler_mixin(pinterest_agent=mock_agent, memory=mm)
        await sched._run_pinterest_publisher()

        async with mm._db.execute("SELECT status FROM pinterest_queue WHERE id=?", (row_id,)) as cur:
            row = await cur.fetchone()
        assert row["status"] == "failed"
        sched._notify_telegram.assert_awaited_once()
        await mm._db.close()

    @pytest.mark.asyncio
    async def test_processes_multiple_due_pins(self, tmp_path):
        """Processes all pending due pins in one run."""
        mm = await _make_memory_base(tmp_path)
        mock_agent = MagicMock()
        mock_agent.deliver_pin = AsyncMock(return_value="tailwind_queued")

        past_ts = "2020-01-01T00:00:00+00:00"
        for _ in range(3):
            await mm._db.execute(
                "INSERT INTO pinterest_queue (pin_variant, image_path, title, description, board_id, scheduled_at) VALUES (?,?,?,?,?,?)",
                (1, "/img.jpg", "T", "D", "b001", past_ts),
            )
        await mm._db.commit()

        sched = _make_scheduler_mixin(pinterest_agent=mock_agent, memory=mm)
        await sched._run_pinterest_publisher()

        assert mock_agent.deliver_pin.await_count == 3
        await mm._db.close()


# ---------------------------------------------------------------------------
# Part 5 — Job registration
# ---------------------------------------------------------------------------

class TestPinterestJobRegistration:
    """pinterest_publisher job is registered when _register_builtin_jobs() is called."""

    def test_pinterest_publisher_job_registered(self):
        """Scheduler has a job with id='pinterest_publisher' after setup."""
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apps.backend.core.scheduler import Scheduler  # noqa: PLC0415

        sched = Scheduler.__new__(Scheduler)
        sched.memory = MagicMock()
        sched._ws_broadcast = None
        sched.pepe = MagicMock()
        sched.storage = None
        sched.research_agent = None
        sched.design_agent = None
        sched.publisher_agent = None
        sched.analytics_agent = None
        sched.finance_agent = None
        sched._telegram_broadcast = None
        sched.screen_watcher = None
        sched.production_queue = None
        sched.budget_manager = None
        sched.publication_policy = None
        sched.autopilot_loop = None
        sched.etsy_client = None
        sched.shop_optimizer = None
        sched.etsy_ads_manager = None
        sched.learning_loop = None
        sched.pinterest_agent = None
        sched._scheduler = AsyncIOScheduler()
        sched._job_status = {}
        sched._job_status_lock = threading.Lock()
        sched._internal_jobs = {"ssd_health_check", "agent_status_sync"}

        import types
        fake_settings = types.SimpleNamespace(
            REMIND_CHECKER_INTERVAL=2,
            REMIND_UNACK_PING_HOURS=1,
            URGENCY_MEDIUM_DIGEST_HOUR=9,
        )
        with patch("apps.backend.core._scheduler._scheduler_core_mixin.settings", fake_settings):
            sched._register_builtin_jobs()

        job_ids = [j.id for j in sched._scheduler.get_jobs()]
        assert "pinterest_publisher" in job_ids
