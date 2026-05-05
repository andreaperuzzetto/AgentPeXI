"""Scheduler — personal mixin: learning loop, weekly synthesis, decay, reminders."""

from __future__ import annotations

import logging

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.scheduler")


class _PersonalMixin:
    """Personal-domain scheduled jobs: learning loop, reminders, shared-memory decay."""

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
                    logger.exception("Unexpected error")
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
