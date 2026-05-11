from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger("agentpexi.autopilot")


class _CommandsMixin:
    """Telegram command handlers: /run, /stop, /queue, /status."""

    async def cmd_run(self) -> str:
        status = await self._get_status()
        if status == "running" and self._running:
            pending = await self.queue.get_pending_approval()
            if pending:
                lines = ["▶️ Loop già in esecuzione.\n\n🔄 Item in attesa di risposta:"]
                for item in pending:
                    lines.append(f"  • Item {item.id} — {item.niche} [{item.product_type}]")
                lines.append("\nUsa i pulsanti nelle notifiche sopra, oppure /skip <id> o /approve <id>.")
                return "\n".join(lines)
            return "▶️ Loop già in esecuzione."
        await self.resume()
        # Controlla se ci sono item pendenti da gestire
        pending = await self.queue.get_pending_approval()
        if pending:
            lines = ["▶️ AutopilotLoop avviato.\n\n🔄 Trovati item in attesa — gestiscili per sbloccare il loop:"]
            for item in pending:
                lines.append(f"  • Item {item.id} — {item.niche} [{item.product_type}] (score={item.entry_score:.2f})")
            lines.append("\nUsa i pulsanti nelle notifiche qui sopra, oppure /skip <id> o /approve <id>.")
            return "\n".join(lines)
        return "▶️ AutopilotLoop avviato."

    async def cmd_stop(self) -> str:
        await self.stop()
        return "⏸ AutopilotLoop in pausa. Riprendi con /run."

    async def cmd_queue(self, action: str = "") -> str:
        """Mostra lo stato della coda. /queue clear per scartare tutto."""
        if action.strip().lower() == "clear":
            # Cancel all in-flight recovery tasks before clearing state
            for t in list(self._bg_tasks):
                t.cancel()
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            self._bg_tasks.clear()
            # Manda tutto in pending_approval e pending_design a discarded
            await self._db.execute(
                """
                UPDATE production_queue
                SET status='discarded', updated_at=?
                WHERE status IN ('pending_approval', 'pending_design')
                """,
                (time.time(),),
            )
            await self._db.commit()
            # Pulisce anche gli event in memoria
            self._approval_events.clear()
            self._approval_results.clear()
            return "🗑 Coda svuotata — tutti gli item pending_approval/pending_design sono ora discarded.\nRiavvia con /run per ripartire da zero."

        # Conta item per stato
        statuses = [
            "pending_design", "pending_approval", "approved",
            "scheduled", "published", "skipped", "failed", "discarded",
        ]
        lines = ["📋 *Stato coda production_queue*\n"]
        total = 0
        for st in statuses:
            items = await self.queue.get_items_by_status(st)
            if items:
                lines.append(f"  {st}: {len(items)}")
                if st in ("pending_approval", "pending_design"):
                    for it in items:
                        lines.append(f"    • id={it.id} — {it.niche} [{it.product_type}]")
                total += len(items)
        lines.append(f"\nTotale: {total} item")
        lines.append("\nUsa /queue clear per svuotare i pending e ripartire da zero.")
        return "\n".join(lines)

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

    @staticmethod
    def _tomorrow_08_00() -> datetime:
        tomorrow = datetime.now() + timedelta(days=1)
        return tomorrow.replace(hour=8, minute=0, second=0, microsecond=0)
