from __future__ import annotations

import asyncio
import logging
import time

from apps.backend.core.budget_manager import BudgetStatus
from apps.backend.core._autopilot._constants import (
    APPROVAL_TIMEOUT,
    APPROVAL_POLL,
    LOOP_SLEEP_PAUSED,
)

logger = logging.getLogger("agentpexi.autopilot")


class _ApprovalMixin:
    """Approval event registration and hybrid-wait logic."""

    # ------------------------------------------------------------------
    # Called by Telegram CallbackQueryHandler
    # ------------------------------------------------------------------

    async def register_approval(self, item_id: int, result: str) -> None:
        """Chiamato dal bot quando l'utente preme Approva/Salta.

        result: "approved" | "skipped_user"
        """
        async with self._approval_lock:
            self._approval_results[item_id] = result
            event = self._approval_events.get(item_id)
        if event is not None:
            event.set()

    # ------------------------------------------------------------------
    # Approval notification
    # ------------------------------------------------------------------

    async def _send_approval_notification(self, item_id: int) -> None:
        item = await self.queue.get_item(item_id)
        if not item:
            return

        async with self._approval_lock:
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

        # Inline keyboard — se il callable è disponibile la allega, altrimenti testo puro
        from apps.backend.telegram.callbacks import build_approval_keyboard
        keyboard = build_approval_keyboard(item_id)

        if item.thumbnail_path:
            try:
                await self._bot_send_photo(item.thumbnail_path, caption)
                return
            except Exception as exc:
                logger.warning("Invio thumbnail fallito: %s", exc)

        if self._bot_send_markup:
            try:
                await self._bot_send_markup(caption, keyboard)
                return
            except Exception as exc:
                logger.warning("Invio keyboard fallito, fallback testo: %s", exc)

        await self._bot_send(caption)

    # ------------------------------------------------------------------
    # Hybrid wait (24h con poll ogni 30s)
    # ------------------------------------------------------------------

    async def _wait_for_approval(self, item_id: int) -> str:
        deadline = time.time() + APPROVAL_TIMEOUT

        while time.time() < deadline:
            async with self._approval_lock:
                event = self._approval_events.get(item_id)
            if event:
                try:
                    await asyncio.wait_for(event.wait(), timeout=APPROVAL_POLL)
                except asyncio.TimeoutError:
                    pass
                async with self._approval_lock:
                    result = self._approval_results.get(item_id)
                if result:
                    return result
            else:
                # Event non ancora registrato — sleep breve per evitare busy-wait
                await asyncio.sleep(APPROVAL_POLL)

            # Poll DB — fallback se il segnale è già arrivato
            item = await self.queue.get_item(item_id)
            if item:
                if item.status == "approved":
                    return "approved"
                if item.status == "skipped":
                    return f"skipped_{item.skip_reason or 'user'}"
                if item.status == "discarded":
                    return "discarded"

            # Budget esaurito durante attesa
            if await self.budget.check_budget() == BudgetStatus.EXCEEDED:
                await self.queue.set_skipped(item_id, "budget")
                return "skipped_budget"

            # Fuori finestra — sleep più lungo senza perdere il posto
            if not await self.policy.is_in_availability_window():
                await asyncio.sleep(LOOP_SLEEP_PAUSED)

        await self.queue.set_skipped(item_id, "timeout")
        return "skipped_timeout"
