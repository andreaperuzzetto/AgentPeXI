"""AutopilotLoop — orchestratore asyncio del pipeline design→approval→publish.

Ciclo principale:
  1. Discard approvazioni stale (solo al primo giro)
  2. Controlla stato loop (paused_* → sleep)
  3. Controlla finestra disponibilità (no cambio stato)
  4. Controlla budget
  5. Controlla quota giornaliera
  6. Queue depth check (TARGET = 2)
  7. Bundle check / pick next niche
  8. Avvia pipeline design
  9. Invia approval notification
 10. Attendi risposta (hybrid wait 24h)
 11. Gestisci decisione

Lo stato del loop è persistito in `autopilot_state` e sopravvive ai restart.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta

import aiosqlite

from apps.backend.core.budget_manager import BudgetManager, BudgetStatus
from apps.backend.core.production_queue import ProductionQueueService
from apps.backend.core.publication_policy import PublicationPolicy

logger = logging.getLogger("agentpexi.autopilot")

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------

TARGET_QUEUE_DEPTH  = 2    # max item in (pending_design + pending_approval) insieme

LOOP_SLEEP_NORMAL   = 30   # secondi tra iterazioni normali
LOOP_SLEEP_PAUSED   = 60   # secondi in paused_skip / paused_manual
LOOP_SLEEP_BUDGET   = 300  # secondi in paused_budget
LOOP_SLEEP_NIGHT    = 300  # secondi fuori finestra
LOOP_SLEEP_QUOTA    = 60   # secondi in paused_quota (controlla resume)
LOOP_SLEEP_EMPTY    = 300  # secondi senza niche disponibili

APPROVAL_TIMEOUT    = 86400  # 24h attesa risposta utente
APPROVAL_POLL       = 30.0   # secondi per poll asyncio


# ---------------------------------------------------------------------------
# AutopilotLoop
# ---------------------------------------------------------------------------

class AutopilotLoop:
    """Orchestratore del pipeline Etsy.

    Dipendenze iniettate nel costruttore:
      - db                   : aiosqlite.Connection (da memory_manager.get_db())
      - queue                : ProductionQueueService
      - budget               : BudgetManager
      - policy               : PublicationPolicy
      - bot_send             : async callable(text: str)
      - bot_send_photo       : async callable(path: str, caption: str)
      - bot_send_media_group : async callable(paths: list, caption: str)
      - design_pipeline      : async callable(item_id: int, niche_data: dict)
      - niche_picker         : async callable() -> dict | None
      - bundle_checker       : async callable() -> dict | None

    L'injection esplicita rende il loop testabile senza Telegram né DB reale.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        queue: ProductionQueueService,
        budget: BudgetManager,
        policy: PublicationPolicy,
        bot_send,
        bot_send_photo=None,
        bot_send_media_group=None,
        design_pipeline=None,
        niche_picker=None,
        bundle_checker=None,
    ) -> None:
        self._db    = db
        self.queue  = queue
        self.budget = budget
        self.policy = policy

        self._bot_send             = bot_send
        self._bot_send_photo       = bot_send_photo       or self._noop_photo
        self._bot_send_media_group = bot_send_media_group or self._noop_media
        self._design_pipeline      = design_pipeline      or self._noop_design
        self._niche_picker         = niche_picker         or self._default_niche_picker
        self._bundle_checker       = bundle_checker       or self._default_bundle_checker

        self._running         = False
        self._first_iteration = True

        # item_id → asyncio.Event  (segnale dal CallbackQueryHandler Telegram)
        self._approval_events:  dict[int, asyncio.Event] = {}
        # item_id → str  ("approved" | "skipped_user" | ...)
        self._approval_results: dict[int, str]           = {}

    # ------------------------------------------------------------------
    # Noop fallback
    # ------------------------------------------------------------------

    async def _noop_photo(self, path: str, caption: str) -> None:
        await self._bot_send(caption)

    async def _noop_media(self, paths: list, caption: str) -> None:
        await self._bot_send(caption)

    async def _noop_design(self, item_id: int, niche_data: dict) -> None:
        logger.warning("design_pipeline non iniettata — item %s non processato", item_id)

    async def _default_niche_picker(self) -> dict | None:
        """Fallback: prima niche per performance_score in niche_intelligence."""
        try:
            cursor = await self._db.execute(
                """
                SELECT niche, product_type
                FROM niche_intelligence
                ORDER BY performance_score DESC LIMIT 1
                """
            )
            row = await cursor.fetchone()
            if row:
                return {"niche": row[0], "product_type": row[1]}
        except Exception:
            pass
        return None

    async def _default_bundle_checker(self) -> dict | None:
        """Placeholder — BundleStrategy implementata in Block 4."""
        return None

    # ------------------------------------------------------------------
    # State machine (autopilot_state)
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

    # ------------------------------------------------------------------
    # Approval events (chiamati da CallbackQueryHandler Telegram)
    # ------------------------------------------------------------------

    def register_approval(self, item_id: int, result: str) -> None:
        """Chiamato dal bot quando l'utente preme Approva/Salta.

        result: "approved" | "skipped_user"
        """
        self._approval_results[item_id] = result
        if item_id in self._approval_events:
            self._approval_events[item_id].set()

    # ------------------------------------------------------------------
    # Startup / shutdown pubblici
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Imposta status=running e avvia il task asyncio."""
        await self._set_status("running")
        self._running         = True
        self._first_iteration = True
        asyncio.create_task(self.run_loop(), name="autopilot_loop")
        logger.info("AutopilotLoop avviato")

    async def stop(self) -> None:
        """Mette in pausa manuale."""
        self._running = False
        await self._set_status("paused_manual")
        logger.info("AutopilotLoop fermato (paused_manual)")

    async def resume(self) -> None:
        """Riprende da qualsiasi stato paused."""
        await self._set_status("running")
        if not self._running:
            self._running = True
            asyncio.create_task(self.run_loop(), name="autopilot_loop")
        logger.info("AutopilotLoop ripreso")

    # ------------------------------------------------------------------
    # Ciclo principale
    # ------------------------------------------------------------------

    async def run_loop(self) -> None:
        logger.info("run_loop: inizio")
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("AutopilotLoop errore non gestito: %s", exc)
                await asyncio.sleep(LOOP_SLEEP_NORMAL)
        logger.info("run_loop: terminato")

    async def _tick(self) -> None:

        # 0. Discard stale approvals — solo alla prima iterazione
        if self._first_iteration:
            discarded = await self.queue.discard_stale_approvals()
            if discarded:
                logger.info("Startup: scartati %d approval scaduti", discarded)
            await self._on_startup_recovery()
            self._first_iteration = False

        # 1. Controlla stato loop
        status = await self._get_status()

        if status == "paused_budget":
            await asyncio.sleep(LOOP_SLEEP_BUDGET)
            return

        if status in ("paused_skip", "paused_manual"):
            await asyncio.sleep(LOOP_SLEEP_PAUSED)
            return

        if status == "paused_quota":
            if datetime.now() >= await self._get_quota_resume():
                await self._set_status("running")
            else:
                await asyncio.sleep(LOOP_SLEEP_QUOTA)
                return

        # 2. Finestra disponibilità (sleep silenzioso, no cambio stato)
        if not await self.policy.is_in_availability_window():
            await asyncio.sleep(LOOP_SLEEP_NIGHT)
            return

        # 3. Budget
        budget_status = await self.budget.check_budget()
        if budget_status == BudgetStatus.EXCEEDED:
            await self._set_status("paused_budget")
            await self._bot_send("⛔ Budget giornaliero esaurito. Loop in pausa fino a /run.")
            return
        if budget_status == BudgetStatus.WARNING:
            await self._bot_send("⚠️ Budget al 75% — continuo ma monitora.")

        # 4. Quota giornaliera
        if not await self.policy.can_publish_today():
            resume_at = self._tomorrow_08_00()
            await self._set_status("paused_quota")
            await self._set_quota_resume(resume_at)
            max_pd = await self.policy._get_int("policy.max_per_day", 5)
            await self._bot_send(
                f"📊 Quota giornaliera raggiunta ({max_pd}/{max_pd}). "
                f"Riprendo domani alle 08:00."
            )
            return

        # 5. Queue depth check
        pending   = await self.queue.get_pending_approval()
        in_design = await self.queue.get_items_by_status("pending_design")
        if len(pending) + len(in_design) >= TARGET_QUEUE_DEPTH:
            await asyncio.sleep(LOOP_SLEEP_PAUSED)
            return

        # 6. Bundle check → fallback su niche picker
        niche_data = await self._bundle_checker()
        if not niche_data:
            niche_data = await self._niche_picker()
        if not niche_data:
            logger.debug("Nessuna niche disponibile — attendo")
            await asyncio.sleep(LOOP_SLEEP_EMPTY)
            return

        # 7. Crea item + avvia design pipeline
        run_id  = str(uuid.uuid4())
        item_id = await self.queue.create_item(
            niche        = niche_data["niche"],
            product_type = niche_data.get("product_type", "digital_print"),
            keywords     = niche_data.get("keywords", []),
            entry_score  = niche_data.get("entry_score", 0.0),
            loop_run_id  = run_id,
        )
        await self._state_set("loop.current_run_id", run_id)
        logger.info("Design pipeline avviata: item=%d niche=%s", item_id, niche_data["niche"])

        await self._design_pipeline(item_id, niche_data)

        # 8. Approval notification
        await self._send_approval_notification(item_id)

        # 9. Hybrid wait
        decision = await self._wait_for_approval(item_id)

        # 10. Gestisci decisione
        await self._handle_decision(item_id, decision)

        # Cleanup eventi
        self._approval_events.pop(item_id, None)
        self._approval_results.pop(item_id, None)

        await asyncio.sleep(LOOP_SLEEP_NORMAL)

    # ------------------------------------------------------------------
    # Approval notification
    # ------------------------------------------------------------------

    async def _send_approval_notification(self, item_id: int) -> None:
        item = await self.queue.get_item(item_id)
        if not item:
            return

        self._approval_events.setdefault(item_id, asyncio.Event())

        kw_preview = ", ".join(item.keywords[:5]) if item.keywords else "—"
        caption = (
            f"🆕 *Nuovo listing pronto*\n\n"
            f"📦 Prodotto: {item.product_type}\n"
            f"🎯 Niche: {item.niche}\n"
            f"🏷️ Titolo: {item.listing_title or '—'}\n"
            f"💰 Prezzo: €{item.listing_price or 0:.2f}\n"
            f"🔑 Keywords: {kw_preview}\n"
            f"📊 Entry score: {item.entry_score:.2f}\n\n"
            f"💸 Costi: ${item.llm_cost_usd:.4f} LLM + "
            f"${item.image_cost_usd:.4f} img + $0.20 fee\n\n"
            f"Rispondi con /approve {item_id} o /skip {item_id}"
        )

        if item.thumbnail_path:
            try:
                await self._bot_send_photo(item.thumbnail_path, caption)
                return
            except Exception as exc:
                logger.warning("Invio thumbnail fallito: %s", exc)

        await self._bot_send(caption)

    # ------------------------------------------------------------------
    # Hybrid wait (24h con poll ogni 30s)
    # ------------------------------------------------------------------

    async def _wait_for_approval(self, item_id: int) -> str:
        deadline = time.time() + APPROVAL_TIMEOUT

        while time.time() < deadline:
            event = self._approval_events.get(item_id)
            if event:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(event.wait()),
                        timeout=APPROVAL_POLL,
                    )
                    result = self._approval_results.get(item_id)
                    if result:
                        return result
                except asyncio.TimeoutError:
                    pass

            # Poll DB — fallback se il segnale è già arrivato
            item = await self.queue.get_item(item_id)
            if item:
                if item.status == "approved":
                    return "approved"
                if item.status == "skipped":
                    return f"skipped_{item.skip_reason or 'user'}"

            # Budget esaurito durante attesa
            if await self.budget.check_budget() == BudgetStatus.EXCEEDED:
                await self.queue.set_skipped(item_id, "budget")
                return "skipped_budget"

            # Fuori finestra — sleep più lungo senza perdere il posto
            if not await self.policy.is_in_availability_window():
                await asyncio.sleep(LOOP_SLEEP_PAUSED)

        await self.queue.set_skipped(item_id, "timeout")
        return "skipped_timeout"

    # ------------------------------------------------------------------
    # Handle decision
    # ------------------------------------------------------------------

    async def _handle_decision(self, item_id: int, decision: str) -> None:
        if decision == "approved":
            slot = await self.policy.next_available_slot()
            await self.queue.assign_slot(item_id, slot.timestamp())
            await self._reset_skip_counters()
            await self._bot_send(f"✅ Approvato! Pubblicazione: {slot:%d/%m %H:%M}")

        elif decision == "skipped_user":
            await self.queue.set_skipped(item_id, "user")
            consec = await self._increment_user_skip()
            if consec >= 3:
                await self._handle_skip_pause()
            else:
                await self._bot_send(f"⏭ Saltato ({consec}/3 skip consecutivi).")

        elif decision == "skipped_timeout":
            consec_to = await self._increment_timeout_skip()
            if consec_to == 2:
                await self._bot_send("⚠️ 2° timeout consecutivo — sei disponibile?")
            elif consec_to >= 3:
                await self._handle_timeout_pause()

        elif decision == "skipped_budget":
            pass  # già gestito nel loop

        else:
            logger.warning("_handle_decision: decisione sconosciuta '%s'", decision)

    # ------------------------------------------------------------------
    # Skip pause (3 user-skip consecutivi)
    # ------------------------------------------------------------------

    async def _handle_skip_pause(self) -> None:
        await self._set_status("paused_skip")
        recent = await self.queue.get_last_skipped(limit=3, reason="user")

        msg = "⛔ 3 listing consecutivi saltati. Loop in pausa.\n\nUltimi rifiutati:\n"
        for it in recent:
            msg += f"• {it.niche} — {it.product_type} (score: {it.entry_score:.2f})\n"
        msg += "\nRiprendi con /run quando vuoi."

        photos = [it.thumbnail_path for it in recent if it.thumbnail_path]
        if photos:
            try:
                await self._bot_send_media_group(photos, caption=msg)
                return
            except Exception as exc:
                logger.warning("Invio media group fallito: %s", exc)

        await self._bot_send(msg)

    # ------------------------------------------------------------------
    # Timeout pause (3 timeout consecutivi)
    # ------------------------------------------------------------------

    async def _handle_timeout_pause(self) -> None:
        await self._set_status("paused_manual")
        await self._bot_send(
            "⛔ 3 timeout consecutivi. Loop in pausa — sei ancora lì?\n"
            "Riprendi con /run quando vuoi."
        )

    # ------------------------------------------------------------------
    # Startup recovery
    # ------------------------------------------------------------------

    async def _on_startup_recovery(self) -> None:
        """Al restart, re-invia le notification per item ancora in pending_approval."""
        pending = await self.queue.get_pending_approval()
        for item in pending:
            if item.id not in self._approval_events:
                self._approval_events[item.id] = asyncio.Event()
                try:
                    await self._send_approval_notification(item.id)
                    logger.info("Recovery: re-inviata notification item %d", item.id)
                except Exception as exc:
                    logger.warning("Recovery notification fallita item %d: %s", item.id, exc)

    # ------------------------------------------------------------------
    # Comandi Telegram (chiamati da handlers/autopilot.py)
    # ------------------------------------------------------------------

    async def cmd_run(self) -> str:
        status = await self._get_status()
        if status == "running" and self._running:
            return "▶️ Loop già in esecuzione."
        await self.resume()
        return "▶️ AutopilotLoop avviato."

    async def cmd_stop(self) -> str:
        await self.stop()
        return "⏸ AutopilotLoop in pausa. Riprendi con /run."

    async def cmd_status(self) -> str:
        status  = await self._get_status()
        summary = await self.budget.get_status_summary()
        count   = await self.policy.published_today_count()
        max_pd  = await self.policy._get_int("policy.max_per_day", 5)
        pending   = await self.queue.get_pending_approval()
        in_design = await self.queue.get_items_by_status("pending_design")

        lines = [
            f"🤖 *AutopilotLoop*: `{status}`",
            f"",
            f"📊 Pubblicati oggi: {count}/{max_pd}",
            f"🔄 In coda: {len(pending)} approvazione + {len(in_design)} design",
            f"",
            f"💰 Budget oggi:",
            f"  LLM:  ${summary.llm_today:.4f} / ${summary.llm_limit:.2f} "
            f"({summary.llm_pct*100:.0f}%)",
            f"  Img:  ${summary.image_today:.4f} / ${summary.image_limit:.2f} "
            f"({summary.image_pct*100:.0f}%)",
            f"  Fee:  ${summary.fee_today:.2f} / ${summary.fee_limit:.2f} "
            f"({summary.fee_pct*100:.0f}%)",
            f"  Stato: {summary.status.value.upper()}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _tomorrow_08_00() -> datetime:
        tomorrow = datetime.now() + timedelta(days=1)
        return tomorrow.replace(hour=8, minute=0, second=0, microsecond=0)
