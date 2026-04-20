"""Scheduler — APScheduler AsyncIOScheduler integrato in FastAPI."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_SUBMITTED, EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

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
        finance_agent: Any = None,
        telegram_broadcaster: Callable | None = None,
        screen_watcher: Any = None,
    ) -> None:
        self.memory = memory
        self._ws_broadcast = ws_broadcaster
        self.pepe = pepe
        self.storage = storage
        self.research_agent = research_agent
        self.design_agent = design_agent
        self.publisher_agent = publisher_agent
        self.analytics_agent = analytics_agent
        self.finance_agent = finance_agent
        self._telegram_broadcast = telegram_broadcaster
        self.screen_watcher = screen_watcher
        self._scheduler = AsyncIOScheduler()
        # Track job execution state: job_id → {status, last_run}
        self._job_status: dict[str, dict[str, Any]] = {}
        self._job_status_lock = threading.Lock()
        # Internal jobs we hide from the user-facing scheduler panel
        self._internal_jobs = {"ssd_health_check", "agent_status_sync"}

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Avvia lo scheduler, registra job predefiniti e carica job da DB."""
        self._register_builtin_jobs()
        await self._load_db_jobs()
        # Listen for job lifecycle events
        self._scheduler.add_listener(self._on_job_submitted, EVENT_JOB_SUBMITTED)
        self._scheduler.add_listener(self._on_job_executed, EVENT_JOB_EXECUTED)
        self._scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)
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

        # daily_pipeline, analytics_daily, finance_daily rimossi (Blocco 0 planv2).
        # Pipeline, analytics e finance si avviano SOLO via comandi Telegram:
        # /pipeline, /analytics, /finance

        # Screen cleanup nightly (Blocco 2) — elimina chunk più vecchi di SCREEN_RETENTION_DAYS
        if self.screen_watcher is not None:
            self._scheduler.add_job(
                self._run_screen_cleanup,
                trigger=CronTrigger(hour=3, minute=0),
                id="screen_cleanup",
                name="Screen memory cleanup",
                replace_existing=True,
            )
            logger.info("Job screen_cleanup registrato (03:00 nightly)")

        # Wiki health check domenicale 04:00 — compact + lint + update_index (Step 5.2.4)
        # Eseguito solo se pepe ha l'attributo wiki inizializzato (lifespan Step 5.2.5).
        self._scheduler.add_job(
            self._run_wiki_health_check,
            trigger=CronTrigger(day_of_week="sun", hour=4, minute=0),
            id="wiki_health_check",
            name="Wiki health check",
            replace_existing=True,
        )
        logger.info("Job wiki_health_check registrato (domenica 04:00)")

        # Personal Learning Loop nightly 03:30 (dopo screen_cleanup alle 03:00)
        self._scheduler.add_job(
            self._run_personal_learning_loop,
            trigger=CronTrigger(hour=3, minute=30),
            id="personal_learning_loop",
            name="Personal learning loop",
            replace_existing=True,
        )
        # 2. Reminder checker ogni 2 minuti — invia reminder scaduti
        self._scheduler.add_job(
            self._run_reminder_checker,
            trigger=IntervalTrigger(minutes=settings.REMIND_CHECKER_INTERVAL),
            id="reminder_checker",
            name="Reminder checker",
            replace_existing=True,
        )
        # 3. Unacknowledged reminder ping ogni ora
        self._scheduler.add_job(
            self._run_unack_ping,
            trigger=IntervalTrigger(hours=settings.REMIND_UNACK_PING_HOURS),
            id="reminder_unack_ping",
            name="Reminder unacknowledged ping",
            replace_existing=True,
        )
        # 4. Urgency MEDIUM digest giornaliero
        self._scheduler.add_job(
            self._run_medium_digest,
            trigger=CronTrigger(hour=settings.URGENCY_MEDIUM_DIGEST_HOUR, minute=0),
            id="urgency_medium_digest",
            name="Urgency medium digest",
            replace_existing=True,
        )
        logger.info(
            "Job Personal registrati: personal_learning_loop (03:30), reminder_checker (%dm), "
            "reminder_unack_ping (%dh), urgency_medium_digest (%d:00)",
            settings.REMIND_CHECKER_INTERVAL,
            settings.REMIND_UNACK_PING_HOURS,
            settings.URGENCY_MEDIUM_DIGEST_HOUR,
        )

        logger.info("Job predefiniti registrati (ssd_health_check, agent_status_sync)")

    # ------------------------------------------------------------------
    # Caricamento job da SQLite
    # ------------------------------------------------------------------

    async def _load_db_jobs(self) -> None:
        """Carica scheduled_tasks dal DB e li registra come job APScheduler."""
        try:
            rows = await self.memory.get_enabled_scheduled_tasks()
        except Exception as exc:
            logger.warning("Errore caricamento scheduled_tasks: %s", exc)
            return

        for row_dict in rows:
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

    # ------------------------------------------------------------------
    # Wiki health check — Step 5.2.4
    # ------------------------------------------------------------------

    async def _run_wiki_health_check(self) -> None:
        """Domenicale 04:00 — compact + lint + update_index su entrambi i domini.

        Flusso:
        1. Guard: pepe.wiki deve essere inizializzato (lifespan Step 5.2.5)
        2. Per ciascun dominio ["etsy", "personal"]:
           a. compact_wiki   — distilla file oltre soglia, ritorna {domain, files_compacted}
           b. lint           — wikilinks rotti + raw pending, ritorna report testuale
           c. update_index   — rigenera frontmatter summary: per ogni file wiki
        3. get_stats        — conta file/raw per il report aggregato
        4. Invia report Telegram
        """
        if not self.pepe:
            return
        wiki = getattr(self.pepe, "wiki", None)
        if wiki is None:
            logger.info("wiki_health_check: wiki non inizializzato, skip")
            return

        llm_etsy     = self.pepe.client        # Anthropic Sonnet
        llm_personal = self.pepe._local_client  # Ollama

        domains = [
            ("etsy",     llm_etsy),
            ("personal", llm_personal),
        ]

        compact_totals: dict[str, int] = {}
        lint_reports:   dict[str, str] = {}

        for domain, llm in domains:
            # compact
            try:
                compact_result = await wiki.compact_wiki(domain, llm)
                compact_totals[domain] = compact_result.get("files_compacted", 0)
                logger.info("wiki compact %s: %d file", domain, compact_totals[domain])
            except Exception as exc:
                compact_totals[domain] = -1
                logger.error("wiki_health_check compact %s: %s", domain, exc)

            # lint
            try:
                lint_reports[domain] = await wiki.lint(domain, llm)
            except Exception as exc:
                lint_reports[domain] = f"[errore lint: {exc}]"
                logger.error("wiki_health_check lint %s: %s", domain, exc)

            # update_index
            try:
                await wiki.update_index(domain, llm)
                logger.info("wiki update_index %s: completato", domain)
            except Exception as exc:
                logger.error("wiki_health_check update_index %s: %s", domain, exc)

        # stats aggregate
        try:
            stats = await wiki.get_stats()
        except Exception:
            stats = {}

        # Telegram report
        lines = ["📚 *Wiki health check* completato\n"]
        for domain in ("etsy", "personal"):
            compacted = compact_totals.get(domain, 0)
            symbol = "✅" if compacted >= 0 else "❌"
            lines.append(f"{symbol} *{domain.capitalize()}* — {compacted} file compattati")
            lint = lint_reports.get(domain, "")
            if lint and lint != "OK":
                # Tronca lint report a 300 char per non appesantire il messaggio
                lines.append(f"  ⚠️ Lint: {lint[:300]}")

        etsy_niches    = stats.get("etsy_niches", "?")
        total_raw      = stats.get("total_raw", "?")
        pending_raw    = stats.get("pending_raw", "?")
        lines.append(f"\n📊 Nicchie: {etsy_niches} | Raw totale: {total_raw} | Pending: {pending_raw}")

        report = "\n".join(lines)
        await self._notify_telegram(report)
        logger.info("wiki_health_check completato — report inviato")

    # ------------------------------------------------------------------
    # Personal Learning Loop — job implementations
    # ------------------------------------------------------------------

    async def _run_personal_learning_loop(self) -> None:
        """Nightly 03:30 — learning loop completo in 6 step.

        1. Stop condition: skip se nessuna attività nelle ultime 24h
        2. Decay pattern vecchi
        3. Promuovi topic frequenti (Recall queries ripetute)
        4. Rileva abitudini Watcher (stessa app stesso slot 5+ giorni)
        5. Penalizza reminder ignorati (inviati ma non acked dopo 4h)
        6. Notifica Telegram se > 5 pattern aggiornati
        """
        try:
            # Step 1 — stop condition
            recent_steps = await self.memory.get_agent_steps_count(agent="*", hours=24)
            if recent_steps == 0:
                logger.info("Learning loop: nessuna attività nelle ultime 24h, skip")
                return

            decay_days = settings.LEARNING_DECAY_DAYS
            decay_factor = settings.LEARNING_DECAY_FACTOR

            # Step 2 — decay pattern vecchi
            decayed = await self.memory.decay_old_patterns(
                days=decay_days, factor=decay_factor
            )
            logger.info("Learning loop step 2 — decay: %d pattern aggiornati", decayed)

            # Step 3 — promuovi topic frequenti (Recall)
            try:
                frequent = await self.memory.get_frequent_queries(days=settings.LEARNING_DECAY_DAYS, min_occurrences=3)
                for topic in frequent:
                    await self.memory.upsert_learning(
                        agent="recall",
                        pattern_type="topic",
                        pattern_value=topic,
                        signal_type="implicit_repeated",
                        weight_delta=0.1,
                    )
                logger.info("Learning loop step 3 — topic promossi: %d", len(frequent))
            except Exception as exc:
                logger.warning("Learning loop step 3 fallito: %s", exc)

            # Step 4 — rileva abitudini Watcher
            try:
                habits = await self.memory.detect_watcher_habits(days=7, min_days=5)
                for habit in habits:
                    await self.memory.upsert_learning(
                        agent="urgency",
                        pattern_type="app_habit",
                        pattern_value=habit.get("pattern", ""),
                        signal_type="watcher_habit",
                        weight_delta=0.05,
                    )
                logger.info("Learning loop step 4 — abitudini watcher: %d", len(habits))
            except Exception as exc:
                logger.warning("Learning loop step 4 fallito: %s", exc)

            # Step 5 — penalizza reminder ignorati (inviati, non acked dopo 4h)
            try:
                ignored = await self.memory.get_sent_unacknowledged(hours=4)
                for r in ignored:
                    # Estrai pattern semplice: prima parola del testo reminder
                    text = r.get("text", "")
                    pattern = text.split()[0].lower() if text.split() else "reminder"
                    await self.memory.upsert_learning(
                        agent="remind",
                        pattern_type="reminder_pattern",
                        pattern_value=pattern,
                        signal_type="implicit_ignored",
                        weight_delta=-0.05,
                    )
                logger.info("Learning loop step 5 — reminder ignorati penalizzati: %d", len(ignored))
            except Exception as exc:
                logger.warning("Learning loop step 5 fallito: %s", exc)

            # Step 6 — notifica Telegram se cambiamenti significativi
            if decayed > 5 and self.pepe and hasattr(self.pepe, "notify_telegram"):
                try:
                    await self.pepe.notify_telegram(
                        f"🧠 Learning loop completato: {decayed} pattern aggiornati."
                    )
                except Exception:
                    pass

            logger.info("Learning loop completato — decayed=%d", decayed)

        except Exception as exc:
            logger.error("personal_learning_loop fallito: %s", exc)

    async def _run_reminder_checker(self) -> None:
        """Ogni N minuti — invia reminder scaduti via Telegram."""
        if not self.pepe or not hasattr(self.pepe, "notify_telegram"):
            return
        try:
            due = await self.memory.get_due_reminders()
            if not due:
                return

            for reminder in due:
                rid = reminder.get("id")
                text = reminder.get("text", "")
                recurring = reminder.get("recurring_rule")

                # Invia notifica — usa send_reminder_notification per ottenere message_id (necessario per ACK via reply)
                msg = f"⏰ Reminder: {text}"
                if recurring:
                    msg += f"\n🔄 Ricorrente: {recurring}"
                telegram_msg_id = await self.pepe.send_reminder_notification(msg)

                # Aggiorna stato → sent (telegram_msg_id=0 se bot non configurato)
                await self.memory.mark_reminder_sent(rid, telegram_msg_id)

                # Se ricorrente: ri-schedula prossima occorrenza
                if recurring:
                    await self.memory.reschedule_recurring(rid)

                logger.info("Reminder %d inviato: %s", rid, text[:50])

        except Exception as exc:
            logger.error("reminder_checker fallito: %s", exc)

    async def _run_unack_ping(self) -> None:
        """Ogni N ore — ri-notifica reminder inviati ma non confermati."""
        if not self.pepe or not hasattr(self.pepe, "notify_telegram"):
            return
        try:
            unacked = await self.memory.get_sent_unacknowledged(hours=settings.REMIND_UNACK_PING_HOURS)
            if not unacked:
                return

            for reminder in unacked:
                rid = reminder.get("id")
                text = reminder.get("text", "")
                msg = (
                    f"📌 Reminder non confermato:\n«{text}»\n"
                    f"Rispondi a questo messaggio per confermarlo."
                )
                await self.pepe.notify_telegram(msg)
                logger.info("Unack ping per reminder %d", rid)

        except Exception as exc:
            logger.error("reminder_unack_ping fallito: %s", exc)

    async def _run_medium_digest(self) -> None:
        """Ogni giorno all'ora URGENCY_MEDIUM_DIGEST_HOUR — invia digest MEDIUM e svuota buffer."""
        if not self.pepe or not hasattr(self.pepe, "flush_medium_digest"):
            return
        try:
            await self.pepe.flush_medium_digest()
        except Exception as exc:
            logger.error("urgency_medium_digest fallito: %s", exc)

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Job lifecycle listeners
    # ------------------------------------------------------------------

    def _on_job_submitted(self, event: Any) -> None:
        jid = event.job_id
        if jid not in self._internal_jobs:
            with self._job_status_lock:
                self._job_status[jid] = {"status": "running", "last_run": datetime.now().isoformat()}

    def _on_job_executed(self, event: Any) -> None:
        jid = event.job_id
        if jid not in self._internal_jobs:
            with self._job_status_lock:
                self._job_status[jid] = {"status": "completed", "last_run": datetime.now().isoformat()}

    def _on_job_error(self, event: Any) -> None:
        jid = event.job_id
        if jid not in self._internal_jobs:
            with self._job_status_lock:
                self._job_status[jid] = {"status": "failed", "last_run": datetime.now().isoformat()}

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def get_jobs(self) -> list[dict[str, Any]]:
        """Lista dei job attivi nello scheduler."""
        jobs = []
        for job in self._scheduler.get_jobs():
            jid = job.id
            if jid in self._internal_jobs:
                continue
            with self._job_status_lock:
                info = self._job_status.get(jid, {})
            jobs.append({
                "id": jid,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
                "status": info.get("status", "scheduled"),
                "last_run": info.get("last_run"),
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
        if self.research_agent and self.pepe:
            from apps.backend.core.models import AgentTask as _AgentTask

            research_task = _AgentTask(
                agent_name="research",
                input_data={"niches": [niche]},
                source="scheduler",
            )
            try:
                research_result = await self.pepe.dispatch_task(research_task)
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
            logger.warning("Research agent o Pepe non disponibile, uso keywords vuote")

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
            design_result = await self.pepe.dispatch_task(design_task)
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            logger.error("Design fallito per '%s': %s", niche, exc)
            await self._notify_telegram(f"⚠️ Pipeline: Design fallito per '{niche}': {exc}")
            return

        # 8. Staggered publish: schedula i file spalmati nella giornata
        if not self.publisher_agent:
            logger.warning("Publisher agent non disponibile, pipeline interrotta dopo Design")
            await self._notify_telegram(
                f"✅ Design completato per '{niche}': {len(file_paths)} file in pending/.\n"
                f"⚠️ Publisher non disponibile — pubblicazione manuale richiesta."
            )
            return

        publish_base = {
            "production_queue_task_id": task_id,
            "product_type": "printable_pdf",
            "template": template,
            "niche": niche,
            "color_schemes": brief.get("color_schemes", []),
            "keywords": keywords,
            "size": brief.get("size", "A4"),
        }
        await self._schedule_staggered_publish(
            file_paths=file_paths,
            publish_base=publish_base,
            task_id=task_id,
            niche=niche,
            design_cost=cost,
        )

    # ------------------------------------------------------------------
    # Staggered publish
    # ------------------------------------------------------------------

    async def _schedule_staggered_publish(
        self,
        file_paths: list[str],
        publish_base: dict,
        task_id: str,
        niche: str,
        design_cost: float,
    ) -> None:
        """Spalma la pubblicazione dei file nell'arco della giornata.

        Finestra: ora + 15 min → 17:30.
        Se la finestra è < 30 min o c'è un solo file, pubblica tutto subito.
        Altrimenti divide equamente l'intervallo tra i file.
        """
        now = datetime.now()
        window_start = now + timedelta(minutes=15)
        window_end = now.replace(hour=17, minute=30, second=0, microsecond=0)
        window_minutes = max((window_end - window_start).total_seconds() / 60, 0)

        n = len(file_paths)
        if n == 0:
            logger.warning("Nessun file da pubblicare per '%s'", niche)
            return

        if n == 1 or window_minutes < 30:
            # Batch unico, subito
            run_time = window_start if window_minutes >= 0 else now + timedelta(minutes=5)
            self._scheduler.add_job(
                self._publish_staggered_job,
                trigger=DateTrigger(run_date=run_time),
                id=f"pub_{task_id}_0",
                name=f"Publish 1/1 — {niche[:30]}",
                replace_existing=True,
                kwargs={
                    "file_paths": file_paths,
                    "publish_base": publish_base,
                    "niche": niche,
                    "design_cost": design_cost,
                    "job_index": 0,
                    "total_jobs": 1,
                },
            )
            logger.info(
                "Publish unico schedulato alle %s per '%s' (%d file)",
                run_time.strftime("%H:%M"), niche, n,
            )
            await self._notify_telegram(
                f"✅ Design completato per '{niche}' ({n} file).\n"
                f"📅 Pubblicazione in coda alle {run_time.strftime('%H:%M')}."
            )
            return

        # Intervallo equo tra i file
        interval_minutes = window_minutes / (n - 1)
        run_times: list[datetime] = []
        for i in range(n):
            rt = window_start + timedelta(minutes=i * interval_minutes)
            run_times.append(rt)
            self._scheduler.add_job(
                self._publish_staggered_job,
                trigger=DateTrigger(run_date=rt),
                id=f"pub_{task_id}_{i}",
                name=f"Publish {i+1}/{n} — {niche[:30]}",
                replace_existing=True,
                kwargs={
                    "file_paths": [file_paths[i]],
                    "publish_base": publish_base,
                    "niche": niche,
                    "design_cost": design_cost if i == 0 else 0.0,
                    "job_index": i,
                    "total_jobs": n,
                },
            )
            logger.info(
                "Publish %d/%d schedulato alle %s per '%s'",
                i + 1, n, rt.strftime("%H:%M"), niche,
            )

        await self._notify_telegram(
            f"✅ Design completato per '{niche}' ({n} varianti).\n"
            f"📅 Pubblicazione distribuita: {run_times[0].strftime('%H:%M')} → "
            f"{run_times[-1].strftime('%H:%M')}."
        )

    async def _publish_staggered_job(
        self,
        file_paths: list[str],
        publish_base: dict,
        niche: str,
        design_cost: float,
        job_index: int,
        total_jobs: int,
    ) -> None:
        """Eseguito da DateTrigger: pubblica un batch di file tramite Publisher Agent."""
        if not self.publisher_agent:
            logger.warning("Publisher agent non disponibile per job staggered %d/%d", job_index + 1, total_jobs)
            return

        from apps.backend.core.models import AgentTask as _AgentTask

        publish_input = {**publish_base, "file_paths": file_paths}
        publish_task = _AgentTask(
            agent_name="publisher",
            input_data=publish_input,
            source="scheduler",
        )
        try:
            result = await self.pepe.dispatch_task(publish_task) if self.pepe else await self.publisher_agent.execute(publish_task)
            pub_out = result.output_data or {}
            listings_created = pub_out.get("listings_created", 0)
            total_cost = design_cost + result.cost_usd
            logger.info(
                "Publish %d/%d completato: %d listing creati per '%s' (costo $%.4f)",
                job_index + 1, total_jobs, listings_created, niche, total_cost,
            )
            await self._broadcast({
                "type": "system_status",
                "event": "pipeline_publish_completed",
                "niche": niche,
                "job_index": job_index,
                "total_jobs": total_jobs,
                "listings_created": listings_created,
                "cost_usd": total_cost,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            if job_index == total_jobs - 1:
                await self._notify_telegram(
                    f"🎉 Pipeline completata per '{niche}': tutti i listing pubblicati."
                )
        except Exception as exc:
            logger.error(
                "Publish staggered %d/%d fallito per '%s': %s",
                job_index + 1, total_jobs, niche, exc,
            )
            await self._notify_telegram(
                f"❌ Publish {job_index + 1}/{total_jobs} fallito per '{niche}': {exc}\n"
                f"I file sono in pending/ — riprovo domani."
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
            dispatcher = self.pepe.dispatch_task if self.pepe else self.analytics_agent.execute
            await dispatcher(task)
            logger.info("Analytics giornaliero completato")
        except Exception as exc:
            logger.error("Analytics fallito: %s", exc)
            await self._notify_telegram(f"⚠️ Analytics giornaliero fallito: {exc}")

    async def _run_finance(self) -> None:
        """Job giornaliero finance: costi, margini, ROI per nicchia."""
        if not self.finance_agent:
            logger.warning("Finance agent non disponibile, skip")
            return

        from apps.backend.core.models import AgentTask as _AgentTask

        task = _AgentTask(
            agent_name="finance",
            input_data={"period_days": 30},
            source="scheduler",
        )
        try:
            dispatcher = self.pepe.dispatch_task if self.pepe else self.finance_agent.execute
            await dispatcher(task)
            logger.info("Finance report giornaliero completato")
        except Exception as exc:
            logger.error("Finance report fallito: %s", exc)
            await self._notify_telegram(f"⚠️ Finance report fallito: {exc}")

    async def _pick_niche(self) -> str | None:
        """Sceglie la prossima nicchia da produrre.

        Deduplicazione a tre livelli:
        1. production_queue — nicchie pianificate/in-corso/completate negli ultimi 7 giorni
        2. etsy_listings    — nicchie già pubblicate su Etsy (via is_duplicate_product)
        3. Fallback pool    — nicchie di default se ChromaDB è vuoto
        """
        # 1. Pool nicchie da ChromaDB insights
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

        # 2. Nicchie recenti in production_queue (ultimi 7 giorni)
        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        recent = await self.memory.get_production_queue(status=None, limit=100)
        blocked_niches: set[str] = {
            item["niche"]
            for item in recent
            if item.get("created_at", "") >= seven_days_ago
            and item.get("status") in ("completed", "in_progress", "planned")
        }

        # 3. Nicchie già pubblicate su Etsy — dedup completo
        for niche in niches_pool:
            if niche in blocked_niches:
                continue
            try:
                if await self.memory.is_duplicate_product(niche, "printable_pdf"):
                    blocked_niches.add(niche)
                    logger.debug("Niche '%s' già in etsy_listings, skip", niche)
            except Exception as exc:
                logger.debug("Errore check etsy_listings per '%s': %s", niche, exc)

        for niche in niches_pool:
            if niche not in blocked_niches:
                return niche

        logger.info("Tutte le nicchie già prodotte o pubblicate su Etsy")
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
