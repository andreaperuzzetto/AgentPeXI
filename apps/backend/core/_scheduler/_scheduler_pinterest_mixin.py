"""Scheduler — Pinterest mixin: pinterest_publisher job.

Job APScheduler `pinterest_publisher` (ogni 15 min):
  - Interroga pinterest_queue WHERE status='pending' AND scheduled_at <= now
  - Chiama pinterest_agent.deliver_pin(pin) per ciascun pin in scadenza
  - On success: aggiorna status='published', published_at, pinterest_pin_id
                incrementa pinterest_boards.pin_count
  - On failure: status='failed', notifica Telegram
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("agentpexi.scheduler.pinterest")


class _PinterestMixin:
    """Scheduled job: pinterest_publisher — delivery pin dalla coda."""

    async def _run_pinterest_publisher(self) -> None:
        """Pubblica su Pinterest/Tailwind tutti i pin schedulati con slot <= now."""
        if self.pinterest_agent is None:
            logger.debug("pinterest_publisher: pinterest_agent non iniettato, skip")
            return

        db = await self.memory.get_db()
        if db is None:
            logger.warning("pinterest_publisher: DB non disponibile, skip")
            return

        now_iso = datetime.now(timezone.utc).isoformat()

        query = """
            SELECT pq.id, pq.pin_variant, pq.image_path, pq.title, pq.description,
                   pq.board_id, pq.scheduled_at, pq.delivery_method,
                   pb.board_name
              FROM pinterest_queue pq
              LEFT JOIN pinterest_boards pb ON pq.board_id = pb.board_id
             WHERE pq.status = 'pending'
               AND pq.scheduled_at <= ?
        """
        async with db.execute(query, (now_iso,)) as cur:
            rows = await cur.fetchall()

        if not rows:
            return

        logger.info("pinterest_publisher: %d pin da consegnare", len(rows))

        for row in rows:
            pin = dict(row)
            pin_queue_id = pin["id"]
            board_id = pin.get("board_id", "")

            try:
                pin_result = await self.pinterest_agent.deliver_pin(pin)

                now_str = datetime.now(timezone.utc).isoformat()
                await db.execute(
                    "UPDATE pinterest_queue SET status='published', published_at=?, pinterest_pin_id=? WHERE id=?",
                    (now_str, pin_result, pin_queue_id),
                )
                await db.execute(
                    "UPDATE pinterest_boards SET pin_count = pin_count + 1 WHERE board_id=?",
                    (board_id,),
                )
                await db.commit()

                logger.info(
                    "pinterest_publisher: pin %d → %s (board: %s)",
                    pin_queue_id, pin_result, board_id,
                )

            except Exception as exc:
                await db.execute(
                    "UPDATE pinterest_queue SET status='failed' WHERE id=?",
                    (pin_queue_id,),
                )
                await db.commit()

                await self._notify_telegram(
                    f"❌ Pinterest pin {pin_queue_id} fallito: {exc}"
                )
                logger.error(
                    "pinterest_publisher: pin %d fallito: %s", pin_queue_id, exc
                )
