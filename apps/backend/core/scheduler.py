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

        # Blocco 2 — publish checker ogni 15 minuti
        self._scheduler.add_job(
            self._run_publish_checker,
            trigger=IntervalTrigger(minutes=15),
            id="publish_checker",
            name="Publish checker (B2)",
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

        compact_totals: dict[str, int]  = {}
        orphan_stats:   dict[str, dict] = {}
        lint_reports:   dict[str, str]  = {}

        for domain, llm in domains:
            # 0. Orphan raw cleanup (Block 5) — prima del lint per avere report aggiornato
            try:
                orphan_stats[domain] = await wiki.cleanup_orphan_raw(domain, llm)
                logger.info(
                    "wiki_health_check orphan_cleanup %s: compiled=%d deleted=%d skipped=%d errors=%d",
                    domain,
                    orphan_stats[domain]["compiled"],
                    orphan_stats[domain]["deleted"],
                    orphan_stats[domain]["skipped"],
                    len(orphan_stats[domain]["errors"]),
                )
            except Exception as exc:
                orphan_stats[domain] = {"compiled": 0, "deleted": 0, "skipped": 0, "errors": [str(exc)]}
                logger.error("wiki_health_check orphan_cleanup %s: %s", domain, exc)

            # 1. compact
            try:
                compact_result = await wiki.compact_wiki(domain, llm)
                compact_totals[domain] = compact_result.get("files_compacted", 0)
                logger.info("wiki compact %s: %d file", domain, compact_totals[domain])
            except Exception as exc:
                compact_totals[domain] = -1
                logger.error("wiki_health_check compact %s: %s", domain, exc)

            # 2. lint
            try:
                lint_reports[domain] = await wiki.lint(domain, llm)
            except Exception as exc:
                lint_reports[domain] = f"[errore lint: {exc}]"
                logger.error("wiki_health_check lint %s: %s", domain, exc)

            # 3. update_index
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

            # Orphan cleanup summary
            ost = orphan_stats.get(domain, {})
            compiled_n = ost.get("compiled", 0)
            deleted_n  = ost.get("deleted",  0)
            errors_n   = len(ost.get("errors", []))
            if compiled_n or deleted_n or errors_n:
                orphan_line = f"  🧹 Orfani: {compiled_n} compilati, {deleted_n} eliminati"
                if errors_n:
                    orphan_line += f", {errors_n} errori"
                lines.append(orphan_line)

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

            # Step 6 — sintesi settimanale personal_memory (max 1 ogni 6 giorni)
            synthesis_generated = False
            try:
                synthesis_generated = await self._run_weekly_personal_synthesis()
            except Exception as exc:
                logger.warning("Learning loop step 6 (weekly synthesis) fallito: %s", exc)

            # Step 7 — notifica Telegram se cambiamenti significativi
            if (decayed > 5 or synthesis_generated) and self.pepe and hasattr(self.pepe, "notify_telegram"):
                try:
                    msg = f"🧠 Learning loop completato: {decayed} pattern aggiornati."
                    if synthesis_generated:
                        msg += "\n📝 Sintesi settimanale personal_memory generata."
                    await self.pepe.notify_telegram(msg)
                except Exception:
                    pass

            logger.info("Learning loop completato — decayed=%d synthesis=%s", decayed, synthesis_generated)

        except Exception as exc:
            logger.error("personal_learning_loop fallito: %s", exc)

    async def _run_weekly_personal_synthesis(self) -> bool:
        """Aggrega gli insight personal_memory degli ultimi 7gg in una sintesi settimanale.

        Gira ogni notte (chiamata da _run_personal_learning_loop) ma produce output
        al massimo una volta ogni 6 giorni — evita duplicati di settimane sovrapposte.

        Requisiti:
        - almeno 5 insight negli ultimi 7gg (escluse le stesse weekly_synthesis)
        - nessuna weekly_synthesis scritta negli ultimi 6 giorni

        LLM: Haiku via self.pepe.client.
        Ritorna True se la sintesi è stata generata e scritta, False altrimenti.
        """
        if not self.pepe or not hasattr(self.pepe, "client"):
            return False

        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        from apps.backend.core.config import MODEL_HAIKU

        now       = _dt.now(_tz.utc)
        cutoff_7d = (now - _td(days=7)).strftime("%Y-%m-%d")
        cutoff_6d = (now - _td(days=6)).strftime("%Y-%m-%d")

        # Guard: evita duplicate settimanali
        try:
            recent_check = await self.memory.query_personal_memory(
                query="sintesi settimanale",
                n_results=3,
                where={"date": {"$gte": cutoff_6d}},
                agent="scheduler",
            )
            if any(
                i.get("metadata", {}).get("type") == "weekly_synthesis"
                for i in (recent_check or [])
            ):
                logger.debug("weekly_personal_synthesis: già presente questa settimana, skip")
                return False
        except Exception as exc:
            logger.debug("weekly_personal_synthesis guard fallito (skip): %s", exc)
            return False

        # Fetch insight ultimi 7 giorni
        try:
            raw = await self.memory.query_personal_memory(
                query="apprendimento ricerca ricordi topic personale",
                n_results=30,
                where={"date": {"$gte": cutoff_7d}},
                agent="scheduler",
            )
        except Exception as exc:
            logger.warning("weekly_personal_synthesis: query fallita: %s", exc)
            return False

        # Filtra weekly_synthesis in Python (evita $ne quirks ChromaDB)
        insights = [
            i for i in (raw or [])
            if i.get("metadata", {}).get("type") != "weekly_synthesis"
        ]

        if len(insights) < 5:
            logger.debug(
                "weekly_personal_synthesis: %d insight (soglia 5), skip",
                len(insights),
            )
            return False

        # Costruisci testo aggregato — max 20 doc, 300 chars ciascuno
        texts: list[str] = []
        topics: list[str] = []
        for ins in insights[:20]:
            doc = ins.get("document", "").strip()
            if not doc:
                continue
            q = ins.get("metadata", {}).get("query", "")
            prefix = f"[{q[:40]}] " if q else ""
            texts.append(f"{prefix}{doc[:300]}")
            if q:
                topics.append(q[:40])

        if not texts:
            return False

        week_str = now.strftime("%Y-W%W")
        combined = "\n\n---\n\n".join(texts)

        system = (
            "Sei Pepe, assistente personale di Andrea. "
            "Hai accesso agli insight e ricerche di Andrea degli ultimi 7 giorni. "
            "Scrivi UNA sintesi strutturata in italiano, max 200 parole. "
            "Formato: max 3 bullet con i topic principali emersi, poi un insight trasversale. "
            "Niente intro. Solo contenuto utile per Andrea."
        )
        user = f"Insight degli ultimi 7 giorni ({len(texts)} elementi):\n\n{combined}"

        try:
            response = await self.pepe.client.messages.create(
                model=MODEL_HAIKU,
                max_tokens=350,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            synthesis_text = response.content[0].text.strip()
        except Exception as exc:
            logger.warning("weekly_personal_synthesis: LLM fallito: %s", exc)
            return False

        if not synthesis_text:
            return False

        # Scrivi in personal_memory
        try:
            await self.memory.store_personal_insight(
                synthesis_text,
                metadata={
                    "type":          "weekly_synthesis",
                    "week":          week_str,
                    "insight_count": len(insights),
                    "topics":        ", ".join(dict.fromkeys(topics))[:200],
                    "agent":         "scheduler",
                    "date":          now.strftime("%Y-%m-%d"),
                    "created_at":    now.isoformat(),
                },
            )
            logger.info(
                "weekly_personal_synthesis scritta: %d insight → week %s",
                len(insights), week_str,
            )
            return True
        except Exception as exc:
            logger.warning("weekly_personal_synthesis: store fallito: %s", exc)
            return False

    async def _run_shared_memory_decay(self) -> None:
        """Domenicale 03:45 — elimina insight shared_memory più vecchi di SHARED_MEMORY_DECAY_DAYS.

        shared_memory contiene pattern cross-domain generati da KnowledgeBridge.
        Con il tempo questi insight diventano obsoleti (i pattern Etsy o Personal
        che li hanno originati possono essere cambiati). La retention default è 90 giorni.
        """
        try:
            deleted = await self.memory.delete_stale_shared_memory(
                older_than_days=settings.SHARED_MEMORY_DECAY_DAYS
            )
            if deleted > 0:
                msg = (
                    f"🔗 Shared memory decay: {deleted} insight cross-domain eliminati "
                    f"(retention {settings.SHARED_MEMORY_DECAY_DAYS}gg)."
                )
                logger.info(msg)
                await self._notify_telegram(msg)
            else:
                logger.debug(
                    "shared_memory_decay: nessun insight da eliminare (retention %dgg)",
                    settings.SHARED_MEMORY_DECAY_DAYS,
                )
        except Exception as exc:
            logger.error("shared_memory_decay fallito: %s", exc)

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

    # Pipeline giornaliera rimossa in B3/step 3.1 — sostituita da AutopilotLoop (B2).
    # _run_pipeline, _run_png_pipeline, _pick_niche, _pick_template, _pick_art_type
    # e i pool _DEFAULT_NICHES/_DEFAULT_NICHES_PNG sono stati rimossi.
    # Vedi §7.1 in STATE.md per la roadmap B2.


    # ------------------------------------------------------------------
    # Blocco 2 — Publish checker
    # ------------------------------------------------------------------

    async def _run_publish_checker(self) -> None:
        """Pubblica su Etsy tutti gli item scheduled con slot ≤ now.

        Schedulato ogni 15 minuti. Attiva Etsy Ads post-publish se policy lo prevede.
        L'effettiva chiamata API ads è implementata nel Blocco 5 (EtsyAdsManager);
        qui si marca solo ads_activated=1 come preparazione.
        """
        from apps.backend.tools.etsy_api import EtsyAPIError

        queue  = self.production_queue
        policy = self.publication_policy

        if queue is None:
            logger.debug("publish_checker: production_queue non iniettata, skip")
            return

        now = datetime.now(timezone.utc).timestamp()
        due_items = await queue.get_due_scheduled(now)

        if not due_items:
            return

        logger.info("publish_checker: %d item da pubblicare", len(due_items))

        mock = bool(getattr(self.pepe, "mock_mode", False))

        for item in due_items:
            if mock:
                await queue.set_published(item.id, etsy_listing_id="MOCK_ID")
                await self._notify_telegram(
                    f"📦 [MOCK] Pubblicato: {item.listing_title or item.niche}"
                )
                logger.info("publish_checker [MOCK] item %d", item.id)
                continue

            try:
                if self.etsy_client is None:
                    raise EtsyAPIError("etsy_client non iniettato")

                listing_id = await self.etsy_client.publish_listing(item)
                await queue.set_published(item.id, listing_id)
                await self._notify_telegram(
                    f"🎉 Pubblicato: {item.listing_title or item.niche}\n"
                    f"🔗 https://etsy.com/listing/{listing_id}"
                )
                logger.info("publish_checker: item %d → listing %s", item.id, listing_id)

                # 🔴 [video] — attiva Etsy Ads se policy lo prevede
                # Chiamata API ads implementata in Blocco 5 (EtsyAdsManager)
                if policy is not None and await policy.ads_enabled():
                    await queue.set_ads_activated(item.id)
                    logger.info(
                        "Etsy Ads attivazione marcata per listing %s", listing_id
                    )

            except EtsyAPIError as exc:
                await queue.set_failed(item.id, str(exc))
                await self._notify_telegram(
                    f"❌ Errore pubblicazione {item.listing_title or item.niche}: {exc}"
                )
                logger.error("publish_checker: item %d fallito: %s", item.id, exc)

            except Exception as exc:
                await queue.set_failed(item.id, str(exc))
                logger.exception("publish_checker: errore inatteso item %d", item.id)

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
    # Blocco 4 / 5.3 — Etsy learning loop domenicale
    # ------------------------------------------------------------------

    async def _run_etsy_learning_loop(self) -> None:
        """Domenicale 02:00 — aggiorna segnali ChromaDB e confronta A/B thumbnail.

        Flusso:
        1. AnalyticsAgent.poll_listing_performance() — aggiorna listing_performance
           e diagnostica Ladder System (ctr_low, views_low, conv_low).
        2. LearningLoop.run_full_update() — ricalcola niche_intelligence da snapshot.
        3. LearningLoop.compare_ab_thumbnails(niche) — per ogni niche con ctr_low
           recente: confronta CTR originale vs alternativo, scrivi design_winner
           o rafforza low_ctr_signal.
        4. Invia report Telegram aggregato.
        """
        report_lines: list[str] = []
        errors: list[str] = []

        # 1. Poll listing performance
        if self.analytics_agent is not None:
            try:
                await self.analytics_agent.poll_listing_performance()
                report_lines.append("✅ Poll listing performance completato")
            except Exception as exc:
                errors.append(f"poll_listing_performance: {exc}")
                logger.error("etsy_learning_loop poll_listing: %s", exc)
        else:
            report_lines.append("ℹ️ analytics_agent non disponibile — poll skipped")

        # 2. LearningLoop update
        if self.learning_loop is not None:
            try:
                summary = await self.learning_loop.run_full_update()
                n_updated = summary.get("n_updated", 0)
                top       = summary.get("top_niches", [])
                report_lines.append(
                    f"✅ niche_intelligence: {n_updated} niche aggiornate"
                    + (f" | top: {', '.join(top[:3])}" if top else "")
                )
            except Exception as exc:
                errors.append(f"run_full_update: {exc}")
                logger.error("etsy_learning_loop run_full_update: %s", exc)

            # 3. A/B thumbnail comparison — B5/5.3
            try:
                db     = await self.memory.get_db()
                cursor = await db.execute(
                    """
                    SELECT DISTINCT pq.niche
                    FROM listing_performance lp
                    JOIN production_queue pq ON lp.production_queue_id = pq.id
                    WHERE lp.ladder_level = 'ctr_low'
                      AND lp.snapshot_at > unixepoch() - 7 * 86400
                    """
                )
                ctr_low_rows = await cursor.fetchall()
                ab_compared  = 0
                ab_skipped   = 0

                for row in ctr_low_rows:
                    niche = row["niche"]
                    try:
                        result = await self.learning_loop.compare_ab_thumbnails(niche)
                        if result.get("status") == "compared":
                            ab_compared += 1
                        else:
                            ab_skipped += 1
                    except Exception as exc:
                        logger.warning("etsy_learning_loop compare_ab [%s]: %s", niche, exc)
                        ab_skipped += 1

                if ctr_low_rows:
                    report_lines.append(
                        f"✅ A/B thumbnail: {ab_compared} confrontati, {ab_skipped} skipped"
                    )
            except Exception as exc:
                errors.append(f"compare_ab_thumbnails: {exc}")
                logger.error("etsy_learning_loop compare_ab: %s", exc)

        # 4. Report Telegram
        if errors:
            report_lines.append(f"⚠️ Errori: {'; '.join(errors[:3])}")

        if report_lines:
            msg = "📈 *Etsy learning loop* (domenica 02:00)\n\n" + "\n".join(report_lines)
            await self._notify_telegram(msg)

        logger.info(
            "etsy_learning_loop completato — %d step, %d errori",
            len(report_lines), len(errors),
        )

    # ------------------------------------------------------------------
    # Blocco 4 — polling performance listing
    # ------------------------------------------------------------------

    async def _run_poll_listing_performance(self) -> None:
        """Esegue il polling delle performance listing ogni 6 ore.

        Chiama AnalyticsAgent.poll_listing_performance() che:
        - inserisce snapshot in listing_performance
        - esegue diagnostica Ladder System
        - aggiorna LearningLoop (quando disponibile, step 4.5)
        """
        if self.analytics_agent is None:
            return
        try:
            await self.analytics_agent.poll_listing_performance()
        except Exception as exc:
            logger.error("poll_listing_performance fallito: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Blocco 5 — Shop profile optimizer
    # ------------------------------------------------------------------

    async def _run_shop_optimizer_job(self) -> None:
        """Lunedì 07:00 — aggiorna il profilo shop Etsy se le top niches sono cambiate.

        Chiama ShopProfileOptimizer.apply_shop_profile() che:
        - legge top niches da LearningLoop.get_top_niches()
        - confronta con l'ultima applicazione (config DB)
        - se cambiate: genera titolo SEO + about via Haiku e applica via Etsy API
        - se invariate: skip silenzioso

        Notifica Telegram solo se il profilo è stato effettivamente aggiornato.
        """
        if self.shop_optimizer is None:
            return
        try:
            result = await self.shop_optimizer.apply_shop_profile()
            status = result.get("status", "unknown")

            if status == "applied":
                title = result.get("title", "—")
                niches = ", ".join(result.get("niches", [])) or "—"
                await self._notify_telegram(
                    f"🏪 Shop profile aggiornato\n"
                    f"📝 Titolo: {title}\n"
                    f"📊 Niches: {niches}"
                )
                logger.info("shop_optimizer_job: profilo applicato — %s", title)

            elif status == "mock":
                logger.info("shop_optimizer_job: mock mode — nessuna chiamata API")

            elif status == "skipped":
                logger.info("shop_optimizer_job: niches invariate, skip")

            elif status in ("no_api", "error"):
                err = result.get("error", status)
                logger.warning("shop_optimizer_job: status=%s err=%s", status, err)
                await self._notify_telegram(
                    f"⚠️ Shop optimizer: {status} — {err}"
                )

        except Exception as exc:
            logger.error("shop_optimizer_job fallito: %s", exc)

    async def _run_etsy_ads_manager(self) -> None:
        """Ogni 6h — gestione automatica campagne Etsy Ads.

        Attiva ads sui listing nuovi (< 14 giorni) se policy.ads_enabled.
        Pausa ads se CTR < 1.5% dopo 7+ giorni di attività.
        Notifica Telegram solo se ci sono state azioni (attivazioni o pause).
        """
        if self.etsy_ads_manager is None:
            return
        try:
            await self.etsy_ads_manager.auto_manage_ads()
        except Exception as exc:
            logger.error("etsy_ads_manager job fallito: %s", exc)

    # ------------------------------------------------------------------
    # Broadcast helper
    # ------------------------------------------------------------------

    async def _broadcast(self, event: dict[str, Any]) -> None:
        if self._ws_broadcast:
            try:
                await self._ws_broadcast(event)
            except Exception:
                pass
