from __future__ import annotations

import time
from datetime import datetime

import logging

logger = logging.getLogger("agentpexi.autopilot")


class _StateMixin:
    """Persistent state machine helpers (autopilot_state table)."""

    # ------------------------------------------------------------------
    # Low-level key/value store
    # ------------------------------------------------------------------

    async def _state_get(self, key: str, default: str = "") -> str:
        cursor = await self._db.execute(
            "SELECT value FROM autopilot_state WHERE key=?", (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else default

    async def _state_set(self, key: str, value: str) -> None:
        await self._db.execute(
            """
            INSERT INTO autopilot_state(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE
                SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, time.time()),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Loop status
    # ------------------------------------------------------------------

    async def _get_status(self) -> str:
        return await self._state_get("loop.status", "idle")

    async def _set_status(self, status: str) -> None:
        await self._state_set("loop.status", status)
        if status.startswith("paused"):
            await self._state_set("loop.paused_at",    str(time.time()))
            await self._state_set("loop.pause_reason", status)

    async def _get_quota_resume(self) -> datetime:
        raw = await self._state_get("loop.quota_resume_at", "0")
        try:
            return datetime.fromtimestamp(float(raw))
        except (ValueError, OSError):
            return datetime.now()

    async def _set_quota_resume(self, dt: datetime) -> None:
        await self._state_set("loop.quota_resume_at", str(dt.timestamp()))

    # ------------------------------------------------------------------
    # Skip counters
    # ------------------------------------------------------------------

    async def _get_user_skip_count(self) -> int:
        try:
            return int(await self._state_get("loop.consecutive_user_skips", "0"))
        except ValueError:
            return 0

    async def _get_timeout_count(self) -> int:
        try:
            return int(await self._state_get("loop.consecutive_timeouts", "0"))
        except ValueError:
            return 0

    async def _increment_user_skip(self) -> int:
        n = await self._get_user_skip_count() + 1
        await self._state_set("loop.consecutive_user_skips", str(n))
        return n

    async def _increment_timeout_skip(self) -> int:
        n = await self._get_timeout_count() + 1
        await self._state_set("loop.consecutive_timeouts", str(n))
        return n

    async def _reset_skip_counters(self) -> None:
        await self._state_set("loop.consecutive_user_skips", "0")
        await self._state_set("loop.consecutive_timeouts",   "0")
