"""RemindAgent — gestione reminder per il dominio Personal.

Input:
  {"action": "create"|"list"|"cancel"|"ack",
   "text": "...",          # per create
   "when": "ISO8601|nat",  # per create, stringa naturale o ISO
   "recurring": "daily|weekdays|weekly:Mon,Wed|monthly:15",  # opz per create
   "reminder_id": 42}      # per cancel/ack

Pipeline create:
1. Parsa "when" (Ollama caveman → ISO8601)
2. Salva su DB reminders
3. Crea pagina su Notion Calendar (fail-safe)
4. Conferma a Telegram

Pipeline list:
1. Legge reminder pending + sent non acknowledged
2. Formatta tabella compatta

Pipeline cancel:
1. Aggiorna status → cancelled (DB + Notion)

Pipeline ack:
1. Aggiorna status → acknowledged (DB + Notion)

Tutto fail-safe: errori Notion non bloccano l'operazione DB.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

import anthropic

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.tools.notion_calendar import NotionCalendar

logger = logging.getLogger("agentpexi.remind")

# Prompt caveman per parsing data/ora naturale → ISO8601
_PARSE_WHEN_SYSTEM = (
    "Parse datetime. Output ONLY: YYYY-MM-DDTHH:MM:SS\n"
    "If relative (domani, tra 2 ore...) use context date.\n"
    "If time missing, use 09:00.\n"
    "If unparseable: INVALID"
)


class RemindAgent(AgentBase):
    """Gestisce reminder: creazione, lista, cancellazione, acknowledgment."""

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
    ) -> None:
        super().__init__(
            name="remind",
            model=settings.OLLAMA_MODEL,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )
        self._notion: NotionCalendar | None = None
        self._notion_ready: bool = False

    # ------------------------------------------------------------------
    # Init Notion (lazy, una volta sola)
    # ------------------------------------------------------------------

    async def _ensure_notion(self) -> None:
        """Inizializza NotionCalendar la prima volta che serve."""
        if self._notion_ready:
            return
        token = getattr(settings, "NOTION_API_TOKEN", "")
        if token:
            self._notion = NotionCalendar(token=token)
            await self._notion.ensure_database()
        self._notion_ready = True

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        inp = task.input_data or {}
        action = inp.get("action", "create")

        await self._ensure_notion()
        await self._step(f"Azione: {action}")

        if action == "create":
            return await self._create(inp)
        elif action == "list":
            return await self._list()
        elif action == "cancel":
            return await self._cancel(inp)
        elif action == "ack":
            return await self._ack(inp)
        else:
            return AgentResult(
                agent_name=self.name,
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                output_data={"error": f"Azione non riconosciuta: {action}"},
            )

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    async def _create(self, inp: dict) -> AgentResult:
        text: str = inp.get("text", "").strip()
        when_raw: str = inp.get("when", "").strip()
        recurring: str | None = inp.get("recurring") or None

        if not text:
            return self._fail("text mancante per creare un reminder")
        if not when_raw:
            return self._fail("when mancante — specifica data/ora del reminder")

        # 1. Parsa when → ISO8601
        await self._step("Parsing data/ora con Ollama")
        trigger_at = await self._parse_when(when_raw)
        if trigger_at is None:
            return self._fail(f"Non riesco a capire la data/ora: «{when_raw}»")

        # 2. Salva su DB (add_reminder vuole str ISO8601, non datetime)
        await self._step("Salvataggio reminder su DB")
        reminder_id = await self.memory.add_reminder(
            text=text,
            trigger_at=trigger_at.isoformat(),
            recurring_rule=recurring,
        )
        if reminder_id is None:
            return self._fail("Errore salvataggio reminder su DB")

        # 3. Notion (fail-safe)
        notion_page_id: str | None = None
        if self._notion:
            await self._step("Creazione pagina su Notion Calendar")
            notion_page_id = await self._notion.create_reminder(
                text=text,
                trigger_at=trigger_at,
                recurring_rule=recurring,
            )
            if notion_page_id:
                await self.memory.update_reminder_notion_id(reminder_id, notion_page_id)

        when_str = trigger_at.strftime("%d/%m/%Y %H:%M")
        recur_str = f" (ricorrente: {recurring})" if recurring else ""
        reply = (
            f"✅ Reminder salvato:\n"
            f"«{text}»\n"
            f"📅 {when_str}{recur_str}\n"
            f"{'🗒 Sincronizzato su Notion.' if notion_page_id else ''}"
        ).strip()

        return AgentResult(
            agent_name=self.name,
            task_id="",
            status=TaskStatus.COMPLETED,
            output_data={
                "reminder_id": reminder_id,
                "text": text,
                "trigger_at": trigger_at.isoformat(),
                "recurring": recurring,
                "notion_page_id": notion_page_id,
                "reply": reply,
                "confidence": 1.0,
            },
        )

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    async def _list(self) -> AgentResult:
        await self._step("Lettura reminder attivi")
        pending = await self.memory.get_pending_reminders()
        unacked = await self.memory.get_sent_unacknowledged()

        all_items = sorted(
            (pending or []) + (unacked or []),
            key=lambda r: r.get("trigger_at", ""),
        )

        if not all_items:
            reply = "Nessun reminder attivo."
        else:
            lines = ["📋 Reminder attivi:\n"]
            for r in all_items:
                dt_str = r.get("trigger_at", "?")
                try:
                    dt = datetime.fromisoformat(dt_str)
                    dt_str = dt.strftime("%d/%m %H:%M")
                except ValueError:
                    pass
                status_icon = "⏰" if r.get("status") == "pending" else "📨"
                recur = f" [{r['recurring_rule']}]" if r.get("recurring_rule") else ""
                lines.append(f"{status_icon} [{r['id']}] {dt_str}{recur} — {r['text']}")
            reply = "\n".join(lines)

        return AgentResult(
            agent_name=self.name,
            task_id="",
            status=TaskStatus.COMPLETED,
            output_data={
                "reminders": all_items,
                "reply": reply,
                "confidence": 1.0,
            },
        )

    # ------------------------------------------------------------------
    # cancel
    # ------------------------------------------------------------------

    async def _cancel(self, inp: dict) -> AgentResult:
        reminder_id = inp.get("reminder_id")
        if reminder_id is None:
            return self._fail("reminder_id mancante per la cancellazione")

        await self._step(f"Cancellazione reminder {reminder_id}")
        await self.memory.cancel_reminder(int(reminder_id))

        if self._notion:
            # cancel usa reminder_id direttamente (non telegram_msg_id)
            notion_id = await self.memory.get_reminder_notion_id_by_id(int(reminder_id))
            if notion_id:
                await self._notion.update_status(notion_id, "Cancelled")

        return AgentResult(
            agent_name=self.name,
            task_id="",
            status=TaskStatus.COMPLETED,
            output_data={
                "reminder_id": reminder_id,
                "reply": f"✅ Reminder {reminder_id} cancellato.",
                "confidence": 1.0,
            },
        )

    # ------------------------------------------------------------------
    # ack
    # ------------------------------------------------------------------

    async def _ack(self, inp: dict) -> AgentResult:
        # L'acknowledgment arriva via reply Telegram: inp contiene telegram_msg_id
        telegram_msg_id = inp.get("telegram_msg_id")
        if telegram_msg_id is None:
            return self._fail("telegram_msg_id mancante per l'acknowledgment")

        await self._step(f"Acknowledgment via Telegram msg {telegram_msg_id}")
        found = await self.memory.acknowledge_reminder(int(telegram_msg_id))

        if not found:
            return self._fail(f"Nessun reminder sent trovato per msg_id {telegram_msg_id}")

        if self._notion:
            notion_id = await self.memory.get_reminder_notion_id(int(telegram_msg_id))
            if notion_id:
                await self._notion.update_status(notion_id, "Done")

        return AgentResult(
            agent_name=self.name,
            task_id="",
            status=TaskStatus.COMPLETED,
            output_data={
                "telegram_msg_id": telegram_msg_id,
                "reply": "✅ Reminder confermato come visto.",
                "confidence": 1.0,
            },
        )

    # ------------------------------------------------------------------
    # Parsing data/ora
    # ------------------------------------------------------------------

    async def _parse_when(self, when_raw: str) -> datetime | None:
        """Converte stringa naturale/ISO in datetime-aware via Ollama.

        Fallback: prova parsing diretto ISO8601 prima di chiamare Ollama.
        """
        # Prova prima parsing diretto (già ISO8601)
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(when_raw, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        # Ollama per linguaggio naturale
        now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        try:
            result = await self._call_llm_ollama(
                system=_PARSE_WHEN_SYSTEM,
                user=f"NOW: {now_str}\nINPUT: {when_raw}",
                max_tokens=25,
                temperature=0.0,
            )
            raw = result.strip()
            if raw == "INVALID" or not raw:
                return None
            # Parsa l'output dell'LLM
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
                try:
                    dt = datetime.strptime(raw, fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
            return None
        except Exception as exc:
            logger.warning("_parse_when Ollama fallito: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------------

    async def _step(self, description: str) -> None:
        """Emette un WS step e incrementa il counter."""
        self._step_counter += 1
        await self._broadcast({
            "type": "agent_step",
            "agent": self.name,
            "step": self._step_counter,
            "description": description,
        })

    def _fail(self, reason: str) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            task_id="",
            status=TaskStatus.FAILED,
            output_data={"error": reason},
        )
