"""Handler Telegram — comandi di sistema e infrastruttura.

Comandi: /status, /pause, /resume, /ask, /new, /report,
         /retry, /resume_agent, /personal, /etsy, /screen,
         /list, /wiki
Handler messaggi: testo generico, vocale
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from apps.backend.core.config import settings
from apps.backend.core.domains import DOMAIN_ETSY
from apps.backend.telegram.formatters import md_escape, reply_chunked

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.system")


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

async def cmd_status(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/status — stato del sistema + AutopilotLoop."""
    lines = ["🟢 *Sistema AgentPeXI*\n"]

    if deps.autopilot_loop is not None:
        try:
            loop_status = await deps.autopilot_loop.cmd_status()
            lines.append(loop_status)
            lines.append("")
        except Exception as exc:
            lines.append(f"⚠️ Loop status errore: {exc}\n")

    statuses = deps.pepe.get_agent_statuses()
    if statuses:
        status_icons = {"idle": "⚪", "running": "🔵", "error": "🔴"}
        lines.append("*Agenti:*")
        for name, status in statuses.items():
            icon = status_icons.get(status, "❓")
            lines.append(f"{icon} {name}: {status}")
        lines.append("")

    queue_size = deps.pepe._queue.qsize()
    lines.append(f"📋 Task Pepe in coda: {queue_size}")

    domain = deps.pepe.get_active_domain()
    domain_name = domain.name if domain else "personal"
    domain_icon = "🏪" if domain_name == "etsy_store" else "🧠"
    lines.append(f"{domain_icon} *Dominio attivo*: {domain_name}")

    if deps.pepe.mock_mode:
        lines.append("\n🟡 *MOCK MODE ATTIVO*")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /report
# ---------------------------------------------------------------------------

