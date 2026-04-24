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

    # Nicchie di default per Digital Art PNG (wall art, quote prints, botanical)
    _DEFAULT_NICHES_PNG = [
        "minimalist botanical print",
        "inspirational quote wall art",
        "abstract watercolor print",
        "nursery wall art animals",
        "vintage style botanical poster",
    ]

    async def _run_png_pipeline(self) -> None:
        """Wrapper Telegram per pipeline Digital Art PNG."""
        await self._run_pipeline(product_type="digital_art_png")

    async def _run_pipeline(self, product_type: str = "printable_pdf") -> None:
        """Pipeline Research → Design → Publisher.

        product_type: "printable_pdf" (default) o "digital_art_png".
        SVG escluso — svg_bundle non è supportato in questa pipeline.
        """
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

        # 3-5. Research Agent in modalità autonoma — decide lui niche + product_type + brief
        niche: str = ""
        template: str = ""
        keywords: list[str] = []
        etsy_tags_13: list[str] = []
        selling_signals: dict = {}
        research_output: dict = {}
        winner_brief: dict = {}

        if self.research_agent and self.pepe:
            from apps.backend.core.models import AgentTask as _AgentTask

            research_task = _AgentTask(
                agent_name="research",
                input_data={
                    "mode": "autonomous",
                    # Se product_type è stato esplicitamente richiesto (es. /pipeline png),
                    # lo passiamo come constraint — Research cercherà solo candidati di quel tipo.
                    "product_type_constraint": product_type,
                },
                source="scheduler",
            )
            try:
                research_result = await self.pepe.dispatch_task(research_task)
                out = research_result.output_data or {}
                research_output = out
                winner = out.get("winner", {})

                if winner:
                    niche = winner.get("niche", "")
                    # Rispetta il product_type del winner solo se non era vincolato dall'esterno.
                    # Se era vincolato, il winner è già filtrato — usa comunque il suo product_type.
                    product_type = winner.get("product_type", product_type)
                    winner_brief = winner.get("brief", {})
                    keywords = winner_brief.get("keywords", [])
                    etsy_tags_13 = winner_brief.get("etsy_tags_13", [])
                    selling_signals = winner_brief.get("selling_signals", {})
                    template = (
                        winner_brief.get("template") or
                        winner_brief.get("art_type") or
                        self._pick_template(niche)
                    )
                    logger.info(
                        "Research autonomo: winner='%s' [%s], %d keywords, %d tags",
                        niche, product_type, len(keywords), len(etsy_tags_13),
                    )
                else:
                    # Research completato ma senza winner → fallback _pick_niche
                    logger.warning("Research autonomo: nessun winner nell'output, fallback _pick_niche")
                    niches_data = out.get("niches", [])
                    if niches_data:
                        first = niches_data[0]
                        niche = first.get("name", "")
                        keywords = first.get("keywords", [])
                        etsy_tags_13 = first.get("etsy_tags_13", [])
                        selling_signals = first.get("selling_signals", {})
                        template = self._pick_template(niche)

            except Exception as exc:
                logger.error("Research autonomo fallito: %s", exc)
                await self._notify_telegram(f"⚠️ Pipeline: Research autonomo fallito: {exc}")
                return
        else:
            logger.warning("Research agent o Pepe non disponibile, uso keywords vuote")

        # Se Research non ha fornito una nicchia → fallback _pick_niche (cold-start)
        if not niche:
            niche = await self._pick_niche(product_type=product_type)
            if not niche:
                logger.info("Pipeline [%s]: nessuna nicchia disponibile, skip", product_type)
                return
            template = self._pick_template(niche) if product_type == "printable_pdf" else self._pick_art_type(niche)
            logger.info("Pipeline [%s] fallback a niche: %s", product_type, niche)

        logger.info("Pipeline [%s] avviata per nicchia: %s", product_type, niche)

        # 5. Costruisci brief per Design
        task_id = str(uuid.uuid4())

        if product_type == "digital_art_png":
            art_type = winner_brief.get("art_type") or self._pick_art_type(niche)
            color_schemes = _extract_color_schemes(winner_brief.get("color_palette_hint", "")) or ["warm", "neutral", "pastel"]
            brief = {
                "niche": niche,
                "product_type": "digital_art_png",
                "art_type": art_type,
                "num_variants": 3,
                "color_schemes": color_schemes,
                "keywords": keywords,
                "production_queue_task_id": task_id,
                "research_result": research_output,
            }
            template = art_type
        else:
            pdf_template = winner_brief.get("template") or self._pick_template(niche)
            color_schemes = _extract_color_schemes(winner_brief.get("color_palette_hint", "")) or ["sage", "blush", "slate"]
            brief = {
                "niche": niche,
                "product_type": "printable_pdf",
                "template": pdf_template,
                "size": "A4",
                "num_variants": 3,
                "color_schemes": color_schemes,
                "keywords": keywords,
                "production_queue_task_id": task_id,
                "research_result": research_output,
            }
            template = pdf_template

        # 6. Aggiungi in production_queue
        await self.memory.add_to_production_queue(
            task_id=task_id,
            product_type=product_type,
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
            design_out = design_result.output_data or {}
            variants = design_out.get("variants", [])
            # Chiave output diversa per tipo: pdf_path (PDF) vs file_path (PNG)
            if product_type == "digital_art_png":
                file_paths = [v["file_path"] for v in variants if v.get("file_path")]
            else:
                file_paths = [v["pdf_path"] for v in variants if v.get("pdf_path")]
            cost = design_result.cost_usd
            logger.info(
                "Design [%s] completato: %d varianti generate, costo $%.4f",
                product_type, len(file_paths), cost,
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

        if product_type == "digital_art_png":
            publish_base = {
                "production_queue_task_id": task_id,
                "product_type": "digital_art_png",
                "niche": niche,
                "color_schemes": brief.get("color_schemes", []),
                "keywords": keywords,
                "etsy_tags_13": etsy_tags_13,
                "selling_signals": selling_signals,
                # Per PNG il file stesso è l'immagine listing — nessun Playwright necessario
                "thumbnail_paths": file_paths,
            }
        else:
            publish_base = {
                "production_queue_task_id": task_id,
                "product_type": "printable_pdf",
                "template": template,
                "niche": niche,
                "color_schemes": brief.get("color_schemes", []),
                "keywords": keywords,
                "size": brief.get("size", "A4"),
                # Dati Research validati: Publisher usa etsy_tags_13 al posto di tag LLM
                # e selling_signals per costruire la SEO copy con i trigger di conversione
                "etsy_tags_13": etsy_tags_13,
                "selling_signals": selling_signals,
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

    async def _run_etsy_learning_loop(self) -> None:
        """Domenicale 02:00 — aggiorna i segnali ChromaDB del learning loop Etsy.

        Esegue in sequenza:
        1. AnalyticsAgent — sync stats Etsy → aggiorna design_winner (sales/views reali)
        2. FinanceAgent   — ricalcola ROI per nicchia → aggiorna niche_roi_snapshot,
                           finance_directive, finance_insight

        Senza questo job, i segnali cross-agent in pepe_memory diventano stale nelle
        settimane in cui la pipeline non gira manualmente. Con questo job i segnali
        restano freschi e DesignAgent/ResearchAgent li trovano aggiornati alla prossima
        esecuzione della pipeline.

        Guard: skip se nessun listing in etsy_listings (niente da analizzare).
        """
        # Guard: skip se non ci sono listing su cui fare analytics/finance
        try:
            listing_count = await self.memory.get_etsy_listings_count()
            if listing_count == 0:
                logger.info("etsy_learning_loop: nessun listing, skip")
                return
        except Exception:
            pass  # se il check fallisce, procedi comunque

        analytics_ok = False
        finance_ok   = False

        # 1. Analytics — sync stats + aggiorna design_winner
        try:
            await self._run_analytics()
            analytics_ok = True
            logger.info("etsy_learning_loop: analytics completato")
        except Exception as exc:
            logger.error("etsy_learning_loop: analytics fallito: %s", exc)

        # 2. Finance — ROI + directive + insight
        try:
            await self._run_finance()
            finance_ok = True
            logger.info("etsy_learning_loop: finance completato")
        except Exception as exc:
            logger.error("etsy_learning_loop: finance fallito: %s", exc)

        # Notifica Telegram riepilogo
        if analytics_ok or finance_ok:
            parts = []
            if analytics_ok:
                parts.append("📊 Analytics ✅")
            else:
                parts.append("📊 Analytics ❌")
            if finance_ok:
                parts.append("💶 Finance ✅")
            else:
                parts.append("💶 Finance ❌")
            await self._notify_telegram(
                "🔁 Etsy learning loop completato\n" + "  ".join(parts)
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
            result = await dispatcher(task)
            logger.info("Finance report giornaliero completato")
            # Notifica Telegram con sintesi
            if result and hasattr(result, "output_data") and result.output_data:
                rep = result.output_data
                rev  = rep.get("total_revenue_eur", 0.0)
                cost = rep.get("llm_cost_eur", 0.0)
                net  = rep.get("net_margin_eur", 0.0)
                roi  = rep.get("roi_pct", 0.0)
                conf = getattr(result, "confidence", 0.0)
                status_icon = "✅" if conf >= 0.60 else "🟡"
                await self._notify_telegram(
                    f"{status_icon} Finance report 30gg\n"
                    f"💶 Revenue: €{rev:.2f} | Costi LLM: €{cost:.4f}\n"
                    f"📊 Margine netto: €{net:.2f} | ROI: {roi:.1f}%\n"
                    f"Confidence: {conf:.0%}"
                )
            else:
                await self._notify_telegram("✅ Finance report completato (dati insufficienti per sintesi)")
        except Exception as exc:
            logger.error("Finance report fallito: %s", exc)
            await self._notify_telegram(f"⚠️ Finance report fallito: {exc}")

    async def _pick_niche(self, product_type: str = "printable_pdf") -> str | None:
        """Sceglie la prossima nicchia da produrre.

        Deduplicazione a tre livelli:
        1. production_queue — nicchie pianificate/in-corso/completate negli ultimi 7 giorni
        2. etsy_listings    — nicchie già pubblicate su Etsy (via is_duplicate_product)
        3. Fallback pool    — nicchie di default per product_type (PDF o PNG)
        """
        # 1. Pool nicchie da ChromaDB insights
        niches_pool: list[str] = []
        query = "etsy wall art niche trending" if product_type == "digital_art_png" else "etsy niche trending"
        try:
            insights = await self.memory.query_insights(query, n_results=5)
            for ins in insights:
                doc = ins.get("document", "")
                if doc:
                    niches_pool.append(doc.split("\n")[0][:80])
        except Exception:
            pass

        if not niches_pool:
            niches_pool = (
                list(self._DEFAULT_NICHES_PNG)
                if product_type == "digital_art_png"
                else list(self._DEFAULT_NICHES)
            )

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
                if await self.memory.is_duplicate_product(niche, product_type):
                    blocked_niches.add(niche)
                    logger.debug("Niche '%s' [%s] già in etsy_listings, skip", niche, product_type)
            except Exception as exc:
                logger.debug("Errore check etsy_listings per '%s': %s", niche, exc)

        for niche in niches_pool:
            if niche not in blocked_niches:
                return niche

        logger.info("Tutte le nicchie [%s] già prodotte o pubblicate su Etsy", product_type)
        return None

    @staticmethod
    def _pick_template(niche: str) -> str:
        """Regola semplice: inferisci template PDF dal nome della nicchia."""
        n = niche.lower()
        if "habit" in n:
            return "habit_tracker"
        if "budget" in n or "finance" in n or "expense" in n:
            return "budget_tracker"
        if "meal" in n or "food" in n or "recipe" in n:
            return "meal_planner"
        if "workout" in n or "fitness" in n or "exercise" in n:
            return "workout_tracker"
        if "journal" in n or "diary" in n or "gratitude" in n:
            return "gratitude_journal"
        if "reading" in n or "book" in n:
            return "reading_log"
        if "travel" in n or "trip" in n or "itinerary" in n:
            return "travel_planner"
        if "goal" in n or "vision" in n or "resolution" in n:
            return "goal_planner"
        if "project" in n or "task" in n or "checklist" in n:
            return "project_planner"
        if "daily" in n or "day" in n:
            return "daily_planner"
        if "monthly" in n or "month" in n:
            return "monthly_planner"
        return "weekly_planner"

    @staticmethod
    def _pick_art_type(niche: str) -> str:
        """Inferisce art_type per Digital Art PNG dal nome della nicchia."""
        n = niche.lower()
        if "quote" in n or "inspirational" in n or "motivation" in n or "saying" in n:
            return "quote_print"
        if "botanical" in n or "plant" in n or "floral" in n or "flower" in n or "leaf" in n:
            return "botanical_print"
        if "nursery" in n or "kids" in n or "baby" in n or "children" in n or "animal" in n:
            return "nursery_print"
        return "wall_art"

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
