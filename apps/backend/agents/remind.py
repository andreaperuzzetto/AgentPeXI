"""RemindAgent — gestione reminder per il dominio Personal.

Input:
  {"action": "create"|"list"|"cancel"|"ack",
   "text": "stringa naturale completa",  # per create — testo libero, l'agente estrae struttura
   "reminder_id": 42,                    # per cancel
   "telegram_msg_id": 99}               # per ack (reply Telegram)

Pipeline create:
1. Estrazione strutturata via Ollama caveman (JSON: text, when, recurring)
2. Parse datetime via dateparser (IT/EN, future preferred)
   → passato → chiede conferma pending_action
   → None → chiede riformulazione
   → entro 5 min → avvisa ma procede
3. Check duplicati (±1h, keyword match)
4. Salvataggio SQLite
5. Salvataggio Notion (opzionale, fail-safe)
6. Risposta Telegram con conferma
7. Aggiorna personal_learning

Pipeline list:
1. Legge reminder pending + sent non acknowledged
2. Formatta tabella compatta

Pipeline cancel:
1. Aggiorna status → cancelled (DB + Notion)

Pipeline ack:
1. Aggiorna status → acknowledged via telegram_msg_id (DB + Notion)

Tutto fail-safe: errori Notion non bloccano l'operazione DB.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine

import anthropic
import dateparser

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.tools.notion_calendar import NotionCalendar

logger = logging.getLogger("agentpexi.remind")

# Step 1 — Estrazione strutturata (caveman)
_REMIND_EXTRACT_SYSTEM = (
    "Extract reminder info. Output ONLY valid JSON:\n"
    '{"text": "cosa ricordare", "when": "stringa temporale", "recurring": null}\n'
    "recurring values: null | \"daily\" | \"weekly:MON\" | \"weekly:MON,WED,FRI\" "
    "| \"monthly:15\" | \"weekdays\"\n"
    "Days: MON TUE WED THU FRI SAT SUN"
)

_DATEPARSER_SETTINGS = {
    "PREFER_DATES_FROM": "future",
    "RETURN_AS_TIMEZONE_AWARE": False,
    "LANGUAGES": ["it", "en"],
    "PREFER_DAY_OF_MONTH": "first",
}


class RemindAgent(AgentBase):
    """Gestisce reminder: creazione, lista, cancellazione, acknowledgment."""

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        notion_calendar: NotionCalendar | None = None,
    ) -> None:
        super().__init__(
            name="remind",
            model=settings.OLLAMA_MODEL,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )
        # Se iniettato da lifespan (già ensure_database chiamato), usa direttamente
        self._notion: NotionCalendar | None = notion_calendar
        self._notion_ready: bool = notion_calendar is not None

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
        raw_input: str = inp.get("text", "").strip()
        if not raw_input:
            return self._fail(
                "Testo mancante. Es: /remind chiamare Mario domani alle 15"
            )

        # Step 1 — Estrazione strutturata via Ollama caveman
        await self._step("Estrazione strutturata (Ollama)")
        extracted = await self._extract_reminder_json(raw_input)
        if extracted is None:
            return self._fail(
                "Non ho capito, puoi riformulare? "
                "Es: 'ricordami di X [quando] [ogni Y]'"
            )

        text: str = extracted.get("text", raw_input).strip()
        when_raw: str = (extracted.get("when") or "").strip()
        recurring: str | None = extracted.get("recurring") or None

        if not when_raw:
            return self._fail(
                "Non ho capito quando. Puoi essere più specifico? "
                "Es: 'domani alle 15', 'ogni lunedì alle 9'"
            )

        # Step 2 — Parse datetime via dateparser
        await self._step("Parse datetime")
        trigger_at: datetime | None = dateparser.parse(when_raw, settings=_DATEPARSER_SETTINGS)

        if trigger_at is None:
            return self._fail(
                "Non ho capito quando. Puoi essere più specifico? "
                "Es: 'domani alle 15', 'ogni lunedì alle 9'"
            )

        now = datetime.now()
        delta = trigger_at - now

        # Caso: data nel passato
        if delta.total_seconds() < 0:
            suggested = trigger_at + timedelta(days=1)
            suggested_str = suggested.strftime("%d/%m/%Y %H:%M")
            return self._fail(
                f"Sembra una data passata ({trigger_at.strftime('%d/%m/%Y %H:%M')}). "
                f"Intendevi {suggested_str}? Rispondi sì/no oppure specifica di nuovo."
            )

        # Caso: entro 5 minuti — avvisa ma procede
        soon_warning = ""
        if 0 < delta.total_seconds() < 300:
            soon_warning = "⚠️ Il reminder è tra meno di 5 minuti.\n"

        # Step 3 — Check duplicati
        await self._step("Check duplicati")
        duplicate = await self._check_duplicate(text, trigger_at)
        if duplicate:
            dup_time = duplicate.get("trigger_at", "?")
            try:
                dup_dt = datetime.fromisoformat(dup_time).strftime("%d/%m %H:%M")
            except (ValueError, TypeError):
                dup_dt = dup_time
            return self._fail(
                f"Hai già un reminder simile: «{duplicate['text']}» alle {dup_dt}. "
                f"Vuoi aggiungerlo lo stesso? Rispondi sì/no."
            )

        # Step 4 — Salva su DB
        await self._step("Salvataggio reminder su DB")
        reminder_id = await self.memory.add_reminder(
            text=text,
            trigger_at=trigger_at.isoformat(),
            recurring_rule=recurring,
        )
        if reminder_id is None:
            return self._fail("Errore salvataggio reminder su DB")

        # Step 5 — Notion (fail-safe)
        notion_page_id: str | None = None
        if self._notion:
            await self._step("Creazione pagina su Notion Calendar")
            try:
                notion_page_id = await self._notion.create_reminder(
                    text=text,
                    trigger_at=trigger_at,
                    recurring_rule=recurring,
                )
                if notion_page_id:
                    await self.memory.update_reminder_notion_id(reminder_id, notion_page_id)
            except Exception as exc:
                logger.warning("Notion create_reminder fallito (fail-safe): %s", exc)

        # Step 6 — Risposta
        when_str = trigger_at.strftime("%d/%m/%Y %H:%M")
        recur_str = f" (ricorrente: {recurring})" if recurring else ""
        notion_str = "🗒 Sincronizzato su Notion." if notion_page_id else "⚠️ Notion non disponibile, reminder salvato localmente."
        reply = (
            f"{soon_warning}"
            f"✅ Reminder salvato:\n"
            f"«{text}»\n"
            f"📅 {when_str}{recur_str}\n"
            f"{notion_str}"
        ).strip()

        # Step 7 — personal_learning (categoria "remind", segnale positivo)
        try:
            await self.memory.upsert_learning(
                agent="remind",
                pattern_type="action",
                pattern_value="create",
                signal_type="positive",
                weight_delta=0.05,
            )
        except Exception:
            pass

        return AgentResult(
            agent_name=self.name,
            task_id=self._task_id,
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
            task_id=self._task_id,
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
            task_id=self._task_id,
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
            task_id=self._task_id,
            status=TaskStatus.COMPLETED,
            output_data={
                "telegram_msg_id": telegram_msg_id,
                "reply": "✅ Reminder confermato come visto.",
                "confidence": 1.0,
            },
        )

    # ------------------------------------------------------------------
    # Helpers — estrazione JSON e check duplicati
    # ------------------------------------------------------------------

    async def _extract_reminder_json(self, raw_input: str) -> dict | None:
        """Step 1: Ollama caveman estrae JSON strutturato dal testo naturale.

        Strategia first_brace/last_brace. 2 tentativi prima di fallire.
        Ritorna dict con chiavi text/when/recurring, oppure None.
        """
        for attempt in range(2):
            try:
                result = await self._call_llm_ollama(
                    system=_REMIND_EXTRACT_SYSTEM,
                    user=raw_input,
                    max_tokens=120,
                    temperature=0.0,
                )
                raw = (result or "").strip()
                # first_brace/last_brace strategy
                start = raw.find("{")
                end = raw.rfind("}")
                if start == -1 or end == -1 or end <= start:
                    logger.debug("_extract_reminder_json tentativo %d: nessun JSON trovato", attempt + 1)
                    continue
                parsed = json.loads(raw[start : end + 1])
                if isinstance(parsed, dict) and ("text" in parsed or "when" in parsed):
                    return parsed
            except (json.JSONDecodeError, Exception) as exc:
                logger.debug("_extract_reminder_json tentativo %d fallito: %s", attempt + 1, exc)
        return None

    async def _check_duplicate(self, text: str, trigger_at: datetime) -> dict | None:
        """Step 3: Cerca reminder pending con testo simile nella finestra ±1h.

        Ritorna il primo duplicato trovato, oppure None.
        """
        try:
            pending = await self.memory.get_pending_reminders()
            if not pending:
                return None
            keyword = text.split()[0].lower() if text.split() else ""
            window_start = trigger_at - timedelta(hours=1)
            window_end = trigger_at + timedelta(hours=1)
            for r in pending:
                try:
                    r_dt = datetime.fromisoformat(r.get("trigger_at", ""))
                except (ValueError, TypeError):
                    continue
                if window_start <= r_dt <= window_end:
                    if keyword and keyword in r.get("text", "").lower():
                        return r
        except Exception as exc:
            logger.debug("_check_duplicate fallito (ignorato): %s", exc)
        return None

    # ------------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------------

    async def _step(self, description: str) -> None:
        """Convenience wrapper: delega a base._log_step per scrivere su DB e broadcast WS."""
        await self._log_step(step_type="step", description=description)

    def _fail(self, reason: str) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            task_id=self._task_id,
            status=TaskStatus.FAILED,
            output_data={"error": reason},
        )