async def cmd_report(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/report — chiede report a Pepe."""
    session_id = str(update.effective_chat.id)
    reply = await deps.pepe.handle_user_message(
        "Dammi un report sullo stato attuale del sistema e delle attività recenti.",
        source="telegram",
        session_id=session_id,
    )
    await reply_chunked(update.message, reply)


# ---------------------------------------------------------------------------
# /pause / /resume
# ---------------------------------------------------------------------------

async def cmd_pause(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/pause — ferma i worker di Pepe."""
    await deps.pepe.stop()
    await update.message.reply_text("⏸️ Worker Pepe fermati.")


async def cmd_resume(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/resume — riavvia i worker di Pepe."""
    await deps.pepe.start()
    await update.message.reply_text("▶️ Worker Pepe riavviati.")


# ---------------------------------------------------------------------------
# /ask
# ---------------------------------------------------------------------------

async def cmd_ask(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/ask <domanda> — chiede qualcosa a Pepe."""
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Uso: /ask <la tua domanda>")
        return
    session_id = str(update.effective_chat.id)
    reply = await deps.pepe.handle_user_message(text, source="telegram", session_id=session_id)
    await reply_chunked(update.message, reply)


# ---------------------------------------------------------------------------
# /new
# ---------------------------------------------------------------------------

async def cmd_new(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/new — nuova sessione, azzera conversazione precedente."""
    session_id = str(update.effective_chat.id)
    await deps.pepe.memory.clear_session(session_id)
    await update.message.reply_text(
        "✅ Nuova sessione avviata. La conversazione precedente è stata archiviata."
    )


# ---------------------------------------------------------------------------
# /retry / /resume_agent
# ---------------------------------------------------------------------------

async def cmd_retry(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/retry [task_id] — riprova ultimo task fallito o uno specifico."""
    task_id = context.args[0] if context.args else None
    try:
        result = await deps.pepe.retry_task(task_id=task_id)
        await update.message.reply_text(
            f"✅ Retry completato: {result.agent_name} → {result.status.value}"
        )
    except ValueError as exc:
        await update.message.reply_text(f"❌ {exc}")
    except RuntimeError as exc:
        await update.message.reply_text(f"⚠️ {exc}")


async def cmd_resume_agent(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/resume_agent <name> — riattiva agente sospeso."""
    if not context.args:
        await update.message.reply_text("Uso: /resume_agent <nome_agente>")
        return
    name = context.args[0]
    if deps.pepe.resume_agent(name):
        await update.message.reply_text(f"✅ Agente *{name}* riattivato.", parse_mode="Markdown")
    else:
        await update.message.reply_text(
            f"❌ Agente *{name}* non trovato o non sospeso.", parse_mode="Markdown"
        )


# ---------------------------------------------------------------------------
# /personal / /etsy
# ---------------------------------------------------------------------------

async def cmd_personal(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/personal — passa al dominio Personal."""
    deps.pepe.set_active_domain(None)
    if deps.pepe._ws_broadcast:
        await deps.pepe._ws_broadcast({
            "type": "system_status",
            "domain": "personal",
            "message": "Dominio Personal attivato",
        })
    await update.message.reply_text(
        "🧠 *Dominio business disattivato*\n\n"
        "Gli agenti personal sono sempre disponibili — "
        "usa /etsy per attivare la pipeline Etsy.",
        parse_mode="Markdown",
    )


async def cmd_etsy(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/etsy — torna al dominio Etsy store."""
    deps.pepe.set_active_domain(DOMAIN_ETSY)
    if deps.pepe._ws_broadcast:
        await deps.pepe._ws_broadcast({
            "type": "system_status",
            "domain": "etsy_store",
            "message": "Dominio Etsy attivato",
        })
    await update.message.reply_text(
        "🏪 *Dominio Etsy attivo*\n\n"
        "Gli agenti personal rimangono disponibili in qualsiasi momento.",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# /screen
# ---------------------------------------------------------------------------

async def cmd_screen(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/screen [on|off|status] — gestione Screen Watcher."""
    arg = (context.args[0].lower() if context.args else "status")

    if not deps.screen_watcher:
        await update.message.reply_text(
            "❌ *Screen Watcher non disponibile*\n\n"
            "Il servizio non è partito all'avvio del server.\n"
            "Cause probabili: `mss`, `pyobjc` o `Vision` non installati.\n\n"
            "Controlla i log del server per il dettaglio dell'errore.",
            parse_mode="Markdown",
        )
        return

    if arg == "off":
        deps.screen_watcher.pause()
        await update.message.reply_text(
            "⏸️ *Screen Watcher in pausa*\n\nNon catturerò più lo schermo.\n"
            "Usa /screen on per riprendere.",
            parse_mode="Markdown",
        )
    elif arg == "on":
        deps.screen_watcher.resume()
        await update.message.reply_text(
            "▶️ *Screen Watcher attivo*\n\nRiprendo a monitorare lo schermo.",
            parse_mode="Markdown",
        )
    else:
        st = deps.screen_watcher.get_status()
        icon = "▶️" if st["active"] else "⏸️"
        stato = "Attivo" if st["active"] else "In pausa"
        last_app = md_escape(st["last_capture_app"] or "—")
        last_time = st["last_capture_time"] or "—"
        if last_time and last_time != "—":
            try:
                from datetime import datetime
                last_time = datetime.fromisoformat(last_time).strftime("%d/%m %H:%M")
            except Exception:
                pass
        await update.message.reply_text(
            f"{icon} *Screen Watcher*: {stato}\n\n"
            f"📸 Catture oggi: {st['captures_today']}\n"
            f"🖥️ Ultima app: {last_app}\n"
            f"🕐 Ultima cattura: {last_time}\n\n"
            "Comandi: `/screen on` · `/screen off` · `/screen status`",
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# /list
# ---------------------------------------------------------------------------

async def cmd_list(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/list — lista tutti i comandi disponibili."""
    domain = deps.pepe.get_active_domain()
    domain_name = domain.name if domain else "personal"
    domain_icon = "🧠" if not domain else "🏪"
    lines = [
        f"📋 *Comandi AgentPeXI* ({domain_icon} {domain_name})\n",
        "*— Sistema —*",
        "/status — stato agenti, coda, dominio attivo",
        "/list — questo messaggio",
        "/pause — ferma i worker",
        "/resume — riavvia i worker",
        "/new — nuova sessione (azzera conversazione)",
        "",
        "*— Dominio —*",
        "/personal — passa al dominio Personal",
        "/etsy — passa al dominio Etsy store",
        "/screen [on|off|status] — gestione Screen Watcher",
        "",
        "*— Etsy / AutopilotLoop —*",
        "/run — avvia AutopilotLoop (pipeline autonoma)",
        "/stop — metti in pausa AutopilotLoop",
        "/niche <nicchia> [quick] — Research singola nicchia (deep di default)",
        "/niche <n1> | <n2> [quick] — confronto multi-nicchia (separatore | o ,, max 5)",
        "/design <nicchia> [png] — Design Agent standalone (PDF di default)",
        "/analytics — esegue subito il job analytics",
        "/finance — genera report economico",
        "/listings — lista ultimi 10 listing",
        "/wiki [stats|query|lint|health] — knowledge base wiki",
        "",
        "*— Personal —*",
        "/remind <testo> alle <quando> — crea reminder",
        "/reminders — lista reminder attivi",
        "/summarize <url|testo> [short] — riassumi contenuto",
        "/research <domanda> [quick] — ricerca web strutturata",
        "/feedback positivo|negativo <keyword> — insegna al sistema",
        "/urgency add <keyword> — aggiungi keyword ad alta urgenza",
        "",
        "*— Interazione —*",
        "/ask <domanda> — chiede qualcosa a Pepe",
        "/report — report stato sistema",
        "/retry [task\\_id] — riprova ultimo task fallito",
        "/resume\\_agent <nome> — riattiva agente sospeso",
        "",
        "💬 Oppure scrivi direttamente — Pepe risponde.",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /wiki
# ---------------------------------------------------------------------------

async def cmd_wiki(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/wiki [stats|query <testo>|lint [etsy|personal]|health]"""
    wiki = getattr(deps.pepe, "wiki", None)
    if wiki is None:
        await update.message.reply_text("❌ WikiManager non inizializzato. Controlla i log.")
        return

    args = context.args or []
    sub = args[0].lower() if args else "stats"

    if sub == "stats":
        try:
            stats = await wiki.get_stats()
            lines = [
                "📚 *Wiki — Statistiche*\n",
                f"🏪 Etsy nicchie: {stats.get('etsy_niches', 0)}",
                f"📊 Etsy pattern: {stats.get('etsy_patterns', 0)}",
                f"🧠 Personal file: {stats.get('personal_files', 0)}",
                f"📥 Raw totale: {stats.get('total_raw', 0)}",
                f"⏳ Raw pending: {stats.get('pending_raw', 0)}",
            ]
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as exc:
            await update.message.reply_text(f"❌ Errore stats: {exc}")

    elif sub == "query":
        q = " ".join(args[1:]).strip()
        if not q:
            await update.message.reply_text(
                "Uso: `/wiki query <testo>`\nEsempio: `/wiki query weekly planner trends`",
                parse_mode="Markdown",
            )
            return
        await update.message.reply_text(f"🔍 Query wiki Etsy: «{q}»…")
        try:
            result = await wiki.query("etsy", q, deps.pepe.client)
            if result:
                await reply_chunked(update.message, f"📚 *Wiki result*\n\n{result}")
            else:
                await update.message.reply_text("Nessun risultato nella wiki per questa query.")
        except Exception as exc:
            await update.message.reply_text(f"❌ Errore query: {exc}")

    elif sub == "lint":
        domain = (
            args[1].lower()
            if len(args) > 1 and args[1].lower() in ("etsy", "personal")
            else "etsy"
        )
        llm = deps.pepe._local_client if domain == "personal" else deps.pepe.client
        await update.message.reply_text(f"🔍 Lint wiki *{domain}*…", parse_mode="Markdown")
        try:
            report = await wiki.lint(domain, llm)
            header = f"📋 *Wiki lint — {domain}*\n\n"
            await reply_chunked(update.message, header + (report or "Nessun problema trovato."))
        except Exception as exc:
            await update.message.reply_text(f"❌ Errore lint: {exc}")

    elif sub == "health":
        await update.message.reply_text(
            "⏳ Wiki health check in avvio (compact + lint + update_index)…"
        )
        try:
            if deps.scheduler:
                task = asyncio.create_task(
                    deps.scheduler._run_wiki_health_check(),
                    name="wiki_health_manual",
                )
                task.add_done_callback(
                    lambda t: logger.error("Wiki health check fallito: %s", t.exception())
                    if not t.cancelled() and t.exception() else None
                )
            else:
                llm_etsy     = deps.pepe.client
                llm_personal = deps.pepe._local_client
                for domain, llm in (("etsy", llm_etsy), ("personal", llm_personal)):
                    await wiki.compact_wiki(domain, llm)
                    await wiki.update_index(domain, llm)
                await update.message.reply_text("✅ Health check completato (senza scheduler).")
        except Exception as exc:
            await update.message.reply_text(f"❌ Health check fallito: {exc}")

    else:
        await update.message.reply_text(
            "📚 *Wiki — Comandi disponibili*\n\n"
            "`/wiki stats` — statistiche aggregate\n"
            "`/wiki query <testo>` — query knowledge base Etsy\n"
            "`/wiki lint [etsy|personal]` — lint wikilinks e raw pending\n"
            "`/wiki health` — esegui health check manuale",
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# Handler testo generico
# ---------------------------------------------------------------------------

async def handle_text(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Messaggio testo → Pepe. Se reply su un messaggio del bot, tenta ACK reminder."""
    text = update.message.text
    session_id = str(update.effective_chat.id)

    if update.message.reply_to_message is not None:
        replied_msg_id = update.message.reply_to_message.message_id
        acked = await deps.pepe.memory.acknowledge_reminder(replied_msg_id)
        if acked:
            notion_page_id = await deps.pepe.memory.get_reminder_notion_id(replied_msg_id)
            if notion_page_id and getattr(settings, "NOTION_API_TOKEN", ""):
                try:
                    from apps.backend.tools.notion_calendar import NotionCalendar
                    nc = NotionCalendar(token=settings.NOTION_API_TOKEN)
                    await nc.update_status(notion_page_id, "Done")
                except Exception as exc:
                    logger.debug("ACK Notion update fallito (fail-safe): %s", exc)
            await update.message.reply_text("✅ Reminder confermato.")
            return

    reply = await deps.pepe.handle_user_message(text, source="telegram", session_id=session_id)
    await reply_chunked(update.message, reply)


# ---------------------------------------------------------------------------
# Handler vocale
# ---------------------------------------------------------------------------

async def handle_voice(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Vocale → STT → Pepe → TTS → risposta audio."""
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
    await file.download_to_drive(tmp_path)

    try:
        transcription = await _transcribe(tmp_path)
        if not transcription:
            await update.message.reply_text("🔇 Non ho capito l'audio, riprova.")
            return

        await update.message.reply_text(
            f"🎤 _{md_escape(transcription)}_", parse_mode="Markdown"
        )

        session_id = str(update.effective_chat.id)
        reply = await deps.pepe.handle_user_message(
            transcription, source="telegram", session_id=session_id
        )
        await update.message.reply_text(reply)

        audio_bytes = await _synthesize(reply)
        if audio_bytes:
            await update.message.reply_voice(voice=audio_bytes)

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def _transcribe(audio_path: str) -> str:
    """STT via faster-whisper (lazy import)."""
    try:
        from apps.backend.voice.stt import transcribe
        return await transcribe(audio_path)
    except ImportError:
        logger.warning("Modulo voice.stt non disponibile — STT disabilitato")
        return ""
    except Exception as exc:
        logger.error("Errore STT: %s", exc)
        return ""


async def _synthesize(text: str) -> bytes | None:
    """TTS via ElevenLabs (lazy import)."""
    try:
        from apps.backend.voice.tts import synthesize
        return await synthesize(text)
    except ImportError:
        logger.debug("Modulo voice.tts non disponibile — TTS disabilitato")
        return None
    except Exception as exc:
        logger.error("Errore TTS: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(
    app: Application,
    deps: "BotDependencies",
    chat_filter,
) -> None:
    """Registra tutti gli handler di sistema nell'Application."""
    from functools import partial

    add = app.add_handler

    add(CommandHandler("status",       partial(cmd_status,       deps), filters=chat_filter))
    add(CommandHandler("report",       partial(cmd_report,       deps), filters=chat_filter))
    add(CommandHandler("pause",        partial(cmd_pause,        deps), filters=chat_filter))
    add(CommandHandler("resume",       partial(cmd_resume,       deps), filters=chat_filter))
    add(CommandHandler("ask",          partial(cmd_ask,          deps), filters=chat_filter))
    add(CommandHandler("new",          partial(cmd_new,          deps), filters=chat_filter))
    add(CommandHandler("retry",        partial(cmd_retry,        deps), filters=chat_filter))
    add(CommandHandler("resume_agent", partial(cmd_resume_agent, deps), filters=chat_filter))
    add(CommandHandler("personal",     partial(cmd_personal,     deps), filters=chat_filter))
    add(CommandHandler("etsy",         partial(cmd_etsy,         deps), filters=chat_filter))
    add(CommandHandler("screen",       partial(cmd_screen,       deps), filters=chat_filter))
    add(CommandHandler("list",         partial(cmd_list,         deps), filters=chat_filter))
    add(CommandHandler("wiki",         partial(cmd_wiki,         deps), filters=chat_filter))

    # Messaggi vocali
    add(MessageHandler(chat_filter & filters.VOICE, partial(handle_voice, deps)))
    # Testo generico — deve essere l'ultimo handler registrato
    add(MessageHandler(chat_filter & filters.TEXT & ~filters.COMMAND, partial(handle_text, deps)))
