"""Personal command handlers: remind, summarize, research, feedback, urgency."""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from apps.backend.core.models import AgentTask
from apps.backend.telegram.formatters import reply_chunked

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.queue")


async def cmd_remind(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/remind <testo> alle <quando> [ogni <ricorrenza>]"""
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "Uso: `/remind <testo> alle <quando>`\n"
            "Esempio: `/remind riunione alle 15:00 domani`",
            parse_mode="Markdown",
        )
        return

    recurring = None
    if " ogni " in text:
        parts = text.split(" ogni ", 1)
        text = parts[0].strip()
        recurring = parts[1].strip()

    when = ""
    for kw in (" alle ", " entro ", " il ", " tra ", " domani", " dopodomani"):
        if kw in text.lower():
            idx = text.lower().index(kw)
            when = text[idx:].strip()
            text = text[:idx].strip()
            break
    if not when:
        when = text

    task = AgentTask(
        task_id=str(uuid.uuid4()),
        agent_name="remind",
        input_data={"action": "create", "text": f"{text} {when}".strip(), "recurring": recurring},
        source="telegram",
    )
    try:
        result = await deps.pepe.dispatch_task(task)
        reply = (
            (result.output_data or {}).get("reply")
            or (result.output_data or {}).get("error", "Errore remind.")
        )
    except Exception as exc:
        logger.error("dispatch_task remind create fallito: %s", exc)
        reply = f"⚠️ Errore agente remind: {exc}"
    await reply_chunked(update.message, reply)


async def cmd_remind_list(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/reminders — lista reminder attivi."""
    task = AgentTask(
        task_id=str(uuid.uuid4()),
        agent_name="remind",
        input_data={"action": "list"},
        source="telegram",
    )
    try:
        result = await deps.pepe.dispatch_task(task)
        reply = (
            (result.output_data or {}).get("reply")
            or (result.output_data or {}).get("error", "Errore remind.")
        )
    except Exception as exc:
        logger.error("dispatch_task remind list fallito: %s", exc)
        reply = f"⚠️ Errore agente remind: {exc}"
    await reply_chunked(update.message, reply)


async def cmd_summarize(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/summarize <url|testo> [short] — riassume URL o testo."""
    args = context.args or []
    mode = "short" if args and args[-1].lower() == "short" else "detailed"
    if mode == "short":
        args = args[:-1]

    content = " ".join(args).strip()
    file_id = None
    if update.message.document:
        file_id = update.message.document.file_id

    if not content and not file_id:
        await update.message.reply_text(
            "Uso: `/summarize <url> [short]` oppure inoltra un PDF/TXT al bot.\n"
            "Esempio: `/summarize https://example.com/article short`",
            parse_mode="Markdown",
        )
        return

    if file_id:
        source_type, content = "file", file_id
    elif content.startswith("http"):
        source_type = "url"
    else:
        source_type = "text"

    length = "brief" if mode == "short" else "normal"
    await update.message.reply_text("📄 Sto leggendo e riassumendo…")
    task = AgentTask(
        task_id=str(uuid.uuid4()),
        agent_name="summarize",
        input_data={"source_type": source_type, "content": content, "length": length, "save": True},
        source="telegram",
    )
    try:
        result = await deps.pepe.dispatch_task(task)
        reply = (
            (result.output_data or {}).get("reply")
            or (result.output_data or {}).get("error", "Errore summarize.")
        )
    except Exception as exc:
        logger.error("dispatch_task summarize fallito: %s", exc)
        reply = f"⚠️ Errore agente summarize: {exc}"
    await reply_chunked(update.message, reply)


async def cmd_research(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/research <query> [quick] — ricerca web strutturata."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Uso: `/research <domanda> [quick]`\n"
            "Esempio: `/research vantaggi regime forfettario`",
            parse_mode="Markdown",
        )
        return

    mode = "quick" if args[-1].lower() == "quick" else "deep"
    if mode == "quick":
        args = args[:-1]
    query = " ".join(args).strip()

    await update.message.reply_text(f"🔍 Ricerco: «{query}»…")
    task = AgentTask(
        task_id=str(uuid.uuid4()),
        agent_name="research_personal",
        input_data={"query": query, "depth": mode},
        source="telegram",
    )
    try:
        result = await deps.pepe.dispatch_task(task)
        reply = (
            (result.output_data or {}).get("response")
            or (result.output_data or {}).get("error", "Errore research.")
        )
    except Exception as exc:
        logger.error("dispatch_task research_personal fallito: %s", exc)
        reply = f"⚠️ Errore agente research: {exc}"
    await reply_chunked(update.message, reply)


async def cmd_feedback(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/feedback <positivo|negativo> <parola_chiave>"""
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Uso: `/feedback positivo|negativo <parola_chiave>`\n"
            "Esempio: `/feedback positivo scadenza`",
            parse_mode="Markdown",
        )
        return

    signal_raw = args[0].lower()
    keyword = " ".join(args[1:]).lower().strip()

    if signal_raw in ("positivo", "positive", "sì", "si", "yes"):
        signal = "positive"
    elif signal_raw in ("negativo", "negative", "no"):
        signal = "negative"
    else:
        await update.message.reply_text(
            "Segnale non riconosciuto. Usa `positivo` o `negativo`.",
            parse_mode="Markdown",
        )
        return

    weight_delta = 0.1 if signal == "positive" else -0.1
    try:
        await deps.pepe.memory.upsert_learning(
            agent="urgency",
            pattern_type="keyword",
            pattern_value=keyword,
            signal_type=signal,
            weight_delta=weight_delta,
        )
        icon = "✅" if signal == "positive" else "🔕"
        reply = (
            f"{icon} Capito. Quando vedo «{keyword}» lo tratterò come "
            f"{'prioritario' if signal == 'positive' else 'rumore'}."
        )
    except Exception as exc:
        reply = f"❌ Errore salvataggio feedback: {exc}"
    await update.message.reply_text(reply)


async def cmd_urgency(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/urgency add <keyword>"""
    args = context.args or []
    if not args or args[0].lower() != "add" or len(args) < 2:
        await update.message.reply_text(
            "Uso: `/urgency add <keyword>`\n"
            "Esempio: `/urgency add scadenza`\n\n"
            "Insegna a Pepe quali parole indicano sempre urgenza alta.",
            parse_mode="Markdown",
        )
        return

    keyword = " ".join(args[1:]).lower().strip()
    if not keyword:
        await update.message.reply_text(
            "Specifica la keyword dopo `add`.", parse_mode="Markdown"
        )
        return

    try:
        await deps.pepe.memory.upsert_learning(
            agent="urgency",
            pattern_type="keyword",
            pattern_value=keyword,
            signal_type="explicit_positive",
            weight_delta=0.3,
        )
        await update.message.reply_text(
            f"🔴 «{keyword}» aggiunta come keyword ad alta urgenza.\n"
            f"D'ora in poi i messaggi che la contengono saranno trattati come HIGH."
        )
    except Exception as exc:
        await update.message.reply_text(f"❌ Errore salvataggio: {exc}")
