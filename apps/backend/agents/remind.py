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
from typing import Any, Callable, ClassVar, Coroutine

import anthropic
import dateparser

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU, settings
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentCard, AgentResult, AgentTask, TaskStatus
from apps.backend.tools.notion_calendar import NotionCalendar

logger = logging.getLogger("agentpexi.remind")

# Step 1 — Estrazione strutturata (caveman)
_REMIND_EXTRACT_SYSTEM_TMPL = (
    "Extract reminder info from the user message. Output ONLY valid JSON, no markdown, no extra text.\n"
    '{{"text": "<cosa ricordare, senza riferimenti temporali>", "recurring": null}}\n\n'
    "RULES:\n"
    "- 'text': the WHAT to remember — strip all time expressions (e.g. 'stendere la lavatrice', not 'stendere la lavatrice tra dieci minuti')\n"
    "- 'recurring' values: null | \"daily\" | \"weekly:MON\" | \"weekly:MON,WED,FRI\" | \"monthly:15\" | \"weekdays\"\n"
    "  Set 'recurring' only for repeating patterns like 'ogni giorno', 'ogni lunedì', etc.\n"
    "- Days: MON TUE WED THU FRI SAT SUN\n"
    "- If no recurring pattern, set recurring to null"
)

_DATEPARSER_SETTINGS = {
    "PREFER_DATES_FROM": "future",
    "RETURN_AS_TIMEZONE_AWARE": False,
    "DEFAULT_LANGUAGES": ["it", "en"],
    "PREFER_DAY_OF_MONTH": "first",
}


