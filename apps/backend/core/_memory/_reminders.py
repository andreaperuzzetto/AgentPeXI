"""Reminders mixin for MemoryManager."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("agentpexi.memory")


class RemindersMixin:

    async def get_personal_recalls(self, limit: int = 10) -> list[dict]:
        """Ultimi N recall completati dall'agente recall/personal con risposta troncata."""
        cursor = await self._db.execute(
            """SELECT task_id, input_data, output_data, created_at, status
               FROM agent_logs
               WHERE agent_name = 'recall' AND domain = 'personal'
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            try:
                inp = json.loads(r["input_data"] or "{}")
                out = json.loads(r["output_data"] or "{}")
            except Exception:
                inp, out = {}, {}
            response_raw = out.get("response") or out.get("answer") or ""
            result.append({
                "task_id": r["task_id"],
                "query": inp.get("query", ""),
                "response": response_raw[:200] + ("…" if len(response_raw) > 200 else ""),
                "status": r["status"],
                "timestamp": r["created_at"],
            })
        return result

    # ------------------------------------------------------------------
    # Reminders
    # ------------------------------------------------------------------

    async def add_reminder(
        self,
        text: str,
        trigger_at: str,
        recurring_rule: str | None = None,
    ) -> int:
        """Inserisce un reminder. Restituisce l'id generato."""
        async with self._db.execute(
            """INSERT INTO reminders (text, trigger_at, recurring_rule)
               VALUES (?, ?, ?)""",
            (text, trigger_at, recurring_rule),
        ) as cur:
            await self._db.commit()
            return cur.lastrowid

    async def get_due_reminders(self) -> list[dict]:
        """Reminder con trigger_at <= now() e status pending."""
        async with self._db.execute(
            """SELECT * FROM reminders
               WHERE trigger_at <= datetime('now')
               AND status = 'pending'
               ORDER BY trigger_at ASC"""
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def mark_reminder_sent(self, reminder_id: int, telegram_msg_id: int = 0) -> None:
        await self._db.execute(
            "UPDATE reminders SET status='sent', telegram_msg_id=? WHERE id=?",
            (telegram_msg_id, reminder_id),
        )
        await self._db.commit()

    async def acknowledge_reminder(self, telegram_msg_id: int) -> bool:
        """Marca come acknowledged via message_id della reply. Restituisce True se trovato."""
        async with self._db.execute(
            "SELECT id FROM reminders WHERE telegram_msg_id=? AND status='sent'",
            (telegram_msg_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return False
        await self._db.execute(
            "UPDATE reminders SET status='acknowledged', acknowledged_at=datetime('now') WHERE id=?",
            (row["id"],),
        )
        await self._db.commit()
        return True

    async def get_reminder_notion_id(self, telegram_msg_id: int) -> str | None:
        """Restituisce notion_page_id per un reminder dato il telegram_msg_id."""
        async with self._db.execute(
            "SELECT notion_page_id FROM reminders WHERE telegram_msg_id=?",
            (telegram_msg_id,),
        ) as cur:
            row = await cur.fetchone()
        return row["notion_page_id"] if row else None

    async def get_reminder_notion_id_by_id(self, reminder_id: int) -> str | None:
        """Restituisce notion_page_id per un reminder dato il suo id (per cancel)."""
        async with self._db.execute(
            "SELECT notion_page_id FROM reminders WHERE id=?",
            (reminder_id,),
        ) as cur:
            row = await cur.fetchone()
        return row["notion_page_id"] if row else None

    async def cancel_reminder(self, reminder_id: int) -> None:
        await self._db.execute(
            "UPDATE reminders SET status='cancelled' WHERE id=?",
            (reminder_id,),
        )
        await self._db.commit()

    async def get_pending_reminders(self) -> list[dict]:
        """Tutti i reminder pending con trigger futuri, ordinati per trigger_at."""
        async with self._db.execute(
            """SELECT * FROM reminders
               WHERE status = 'pending'
               AND trigger_at > datetime('now')
               ORDER BY trigger_at ASC"""
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_sent_unacknowledged(self, hours: int = 4) -> list[dict]:
        """Reminder inviati ma non acknowledged da più di N ore."""
        async with self._db.execute(
            """SELECT * FROM reminders
               WHERE status = 'sent'
               AND acknowledged_at IS NULL
               AND trigger_at <= datetime('now', ?)
               ORDER BY trigger_at ASC""",
            (f"-{hours} hours",),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def reschedule_recurring(self, reminder_id: int) -> None:
        """Calcola il prossimo trigger_at da recurring_rule e resetta lo status a pending."""
        from datetime import datetime, timedelta

        async with self._db.execute(
            "SELECT trigger_at, recurring_rule FROM reminders WHERE id=?",
            (reminder_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row or not row["recurring_rule"]:
            return

        try:
            current = datetime.fromisoformat(row["trigger_at"])
        except ValueError:
            return

        rule: str = row["recurring_rule"]
        next_dt: datetime | None = None

        if rule == "daily":
            next_dt = current + timedelta(days=1)
        elif rule == "weekdays":
            next_dt = current + timedelta(days=1)
            while next_dt.weekday() >= 5:
                next_dt += timedelta(days=1)
        elif rule.startswith("weekly:"):
            day_names = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
            days_str = rule.split(":", 1)[1].split(",")
            target_days = sorted(day_names[d.strip()] for d in days_str if d.strip() in day_names)
            if target_days:
                candidate = current + timedelta(days=1)
                for _ in range(8):
                    if candidate.weekday() in target_days:
                        next_dt = candidate
                        break
                    candidate += timedelta(days=1)
        elif rule.startswith("monthly:"):
            try:
                day_num = int(rule.split(":", 1)[1])
                month = current.month + 1
                year = current.year + (month - 1) // 12
                month = (month - 1) % 12 + 1
                import calendar
                max_day = calendar.monthrange(year, month)[1]
                next_dt = current.replace(year=year, month=month, day=min(day_num, max_day))
            except (ValueError, IndexError):
                pass

        if next_dt:
            await self._db.execute(
                """UPDATE reminders
                   SET trigger_at=?, status='pending', telegram_msg_id=NULL, acknowledged_at=NULL
                   WHERE id=?""",
                (next_dt.isoformat(), reminder_id),
            )
            await self._db.commit()

    async def update_reminder_notion_id(self, reminder_id: int, notion_page_id: str) -> None:
        await self._db.execute(
            "UPDATE reminders SET notion_page_id=? WHERE id=?",
            (notion_page_id, reminder_id),
        )
        await self._db.commit()
