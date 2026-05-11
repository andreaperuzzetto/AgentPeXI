"""TaskRegistry — centralised asyncio task tracking (CNC-033 + CNC-034)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger("agentpexi.task_registry")


class TaskRegistry:
    """Tracks all asyncio.Task objects created by the application.

    Ensures tasks are properly awaited on shutdown and errors are logged.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def create_task(self, coro: Coroutine[Any, Any, Any], name: str | None = None) -> asyncio.Task:
        """Create and register a tracked asyncio.Task."""
        t = asyncio.create_task(coro, name=name)
        self._tasks.add(t)
        t.add_done_callback(self._on_done)
        return t

    def _on_done(self, t: asyncio.Task) -> None:
        self._tasks.discard(t)
        if not t.cancelled() and t.exception():
            logger.error("Task '%s' failed", t.get_name(), exc_info=t.exception())

    async def shutdown(self) -> None:
        """Cancel all tracked tasks and wait for them to finish."""
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
