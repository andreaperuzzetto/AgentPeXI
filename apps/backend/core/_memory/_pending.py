"""Pending actions mixin for MemoryManager."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apps.backend.core._memory._base import _json_dumps, _json_loads

logger = logging.getLogger("agentpexi.memory")


class PendingMixin:
    # ------------------------------------------------------------------
    # Pending actions
    # ------------------------------------------------------------------

    async def save_pending_action(
        self,
        action_type: str,
        payload: dict,
        expires_hours: int = 24,
        task_id: str | None = None,
    ) -> None:
        """INSERT OR REPLACE — sovrascrive pending_action precedente dello stesso tipo."""
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=expires_hours)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        await self._db.execute(
            """INSERT OR REPLACE INTO pending_actions
               (action_type, payload, expires_at, task_id)
               VALUES (?, ?, ?, ?)""",
            (action_type, _json_dumps(payload), expires_at, task_id),
        )
        await self._db.commit()

    async def get_pending_action(self, action_type: str) -> dict | None:
        """Ritorna None se assente o scaduto."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """SELECT * FROM pending_actions
               WHERE action_type = ? AND expires_at > ?""",
            (action_type, now),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["payload"] = _json_loads(d.get("payload"))
        return d

    async def delete_pending_action(self, action_type: str) -> None:
        await self._db.execute(
            "DELETE FROM pending_actions WHERE action_type = ?",
            (action_type,),
        )
        await self._db.commit()

    async def get_pending_input_for_task(self, task_id: str) -> dict | None:
        """Recupera pending_action collegata a un task specifico."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """SELECT * FROM pending_actions
               WHERE task_id = ? AND expires_at > ?""",
            (task_id, now),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["payload"] = _json_loads(d.get("payload"))
        return d

    async def resolve_pending_input(self, task_id: str) -> None:
        """Marca come risolta la pending_action di un task (dopo risposta utente)."""
        await self._db.execute(
            "DELETE FROM pending_actions WHERE task_id = ?",
            (task_id,),
        )
        await self._db.commit()

    async def get_pending_input_tasks(self) -> list[dict]:
        """Lista task in stato INPUT_REQUIRED (pending_actions con action_type=clarification, non scadute)."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """SELECT * FROM pending_actions
               WHERE action_type = 'clarification' AND expires_at > ?
               ORDER BY rowid DESC""",
            (now,),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["payload"] = _json_loads(d.get("payload"))
            result.append(d)
        return result
