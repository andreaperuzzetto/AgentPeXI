"""Scheduler — system mixin: SSD health, agent sync, screen cleanup, DB task runner."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.scheduler")


class _SystemMixin:
    """System-level scheduled jobs: SSD health, agent status sync, screen cleanup."""

    async def _run_screen_cleanup(self) -> None:
        """Job nightly 03:00 — elimina chunk screen_memory più vecchi di SCREEN_RETENTION_DAYS."""
        if self.screen_watcher is None:
            return
        try:
            deleted = await self.screen_watcher.cleanup_old_memories()
            if deleted and self._telegram_broadcast:
                await self._telegram_broadcast(
                    f"🧹 Screen cleanup: eliminati {deleted} chunk "
                    f"(retention {settings.SCREEN_RETENTION_DAYS}gg)"
                )
        except Exception as exc:
            logger.error("screen_cleanup fallito: %s", exc)

    async def _health_check_ssd(self) -> None:
        """Verifica che STORAGE_PATH sia montato e accessibile tramite StorageManager."""
        if not self.storage:
            # Fallback senza StorageManager
            storage = settings.STORAGE_PATH
            ok = await asyncio.to_thread(os.path.isdir, storage)
            if not ok:
                msg = f"⚠️ STORAGE_PATH non accessibile: {storage}"
                logger.error(msg)
                if self.pepe and hasattr(self.pepe, "notify_telegram"):
                    await self.pepe.notify_telegram(msg, priority=True)
            return

        health = await asyncio.to_thread(self.storage.health_check)

        if not health["available"]:
            msg = f"⚠️ STORAGE_PATH non accessibile: {settings.STORAGE_PATH}"
            logger.error(msg)
            if self.pepe and hasattr(self.pepe, "notify_telegram"):
                await self.pepe.notify_telegram(msg, priority=True)
            await self._broadcast({
                "type": "system_status",
                "event": "ssd_offline",
                "storage_path": settings.STORAGE_PATH,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        else:
            free_gb = health["free_gb"]
            if free_gb < 1.0:
                msg = f"⚠️ Spazio SSD basso: {free_gb:.1f} GB rimasti"
                logger.warning(msg)
                if self.pepe and hasattr(self.pepe, "notify_telegram"):
                    await self.pepe.notify_telegram(msg, priority=True)

            logger.debug(
                "SSD OK — %.1f GB liberi, %d file pending",
                free_gb,
                health["pending_count"],
            )
            await self._broadcast({
                "type": "system_status",
                "event": "ssd_health",
                "health": health,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    async def _sync_agent_status(self) -> None:
        """Broadcast stato agenti + contesto decisionale via WebSocket ogni 30s."""
        if not self.pepe:
            return

        statuses = self.pepe.get_agent_statuses()
        queue_size = self.pepe._queue.qsize() if hasattr(self.pepe, "_queue") else 0
        active_tasks = sum(1 for s in statuses.values() if s == "running")

        await self._broadcast({
            "type": "system_status",
            "event": "agent_sync",
            "agents": statuses,
            "queue_size": queue_size,
            "active_tasks": active_tasks,
            "mock_mode": getattr(self.pepe, "mock_mode", False),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Emetti anche lo stato contestuale — toglierà i valori mock dal pannello
        # "Contesto decisionale" nel frontend senza dipendere dal confidence gate
        if hasattr(self.pepe, "get_context_state"):
            ctx = self.pepe.get_context_state()
            await self._broadcast(ctx)

    # ------------------------------------------------------------------
    # Esecuzione task schedulati da DB
    # ------------------------------------------------------------------

    async def _run_scheduled_task(
        self,
        task_id: int,
        agent_name: str | None,
        task_data: str | None,
    ) -> None:
        """Esegue un task schedulato: aggiorna last_run e delega a Pepe."""
        # Aggiorna last_run nel DB
        try:
            await self.memory.update_task_last_run(task_id, datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            logger.warning("Errore aggiornamento last_run per task %d: %s", task_id, exc)

        if not self.pepe or not agent_name:
            return

        import json as _json

        input_data = {}
        if task_data:
            try:
                input_data = _json.loads(task_data)
            except Exception:
                input_data = {"raw": task_data}

        # Delega a Pepe tramite handle_user_message o dispatch diretto
        from apps.backend.core.models import AgentTask as _AgentTask

        task = _AgentTask(
            agent_name=agent_name,
            input_data=input_data,
            source="scheduler",
        )
        try:
            await self.pepe.dispatch_task(task)
            logger.info("Task schedulato %d eseguito → %s", task_id, agent_name)
        except Exception as exc:
            logger.error("Errore task schedulato %d: %s", task_id, exc)
