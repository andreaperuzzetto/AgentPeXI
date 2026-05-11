"""Scheduler — core mixin: init, start/stop, job registry, lifecycle, API helpers."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_SUBMITTED, EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

from apps.backend.core.config import settings
from apps.backend.core.memory import MemoryManager

logger = logging.getLogger("agentpexi.scheduler")


def _extract_color_schemes(color_hint: str) -> list[str]:
    """Converte un color_palette_hint testuale di Research in nomi di scheme usabili da Design.

    Esempi:
      "sage green, warm beige, dusty pink"  → ["sage", "beige", "blush"]
      ""                                    → []  (chiamante usa default)
    """
    if not color_hint:
        return []
    # Mapping parole chiave → scheme name usato da DesignAgent
    _map = {
        "sage": "sage", "green": "sage", "mint": "sage",
        "beige": "beige", "warm": "beige", "tan": "beige", "cream": "beige",
        "pink": "blush", "blush": "blush", "rose": "blush", "dusty": "blush",
        "slate": "slate", "grey": "slate", "gray": "slate", "blue": "slate",
        "white": "minimal", "minimal": "minimal", "clean": "minimal",
        "warm beige": "beige", "warm white": "minimal",
        "neutral": "beige", "pastel": "blush",
        "dark": "slate", "charcoal": "slate",
    }
    schemes: list[str] = []
    seen: set[str] = set()
    hint_lower = color_hint.lower()
    for keyword, scheme in _map.items():
        if keyword in hint_lower and scheme not in seen:
            seen.add(scheme)
            schemes.append(scheme)
    return schemes[:3] or []


class _CoreMixin:
    """Core lifecycle, job registry, event listeners, and broadcast helpers."""

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
        # Blocco 2 — Autonomy Layer
        production_queue: Any = None,
        budget_manager: Any = None,
        publication_policy: Any = None,
        autopilot_loop: Any = None,
        etsy_client: Any = None,
        # Blocco 5 — Shop Intelligence
        shop_optimizer: Any = None,
        etsy_ads_manager: Any = None,
        # Blocco 4 / 5.3 — LearningLoop (A/B thumbnail comparison)
        learning_loop: Any = None,
        # Blocco B — Pinterest Machine
        pinterest_agent: Any = None,
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
        # Blocco 2
        self.production_queue  = production_queue
        self.budget_manager    = budget_manager
        self.publication_policy = publication_policy
        self.autopilot_loop    = autopilot_loop
        self.etsy_client       = etsy_client
        # Blocco 5
        self.shop_optimizer    = shop_optimizer
        self.etsy_ads_manager  = etsy_ads_manager
        # Blocco 4 / 5.3
        self.learning_loop     = learning_loop
        # Blocco B — Pinterest Machine
        self.pinterest_agent   = pinterest_agent
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
        if self._scheduler.running:
            return
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

        # Blocco 2 — publish checker ogni 15 minuti
        self._scheduler.add_job(
            self._run_publish_checker,
            trigger=IntervalTrigger(minutes=15),
            id="publish_checker",
            name="Publish checker (B2)",
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )

        # Blocco 4 — polling performance listing ogni 6 ore
        if self.analytics_agent is not None:
            self._scheduler.add_job(
                self._run_poll_listing_performance,
                trigger=IntervalTrigger(hours=6),
                id="analytics_poll",
                name="Polling performance listing (B4)",
                replace_existing=True,
            )
            logger.info("Job analytics_poll registrato (ogni 6h)")

        # Blocco 5 — Etsy Ads auto-manager ogni 6h (parallelo ad analytics_poll)
        if self.etsy_ads_manager is not None:
            self._scheduler.add_job(
                self._run_etsy_ads_manager,
                trigger=IntervalTrigger(hours=6),
                id="etsy_ads_manager",
                name="Etsy Ads auto-manager (B5)",
                replace_existing=True,
            )
            logger.info("Job etsy_ads_manager registrato (ogni 6h)")

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

        # Blocco 5 — Shop profile optimizer ogni lunedì 07:00
        if self.shop_optimizer is not None:
            self._scheduler.add_job(
                self._run_shop_optimizer_job,
                trigger=CronTrigger(day_of_week="mon", hour=7, minute=0),
                id="shop_optimizer",
                name="Shop profile optimizer (B5)",
                replace_existing=True,
            )
            logger.info("Job shop_optimizer registrato (lunedì 07:00)")

        # A.1 — Empty sections check giornaliero 09:00 (sezioni inattive >60gg)
        self._scheduler.add_job(
            self._check_empty_sections,
            trigger=CronTrigger(hour=9, minute=0),
            id="empty_sections_check",
            name="Empty sections check (A.1)",
            replace_existing=True,
        )
        logger.info("Job empty_sections_check registrato (09:00 daily)")

        # Etsy learning loop domenicale 02:00 — analytics + finance aggiornano i segnali ChromaDB
        # (design_winner, niche_roi_snapshot, finance_directive, finance_insight)
        self._scheduler.add_job(
            self._run_etsy_learning_loop,
            trigger=CronTrigger(day_of_week="sun", hour=2, minute=0),
            id="etsy_learning_loop",
            name="Etsy learning loop",
            replace_existing=True,
        )
        logger.info("Job etsy_learning_loop registrato (domenica 02:00)")

        # shared_memory decay domenicale 03:45 — elimina insight cross-domain >SHARED_MEMORY_DECAY_DAYS
        self._scheduler.add_job(
            self._run_shared_memory_decay,
            trigger=CronTrigger(day_of_week="sun", hour=3, minute=45),
            id="shared_memory_decay",
            name="Shared memory decay",
            replace_existing=True,
        )
        logger.info("Job shared_memory_decay registrato (domenica 03:45)")

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

        # Blocco B — Pinterest publisher ogni 15 minuti
        self._scheduler.add_job(
            self._run_pinterest_publisher,
            trigger=IntervalTrigger(minutes=15),
            id="pinterest_publisher",
            name="Pinterest publisher (B-07)",
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        logger.info("Job pinterest_publisher registrato (ogni 15min)")

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
    # Job lifecycle listeners
    # ------------------------------------------------------------------

    def _on_job_submitted(self, event: Any) -> None:
        jid = event.job_id
        if jid not in self._internal_jobs:
            with self._job_status_lock:
                self._job_status[jid] = {"status": "running", "last_run": datetime.now(timezone.utc).isoformat()}

    def _on_job_executed(self, event: Any) -> None:
        jid = event.job_id
        if jid not in self._internal_jobs:
            with self._job_status_lock:
                self._job_status[jid] = {"status": "completed", "last_run": datetime.now(timezone.utc).isoformat()}

    def _on_job_error(self, event: Any) -> None:
        jid = event.job_id
        logger.exception(
            "APScheduler job %s failed", jid, exc_info=event.exception
        )
        if jid not in self._internal_jobs:
            with self._job_status_lock:
                self._job_status[jid] = {"status": "failed", "last_run": datetime.now(timezone.utc).isoformat()}

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def get_jobs(self) -> list[dict[str, Any]]:
        """Lista dei job attivi nello scheduler."""
        snapshot = dict(self._job_status)
        try:
            job_snapshot = list(self._scheduler.get_jobs())
        except Exception:
            return []
        jobs = []
        for job in job_snapshot:
            jid = job.id
            if jid in self._internal_jobs:
                continue
            info = snapshot.get(jid, {})
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
    # Broadcast helpers
    # ------------------------------------------------------------------

    async def _broadcast(self, event: dict[str, Any]) -> None:
        if callable(self._ws_broadcast):
            try:
                await self._ws_broadcast(event)
            except Exception:
                logger.exception("Unexpected error")

    async def _notify_telegram(self, message: str) -> None:
        """Invia notifica via Telegram (se broadcaster disponibile)."""
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(message)
            except Exception:
                logger.exception("Unexpected error")
        elif self.pepe and hasattr(self.pepe, "notify_telegram"):
            try:
                await self.pepe.notify_telegram(message, priority=True)
            except Exception:
                logger.exception("Unexpected error")
