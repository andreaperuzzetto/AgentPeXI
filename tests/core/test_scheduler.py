"""Tests for Scheduler — lifecycle, job configuration, health-check SSD."""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from apps.backend.core.scheduler import Scheduler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_memory():
    mem = MagicMock()
    mem.get_enabled_scheduled_tasks = AsyncMock(return_value=[])
    return mem


@pytest.fixture
def scheduler(mock_memory):
    return Scheduler(memory=mock_memory)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def test_start_creates_running_scheduler(scheduler):
    """After start(), the APScheduler is running."""
    await scheduler.start()
    assert scheduler._scheduler.running
    await scheduler.stop()


async def test_stop_halts_scheduler(scheduler):
    """After stop(), the APScheduler is not running (requires event-loop yield)."""
    await scheduler.start()
    await scheduler.stop()
    await asyncio.sleep(0.1)  # APScheduler needs one loop iteration to set running=False
    assert not scheduler._scheduler.running


async def test_start_twice_no_exception(scheduler):
    """Calling start() a second time returns early — no SchedulerAlreadyRunningError."""
    await scheduler.start()
    await scheduler.start()  # should be a no-op
    assert scheduler._scheduler.running
    await scheduler.stop()


async def test_start_loads_db_jobs(scheduler, mock_memory):
    """start() calls memory.get_enabled_scheduled_tasks to load DB jobs."""
    await scheduler.start()
    mock_memory.get_enabled_scheduled_tasks.assert_called_once()
    await scheduler.stop()


# ---------------------------------------------------------------------------
# Job attributes — publish_checker
# ---------------------------------------------------------------------------

async def test_publish_checker_has_correct_attributes(scheduler):
    """publish_checker job must have coalesce=True and max_instances=1."""
    await scheduler.start()
    job = scheduler._scheduler.get_job("publish_checker")
    assert job is not None, "publish_checker job not registered"
    assert job.coalesce is True
    assert job.max_instances == 1
    await scheduler.stop()


# ---------------------------------------------------------------------------
# Job attributes — pinterest_publisher
# ---------------------------------------------------------------------------

async def test_pinterest_publisher_has_correct_attributes(scheduler):
    """pinterest_publisher job must have coalesce=True and max_instances=1."""
    await scheduler.start()
    job = scheduler._scheduler.get_job("pinterest_publisher")
    assert job is not None, "pinterest_publisher job not registered"
    assert job.coalesce is True
    assert job.max_instances == 1
    await scheduler.stop()


# ---------------------------------------------------------------------------
# _health_check_ssd — no StorageManager
# ---------------------------------------------------------------------------

async def test_health_check_ssd_calls_to_thread_with_isdir(scheduler):
    """With storage=None, _health_check_ssd uses asyncio.to_thread(os.path.isdir, ...)."""
    scheduler.storage = None
    scheduler.pepe = None

    module_path = "apps.backend.core._scheduler._scheduler_system_mixin.asyncio.to_thread"
    with patch(module_path, new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = True
        await scheduler._health_check_ssd()

    mock_to_thread.assert_called_once()
    call_args = mock_to_thread.call_args[0]
    assert call_args[0] is os.path.isdir


async def test_health_check_ssd_path_not_found_logs(scheduler, caplog):
    """_health_check_ssd logs an error when STORAGE_PATH is not accessible."""
    import logging
    scheduler.storage = None
    scheduler.pepe = None

    module_path = "apps.backend.core._scheduler._scheduler_system_mixin.asyncio.to_thread"
    with patch(module_path, new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = False
        with caplog.at_level(logging.ERROR, logger="agentpexi.scheduler"):
            await scheduler._health_check_ssd()

    assert any("STORAGE_PATH" in r.message for r in caplog.records)
