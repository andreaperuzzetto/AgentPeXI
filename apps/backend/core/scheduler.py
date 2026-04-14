"""Scheduler — APScheduler AsyncIOScheduler integrato in FastAPI."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Callable, Coroutine

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from apps.backend.core.config import settings
from apps.backend.core.memory import MemoryManager

logger = logging.getLogger("agentpexi.scheduler")


class Scheduler:
    """Gestione job schedulati con APScheduler (AsyncIO)."""

    def __init__(
        self,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        pepe: Any = None,
    ) -> None:
        self.memory = memory
        self._ws_broadcast = ws_broadcaster
        self.pepe = pepe
        self._scheduler = AsyncIOScheduler()

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Avvia lo scheduler, registra job predefiniti e carica job da DB."""
        self._register_builtin_jobs()
        await self._load_db_jobs()
        self._scheduler.start()
        logger.info("Scheduler avviato")

    async def stop(self) -> None:
        """Ferma lo scheduler gracefully."""
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler fermato")

    # ------------------------------------------------------------------
    # Job predefiniti
    # ------------------------------------------------------------------

    def _register_builtin_jobs(self) -> None:
        """Registra i job di sistema."""
        # Health check SSD ogni 5 minuti
        self._scheduler.add_job(
            self._health_check_ssd,
            trigger=IntervalTrigger(minutes=5),
            id="ssd_health_check",
            name="Health check SSD",
            replace_existing=True,
        )

        # Sync stato agenti ogni 30 secondi (broadcast WebSocket)
        self._scheduler.add_job(
            self._sync_agent_status,
            trigger=IntervalTrigger(seconds=30),
            id="agent_status_sync",
            name="Sync stato agenti",
            replace_existing=True,
        )

        logger.info("Job predefiniti registrati (ssd_health_check, agent_status_sync)")

    # ------------------------------------------------------------------
    # Caricamento job da SQLite
    # ------------------------------------------------------------------

    async def _load_db_jobs(self) -> None:
        """Carica scheduled_tasks dal DB e li registra come job APScheduler."""
        try:
            cursor = await self.memory._db.execute(
                "SELECT id, name, cron_expression, agent_name, task_data, enabled "
                "FROM scheduled_tasks WHERE enabled = 1"
            )
            rows = await cursor.fetchall()
        except Exception as exc:
            logger.warning("Errore caricamento scheduled_tasks: %s", exc)
            return

        for row in rows:
            row_dict = dict(row)
            cron_expr = row_dict.get("cron_expression")
            if not cron_expr:
                continue

            job_id = f"db_task_{row_dict['id']}"
            try:
                trigger = CronTrigger.from_crontab(cron_expr)
                self._scheduler.add_job(
                    self._run_scheduled_task,
                    trigger=trigger,
                    id=job_id,
                    name=row_dict.get("name", job_id),
                    replace_existing=True,
                    kwargs={
                        "task_id": row_dict["id"],
                        "agent_name": row_dict.get("agent_name"),
                        "task_data": row_dict.get("task_data"),
                    },
                )
                logger.info("Job DB caricato: %s (%s)", row_dict["name"], cron_expr)
            except Exception as exc:
                logger.warning("Job DB %s non valido: %s", job_id, exc)

        logger.info("Caricati %d job da DB", len(rows))

    # ------------------------------------------------------------------
    # Implementazione job predefiniti
    # ------------------------------------------------------------------

    async def _health_check_ssd(self) -> None:
        """Verifica che STORAGE_PATH sia montato e accessibile."""
        storage = settings.STORAGE_PATH
        ok = os.path.isdir(storage)

        if not ok:
            msg = f"⚠️ STORAGE_PATH non accessibile: {storage}"
            logger.error(msg)
            if self.pepe and hasattr(self.pepe, "notify_telegram"):
                await self.pepe.notify_telegram(msg, priority=True)
            await self._broadcast({
                "type": "system_status",
                "event": "ssd_offline",
                "storage_path": storage,
                "timestamp": datetime.utcnow().isoformat(),
            })
        else:
            # Verifica spazio disponibile
            try:
                stat = os.statvfs(storage)
                free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
                if free_gb < 1.0:
                    msg = f"⚠️ Spazio SSD basso: {free_gb:.1f} GB rimasti"
                    logger.warning(msg)
                    if self.pepe and hasattr(self.pepe, "notify_telegram"):
                        await self.pepe.notify_telegram(msg, priority=True)
            except Exception:
                pass

    async def _sync_agent_status(self) -> None:
        """Broadcast stato agenti via WebSocket."""
        if not self.pepe:
            return

        statuses = self.pepe.get_agent_statuses()
        queue_size = self.pepe._queue.qsize() if hasattr(self.pepe, "_queue") else 0

        await self._broadcast({
            "type": "system_status",
            "event": "agent_sync",
            "agents": statuses,
            "queue_size": queue_size,
            "timestamp": datetime.utcnow().isoformat(),
        })

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
            await self.memory._db.execute(
                "UPDATE scheduled_tasks SET last_run = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), task_id),
            )
            await self.memory._db.commit()
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

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def get_jobs(self) -> list[dict[str, Any]]:
        """Lista dei job attivi nello scheduler."""
        jobs = []
        for job in self._scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            })
        return jobs

    # ------------------------------------------------------------------
    # Broadcast helper
    # ------------------------------------------------------------------

    async def _broadcast(self, event: dict[str, Any]) -> None:
        if self._ws_broadcast:
            try:
                await self._ws_broadcast(event)
            except Exception:
                pass
