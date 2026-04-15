"""Scheduler — APScheduler AsyncIOScheduler integrato in FastAPI."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta
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
        storage: Any = None,
        research_agent: Any = None,
        design_agent: Any = None,
        publisher_agent: Any = None,
        analytics_agent: Any = None,
        telegram_broadcaster: Callable | None = None,
    ) -> None:
        self.memory = memory
        self._ws_broadcast = ws_broadcaster
        self.pepe = pepe
        self.storage = storage
        self.research_agent = research_agent
        self.design_agent = design_agent
        self.publisher_agent = publisher_agent
        self.analytics_agent = analytics_agent
        self._telegram_broadcast = telegram_broadcaster
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

        # Catch-up: se la pipeline di oggi non è ancora stata eseguita
        asyncio.create_task(self._check_catchup())

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

        # Pipeline giornaliera Research → Design → Publish alle 09:00
        self._scheduler.add_job(
            self._run_pipeline,
            trigger=CronTrigger(hour=9, minute=0),
            id="daily_pipeline",
            name="Pipeline giornaliera Research → Design → Publish",
            replace_existing=True,
        )

        # Analytics giornaliero alle 08:00
        self._scheduler.add_job(
            self._run_analytics,
            trigger=CronTrigger(hour=8, minute=0),
            id="analytics_daily",
            name="Analytics giornaliero",
            replace_existing=True,
        )

        logger.info("Job predefiniti registrati (ssd_health_check, agent_status_sync, daily_pipeline, analytics_daily)")

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
        """Verifica che STORAGE_PATH sia montato e accessibile tramite StorageManager."""
        if not self.storage:
            # Fallback senza StorageManager
            storage = settings.STORAGE_PATH
            ok = os.path.isdir(storage)
            if not ok:
                msg = f"⚠️ STORAGE_PATH non accessibile: {storage}"
                logger.error(msg)
                if self.pepe and hasattr(self.pepe, "notify_telegram"):
                    await self.pepe.notify_telegram(msg, priority=True)
            return

        health = self.storage.health_check()

        if not health["available"]:
            msg = f"⚠️ STORAGE_PATH non accessibile: {settings.STORAGE_PATH}"
            logger.error(msg)
            if self.pepe and hasattr(self.pepe, "notify_telegram"):
                await self.pepe.notify_telegram(msg, priority=True)
            await self._broadcast({
                "type": "system_status",
                "event": "ssd_offline",
                "storage_path": settings.STORAGE_PATH,
                "timestamp": datetime.utcnow().isoformat(),
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
                "timestamp": datetime.utcnow().isoformat(),
            })

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
    # Pipeline giornaliera Research → Design
    # ------------------------------------------------------------------

    _DEFAULT_NICHES = [
        "minimalist weekly planner",
        "habit tracker pastel",
        "budget planner printable",
        "daily journal clean design",
        "meal planner weekly",
    ]

    async def _check_catchup(self) -> None:
        """Al boot, verifica se la pipeline di oggi è già stata eseguita.
        Se no e siamo nella finestra 09:00-18:00, lancia dopo 2 minuti."""
        now = datetime.now()
        if now.hour < 9 or now.hour >= 18:
            return
        today_str = now.strftime("%Y-%m-%d")
        today_items = await self.memory.get_production_queue(status=None, limit=20)
        ran_today = any(
            item.get("created_at", "").startswith(today_str) for item in today_items
        )
        if not ran_today:
            logger.info("Catch-up pipeline: nessuna produzione oggi, lancio tra 2 minuti")
            await asyncio.sleep(120)
            await self._run_pipeline()

    async def _run_pipeline(self) -> None:
        """Pipeline Research → Design. Si ferma dopo Design (Publisher in fase successiva)."""
        now = datetime.now()

        # 1. Verifica finestra oraria
        if now.hour < 9 or now.hour >= 18:
            logger.info("Pipeline fuori finestra oraria (09-18), skip")
            return

        # 2. Verifica storage
        if self.storage and not self.storage.is_available():
            msg = "⚠️ Pipeline abortita: storage non disponibile"
            logger.error(msg)
            await self._notify_telegram(msg)
            return

        # 3. Scegli nicchia
        niche = await self._pick_niche()
        if not niche:
            logger.info("Pipeline: nessuna nicchia disponibile (tutte già prodotte), skip")
            return

        logger.info("Pipeline avviata per nicchia: %s", niche)

        # 4. Research Agent
        keywords: list[str] = []
        if self.research_agent:
            from apps.backend.core.models import AgentTask as _AgentTask

            research_task = _AgentTask(
                agent_name="research",
                input_data={"niches": [niche]},
                source="scheduler",
            )
            try:
                research_result = await self.research_agent.execute(research_task)
                out = research_result.output_data or {}
                # Estrai keywords dal report
                niches_data = out.get("niches", [])
                if niches_data and isinstance(niches_data, list):
                    keywords = niches_data[0].get("keywords", [])
                logger.info("Research completato per '%s': %d keywords", niche, len(keywords))
            except Exception as exc:
                logger.error("Research fallito per '%s': %s", niche, exc)
                await self._notify_telegram(f"⚠️ Pipeline: Research fallito per '{niche}': {exc}")
                return
        else:
            logger.warning("Research agent non disponibile, uso keywords vuote")

        # 5. Costruisci brief per Design
        template = self._pick_template(niche)
        task_id = str(uuid.uuid4())

        brief = {
            "niche": niche,
            "product_type": "printable_pdf",
            "template": template,
            "size": "A4",
            "num_variants": 3,
            "color_schemes": ["sage", "blush", "slate"],
            "keywords": keywords,
            "production_queue_task_id": task_id,
        }

        # 6. Aggiungi in production_queue
        await self.memory.add_to_production_queue(
            task_id=task_id,
            product_type="printable_pdf",
            niche=niche,
            brief=brief,
        )

        # 7. Design Agent
        if not self.design_agent:
            logger.warning("Design agent non disponibile, pipeline interrotta dopo queue insert")
            return

        from apps.backend.core.models import AgentTask as _AgentTask

        design_task = _AgentTask(
            agent_name="design",
            input_data=brief,
            source="scheduler",
            task_id=task_id,
        )

        try:
            design_result = await self.design_agent.execute(design_task)
            file_paths = design_result.output_data.get("file_paths", [])
            cost = design_result.cost_usd
            logger.info(
                "Design completato: %d file generati, costo $%.4f",
                len(file_paths), cost,
            )
            await self._broadcast({
                "type": "system_status",
                "event": "pipeline_design_completed",
                "niche": niche,
                "template": template,
                "files_count": len(file_paths),
                "cost_usd": cost,
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as exc:
            logger.error("Design fallito per '%s': %s", niche, exc)
            await self._notify_telegram(f"⚠️ Pipeline: Design fallito per '{niche}': {exc}")
            return

        # 8. Publisher Agent
        if not self.publisher_agent:
            logger.warning("Publisher agent non disponibile, pipeline interrotta dopo Design")
            await self._notify_telegram(
                f"✅ Pipeline Design completata per '{niche}': {len(file_paths)} file in pending/.\n"
                f"⚠️ Publisher non disponibile — pubblicazione manuale richiesta."
            )
            return

        publish_input = {
            "production_queue_task_id": task_id,
            "file_paths": file_paths,
            "product_type": "printable_pdf",
            "template": template,
            "niche": niche,
            "color_schemes": brief.get("color_schemes", []),
            "keywords": keywords,
            "size": brief.get("size", "A4"),
        }

        from apps.backend.core.models import AgentTask as _AgentTask

        publish_task = _AgentTask(
            agent_name="publisher",
            input_data=publish_input,
            source="scheduler",
        )

        try:
            publish_result = await self.publisher_agent.execute(publish_task)
            pub_out = publish_result.output_data or {}
            listings_created = pub_out.get("listings_created", 0)
            logger.info(
                "Pipeline completata: %d listing creati, costo totale $%.4f",
                listings_created, cost + publish_result.cost_usd,
            )
            await self._broadcast({
                "type": "system_status",
                "event": "pipeline_completed",
                "niche": niche,
                "template": template,
                "files_count": len(file_paths),
                "listings_created": listings_created,
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as exc:
            logger.error("Publisher fallito per '%s': %s", niche, exc)
            await self._notify_telegram(
                f"❌ Publisher fallito: {exc}. I file sono in pending/ — riprovo domani."
            )

    async def _run_analytics(self) -> None:
        """Job giornaliero analytics: sync stats + failure analysis + report."""
        if not self.analytics_agent:
            logger.warning("Analytics agent non disponibile, skip")
            return

        from apps.backend.core.models import AgentTask as _AgentTask

        task = _AgentTask(
            agent_name="analytics",
            input_data={},
            source="scheduler",
        )
        try:
            await self.analytics_agent.execute(task)
            logger.info("Analytics giornaliero completato")
        except Exception as exc:
            logger.error("Analytics fallito: %s", exc)
            await self._notify_telegram(f"⚠️ Analytics giornaliero fallito: {exc}")

    async def _pick_niche(self) -> str | None:
        """Sceglie la prossima nicchia da produrre."""
        # Prova ChromaDB per insights recenti
        niches_pool: list[str] = []
        try:
            insights = await self.memory.query_insights("etsy niche trending", n_results=5)
            for ins in insights:
                doc = ins.get("document", "")
                if doc:
                    niches_pool.append(doc.split("\n")[0][:80])
        except Exception:
            pass

        if not niches_pool:
            niches_pool = list(self._DEFAULT_NICHES)

        # Filtra nicchie già prodotte negli ultimi 7 giorni
        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        recent = await self.memory.get_production_queue(status=None, limit=100)

        recent_niches = {
            item["niche"]
            for item in recent
            if item.get("created_at", "") >= seven_days_ago
            and item.get("status") in ("completed", "in_progress", "planned")
        }

        for niche in niches_pool:
            if niche not in recent_niches:
                return niche
        return None

    @staticmethod
    def _pick_template(niche: str) -> str:
        """Regola semplice: inferisci template dal nome della nicchia."""
        n = niche.lower()
        if "habit" in n:
            return "habit_tracker"
        if "budget" in n:
            return "budget_sheet"
        if "journal" in n or "diary" in n:
            return "daily_journal"
        return "weekly_planner"

    async def _notify_telegram(self, message: str) -> None:
        """Invia notifica via Telegram (se broadcaster disponibile)."""
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(message)
            except Exception:
                pass
        elif self.pepe and hasattr(self.pepe, "notify_telegram"):
            try:
                await self.pepe.notify_telegram(message, priority=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Broadcast helper
    # ------------------------------------------------------------------

    async def _broadcast(self, event: dict[str, Any]) -> None:
        if self._ws_broadcast:
            try:
                await self._ws_broadcast(event)
            except Exception:
                pass