class RemindAgent(AgentBase):
    """Gestisce reminder: creazione, lista, cancellazione, acknowledgment."""

    card: ClassVar[AgentCard] = AgentCard(
        name="remind",
        description=(
            "Gestisce reminder. "
            "Per CREARE: action='create', message=cosa, when=quando. "
            "Per LISTARE/VEDERE reminder esistenti: action='list' (niente when). "
            "Per CANCELLARE: action='cancel', reminder_id=N. "
            "Usa action='list' per domande come 'quali sono i miei reminder', "
            "'cosa devo fare', 'mostrami i promemoria'."
        ),
        input_schema={"action": "create|list|cancel|ack", "message": "str", "when": "stringa data naturale"},
        layer="personal",
        llm="haiku",
        requires_clarification=["when"],
        confidence_threshold=0.90,
    )

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        notion_calendar: NotionCalendar | None = None,
        telegram_broadcaster: Callable | None = None,
    ) -> None:
        super().__init__(
            name="remind",
            model=MODEL_HAIKU,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )
        # Se iniettato da lifespan (già ensure_database chiamato), usa direttamente
        self._notion: NotionCalendar | None = notion_calendar
        self._notion_ready: bool = notion_calendar is not None
        self._telegram_broadcast = telegram_broadcaster

    async def _notify_telegram(self, message: str) -> None:
        if self._telegram_broadcast:
            try:
                await self._telegram_broadcast(message)
            except Exception:
                pass

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
        # Haiku può usare chiavi diverse — proviamo in ordine di probabilità
        raw_input: str = (
            inp.get("text")
            or inp.get("message")
            or inp.get("query")
            or inp.get("reminder")
            or inp.get("content")
            or ""
        ).strip()
        if not raw_input:
            return self._fail(
                "Testo mancante. Es: /remind chiamare Mario domani alle 15"
            )

        # Step 1 — Estrazione testo e ricorrenza via LLM, tempo via dateparser
        await self._step("Estrazione strutturata")
        now = datetime.now()
        parse_settings = {**_DATEPARSER_SETTINGS, "RELATIVE_BASE": now}

        # Dateparser in cascata — prova più candidati finché uno ha successo:
        # 1. inp["when"]         — campo estratto dal routing (es. "tra dieci minuti")
        # 2. inp["_user_message"] — messaggio originale completo (iniettato da Pepe)
        # 3. raw_input           — testo estratto (fallback, di solito senza orario)
        _candidates: list[str] = []
        if inp.get("when"):
            _candidates.append(str(inp["when"]).strip())
        if inp.get("_user_message"):
            _candidates.append(str(inp["_user_message"]).strip())
        if raw_input:
            _candidates.append(raw_input)

        trigger_at: datetime | None = None
        _matched_candidate: str = ""
        for _cand in _candidates:
            if not _cand:
                continue
            trigger_at = dateparser.parse(_cand, settings=parse_settings)
            if trigger_at:
                _matched_candidate = _cand
                break

        logger.info(
            "Remind dateparser → %s (candidato: %r)",
            trigger_at, _matched_candidate or None,
        )

        # LLM estrae solo 'text' (cosa ricordare) e 'recurring'
        extracted = await self._extract_reminder_json(raw_input)
        text: str = (extracted.get("text") if extracted else None) or raw_input
        text = text.strip()
        recurring: str | None = (extracted.get("recurring") if extracted else None) or None

        logger.info("Remind extracted: text='%s' recurring=%s trigger_at=%s", text, recurring, trigger_at)

        if trigger_at is None and not recurring:
            return self._fail("Quando?")

        # Caso: data nel passato → chiede conferma
        soon_warning = ""
        if trigger_at is not None:
            delta = trigger_at - now
            if delta.total_seconds() < -60:
                suggested = trigger_at + timedelta(days=1)
                suggested_str = suggested.strftime("%d/%m/%Y %H:%M")
                return self._fail(
                    f"Sembra una data passata ({trigger_at.strftime('%d/%m/%Y %H:%M')}). "
                    f"Intendevi {suggested_str}?"
                )
            # Caso: entro 5 minuti — avvisa ma procede
            if 0 < delta.total_seconds() < 300:
                soon_warning = "⚠️ Il reminder è tra meno di 5 minuti.\n"

        # Step 3 — Check duplicati (solo se abbiamo un trigger_at)
        await self._step("Check duplicati")
        if trigger_at is not None:
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
            trigger_at=trigger_at.isoformat() if trigger_at else None,
            recurring_rule=recurring,
        )
        if reminder_id is None:
            return self._fail("Errore salvataggio reminder su DB")

        # Step 5 — Notion (fail-safe, solo se token configurato)
        notion_page_id: str | None = None
        _notion_token = getattr(settings, "NOTION_API_TOKEN", "")
        if self._notion and trigger_at is not None and _notion_token:
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

        # Step 6 — Notifica Telegram + Risposta
        when_str = trigger_at.strftime("%d/%m/%Y %H:%M") if trigger_at else "orario ricorrente"
        recur_str = f" (ricorrente: {recurring})" if recurring else ""
        _tg_msg = f"⏰ Reminder impostato:\n«{text}»\n📅 {when_str}{recur_str}"
        await self._notify_telegram(_tg_msg)
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

        if trigger_at:
            _voice_when = self._format_rel_time(trigger_at)
            _reply_voice = f"Ok, ti ricordo di {text} {_voice_when}."
        else:
            _reply_voice = f"Salvato come promemoria ricorrente: {text}."

        return AgentResult(
            agent_name=self.name,
            task_id=self._task_id,
            status=TaskStatus.COMPLETED,
            output_data={
                "reminder_id": reminder_id,
                "text": text,
                "trigger_at": trigger_at.isoformat() if trigger_at else None,
                "recurring": recurring,
                "notion_page_id": notion_page_id,
                "reply": reply,
                "confidence": 1.0,
            },
            reply_voice=_reply_voice,
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
            _reply_voice = "Nessun promemoria attivo."
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
            n = len(all_items)
            first_text = all_items[0].get("text", "")
            _label = "promemoria" if n == 1 else "promemoria"
            _verb  = "attivo" if n == 1 else "attivi"
            _reply_voice = f"Hai {n} {_label} {_verb}. Il prossimo è {first_text}."

        return AgentResult(
            agent_name=self.name,
            task_id=self._task_id,
            status=TaskStatus.COMPLETED,
            output_data={
                "reminders": all_items,
                "reply": reply,
                "confidence": 1.0,
            },
            reply_voice=_reply_voice,
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
            reply_voice="Fatto, promemoria cancellato.",
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
            reply_voice="Confermato.",
        )

    # ------------------------------------------------------------------
    # Helpers — estrazione JSON e check duplicati
    # ------------------------------------------------------------------

    async def _extract_reminder_json(self, raw_input: str) -> dict | None:
        """Estrae 'text' (cosa ricordare) e 'recurring' via Haiku.

        Il parsing temporale è delegato a dateparser in _create.
        Strategia first_brace/last_brace. 2 tentativi prima di fallire.
        Ritorna dict con chiavi text/recurring, oppure None.
        """
        system = _REMIND_EXTRACT_SYSTEM_TMPL

        for attempt in range(2):
            try:
                result = await self._call_llm(
                    messages=[{"role": "user", "content": raw_input}],
                    system_prompt=system,
                    max_tokens=150,
                )
                raw = (result or "").strip()
                start = raw.find("{")
                end = raw.rfind("}")
                if start == -1 or end == -1 or end <= start:
                    logger.debug("_extract_reminder_json tentativo %d: nessun JSON trovato in: %s", attempt + 1, raw[:200])
                    continue
                parsed = json.loads(raw[start : end + 1])
                if isinstance(parsed, dict) and ("text" in parsed or "recurring" in parsed):
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
            # Usa le prime 3 parole significative (>3 chars) invece della sola prima parola
            _STOPWORDS = {"di", "da", "il", "la", "lo", "le", "gli", "un", "una", "the", "a", "an"}
            words = [w.lower() for w in text.split() if len(w) > 3 and w.lower() not in _STOPWORDS]
            keywords = words[:3]
            window_start = trigger_at - timedelta(hours=1)
            window_end = trigger_at + timedelta(hours=1)
            for r in pending:
                try:
                    r_dt = datetime.fromisoformat(r.get("trigger_at", ""))
                except (ValueError, TypeError):
                    continue
                if window_start <= r_dt <= window_end:
                    r_text = r.get("text", "").lower()
                    if keywords and any(kw in r_text for kw in keywords):
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
