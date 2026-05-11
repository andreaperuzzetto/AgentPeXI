from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from datetime import datetime

from apps.backend.core.budget_manager import BudgetStatus
from apps.backend.core._autopilot._constants import (
    TARGET_QUEUE_DEPTH,
    LOOP_SLEEP_NORMAL,
    LOOP_SLEEP_PAUSED,
    LOOP_SLEEP_BUDGET,
    LOOP_SLEEP_NIGHT,
    LOOP_SLEEP_QUOTA,
    LOOP_SLEEP_EMPTY,
)

logger = logging.getLogger("agentpexi.autopilot")


class _LoopMixin:
    """Main asyncio loop, startup/shutdown, noop fallbacks, and default pickers."""

    # ------------------------------------------------------------------
    # Noop fallbacks
    # ------------------------------------------------------------------

    async def _noop_photo(self, path: str, caption: str) -> None:
        await self._bot_send(caption)

    async def _noop_media(self, paths: list, caption: str) -> None:
        await self._bot_send(caption)

    async def _noop_design(self, item_id: int, niche_data: dict) -> None:
        logger.warning("design_pipeline non iniettata — item %s non processato", item_id)

    # ------------------------------------------------------------------
    # Background task helper (CNC-005)
    # ------------------------------------------------------------------

    def _add_bg_task(self, coro) -> asyncio.Task:
        """Create a task, register it in _bg_tasks, and auto-discard on completion."""
        t = asyncio.create_task(coro)
        self._bg_tasks.add(t)
        t.add_done_callback(self._bg_tasks.discard)
        return t

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
            logger.exception("_default_niche_picker failed — no niche selected")

    async def _default_bundle_checker(self) -> dict | None:
        """Placeholder — BundleStrategy implementata in Block 4."""
        return None

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Imposta status=running e avvia il task asyncio."""
        await self._set_status("running")
        self._running         = True
        self._first_iteration = True
        self._loop_task = asyncio.create_task(self.run_loop(), name="autopilot_loop")
        logger.info("AutopilotLoop avviato")

    async def stop(self, *, final: bool = False) -> None:
        """Mette in pausa manuale e cancella tutti i task in volo.

        final=True  → imposta status=idle e azzera current_niche (usato da
                       POST /api/autopilot/stop). Tutto avviene sotto _cmd_lock
                       così da evitare la race con una resume() concorrente
                       (NEW-002).
        final=False → imposta status=paused_manual (comportamento storico).
        """
        async with self._cmd_lock:
            self._running = False
            await self._set_status("idle" if final else "paused_manual")
            if final:
                await self._state_set("loop.current_niche", "")
            if self._loop_task:
                self._loop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._loop_task
            for t in list(self._bg_tasks):
                t.cancel()
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            self._bg_tasks.clear()
        logger.info("AutopilotLoop %s", "fermato (idle)" if final else "fermato (paused_manual)")

    async def resume(self) -> None:
        """Riprende da qualsiasi stato paused, cancellando l'eventuale task orfano."""
        async with self._cmd_lock:
            if self._loop_task and not self._loop_task.done():
                self._loop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._loop_task
            await self._set_status("running")
            self._running = True
            self._loop_task = asyncio.create_task(self.run_loop(), name="autopilot_loop")
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
            logger.info("Startup: discard_stale_approvals → %d scartati", discarded)
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
        depth = len(pending) + len(in_design)
        if depth >= TARGET_QUEUE_DEPTH:
            # Per ogni item pending_approval senza recovery task attivo → avvia ora.
            # Questo gestisce item che passano da pending_design a pending_approval
            # DOPO che _on_startup_recovery ha già girato (race condition al restart).
            for item in pending:
                async with self._approval_lock:
                    if item.id in self._approval_events:
                        continue
                    evt = asyncio.Event()
                    self._approval_events[item.id] = evt
                    already_approved = item.id in self._approval_results

                logger.info("Queue depth: item %d senza event — avvio recovery + notifica", item.id)
                if already_approved:
                    evt.set()
                else:
                    try:
                        await self._send_approval_notification(item.id)
                    except Exception as exc:
                        logger.warning("Queue depth notifica item %d: %s", item.id, exc)

                async def _recover_queued(iid: int = item.id) -> None:
                    try:
                        decision = await self._wait_for_approval(iid)
                        await self._handle_decision(iid, decision)
                    except Exception as exc:
                        logger.warning("Queue depth recovery item %d: %s", iid, exc)
                    finally:
                        async with self._approval_lock:
                            self._approval_events.pop(iid, None)
                            self._approval_results.pop(iid, None)

                self._add_bg_task(_recover_queued())

            ids = [str(i.id) for i in pending + in_design]
            logger.info(
                "Queue depth %d/%d — in attesa item %s — dormo %ds",
                depth, TARGET_QUEUE_DEPTH, ", ".join(ids) or "—", LOOP_SLEEP_PAUSED,
            )
            await asyncio.sleep(LOOP_SLEEP_PAUSED)
            return

        # 6. Bundle check → fallback su niche picker
        niche_data = await self._bundle_checker()
        if not niche_data:
            niche_data = await self._niche_picker()

        # Traccia avvio tick effettivo [FE-0.5]
        await self._state_set("loop.last_run_at", str(time.time()))
        if not niche_data:
            logger.info("Nessuna niche disponibile in niche_intelligence — attendo %ds", LOOP_SLEEP_EMPTY)
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
        await self._state_set("loop.current_niche",  niche_data["niche"])  # [FE-0.5]
        logger.info("Design pipeline avviata: item=%d niche=%s", item_id, niche_data["niche"])

        await self._design_pipeline(item_id, niche_data)

        # 8. Approval notification
        await self._send_approval_notification(item_id)

        # 9. Hybrid wait
        decision = await self._wait_for_approval(item_id)

        # 10. Gestisci decisione
        await self._handle_decision(item_id, decision)

        # Cleanup eventi e stato niche corrente [FE-0.5]
        async with self._approval_lock:
            self._approval_events.pop(item_id, None)
            self._approval_results.pop(item_id, None)
        await self._state_set("loop.current_niche", "")

        await asyncio.sleep(LOOP_SLEEP_NORMAL)

    # ------------------------------------------------------------------
    # Startup recovery
    # ------------------------------------------------------------------

    async def _on_startup_recovery(self) -> None:
        """Al restart, avvisa l'utente degli item pendenti e riprende il ciclo
        wait/handle per ciascuno.

        Flusso:
          1. Se ci sono item in pending_approval → invia riepilogo coda
          2. Per ogni item invia notifica con keyboard approve/skip
          3. Spawna task separato che attende la decisione e la processa
        """
        pending = await self.queue.get_pending_approval()
        logger.info("_on_startup_recovery: %d item in pending_approval", len(pending))
        if not pending:
            return

        # ── 1. Riepilogo coda ────────────────────────────────────────────────
        lines = [f"🔄 Coda in attesa: {len(pending)} item da gestire prima che il loop proceda.\n"]
        for item in pending:
            kw = ", ".join(item.keywords[:3]) if item.keywords else "—"
            lines.append(
                f"  • Item {item.id} — {item.niche} [{item.product_type}] "
                f"score={item.entry_score:.2f}"
            )
        lines.append("\nRispondi a ciascuno con i pulsanti qui sotto.")
        try:
            await self._bot_send("\n".join(lines))
        except Exception as exc:
            logger.warning("Recovery: invio riepilogo coda fallito: %s", exc)

        # ── 2. Notifica + task per ogni item ─────────────────────────────────
        for item in pending:
            async with self._approval_lock:
                if item.id in self._approval_events:
                    continue
                evt = asyncio.Event()
                self._approval_events[item.id] = evt
                already_approved = item.id in self._approval_results

            if already_approved:
                # Approvazione già registrata prima di /run → processo immediato
                evt.set()
                logger.info("Recovery: approvazione pre-esistente item %d", item.id)
            else:
                try:
                    await self._send_approval_notification(item.id)
                    logger.info("Recovery: notifica inviata item %d", item.id)
                except Exception as exc:
                    logger.warning("Recovery: notifica fallita item %d: %s", item.id, exc)

            async def _recover_item(iid: int = item.id) -> None:
                try:
                    decision = await self._wait_for_approval(iid)
                    await self._handle_decision(iid, decision)
                except Exception as exc:
                    logger.warning("Recovery _handle_decision item %d: %s", iid, exc)
                finally:
                    async with self._approval_lock:
                        self._approval_events.pop(iid, None)
                        self._approval_results.pop(iid, None)

            self._add_bg_task(_recover_item())
