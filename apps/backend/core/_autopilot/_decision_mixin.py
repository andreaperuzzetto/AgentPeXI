from __future__ import annotations

import logging

logger = logging.getLogger("agentpexi.autopilot")


class _DecisionMixin:
    """Decision handling: approved / skipped / paused states."""

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
