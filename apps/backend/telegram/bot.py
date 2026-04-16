"""Bot Telegram — interfaccia Telegram per AgentPeXI.

Integrazione asincrona con python-telegram-bot v20+.
NON usa run_polling() — si integra nel loop asyncio di FastAPI.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from apps.backend.core.config import settings
from apps.backend.core.domains import DOMAIN_ETSY, DOMAIN_PERSONAL

if TYPE_CHECKING:
    from apps.backend.core.pepe import Pepe
    from apps.backend.core.scheduler import Scheduler
    from apps.backend.screen.watcher import ScreenWatcher

logger = logging.getLogger("agentpexi.telegram")


class TelegramBot:
    """Bot Telegram per AgentPeXI — comandi + chat + vocale."""

    def __init__(
        self,
        pepe: Pepe,
        scheduler: Scheduler | None = None,
        screen_watcher: ScreenWatcher | None = None,
    ) -> None:
        self.pepe = pepe
        self.scheduler = scheduler
        self.screen_watcher = screen_watcher
        self._app: Application | None = None

        # Filtro: rispondi solo ad Andrea
        self._chat_filter = filters.Chat(chat_id=int(settings.TELEGRAM_CHAT_ID)) if settings.TELEGRAM_CHAT_ID else filters.ALL

    # ------------------------------------------------------------------
    # Startup / shutdown (chiamati dal lifespan FastAPI)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Avvia il bot nel loop asyncio corrente (no run_polling)."""
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning("TELEGRAM_BOT_TOKEN non configurato — bot Telegram disattivato")
            return

        self._app = (
            Application.builder()
            .token(settings.TELEGRAM_BOT_TOKEN)
            .build()
        )

        # Registra handler
        self._register_handlers()

        # Registra notifier in Pepe
        self.pepe.set_telegram_notifier(self._send_notification)

        # Avvio asincrono (nello stesso event loop di FastAPI)
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Bot Telegram avviato")

    async def stop(self) -> None:
        """Shutdown graceful."""
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
        logger.info("Bot Telegram fermato")

    # ------------------------------------------------------------------
    # Registrazione handler
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        assert self._app is not None
        add = self._app.add_handler

        add(CommandHandler("status", self._cmd_status, filters=self._chat_filter))
        add(CommandHandler("report", self._cmd_report, filters=self._chat_filter))
        add(CommandHandler("pause", self._cmd_pause, filters=self._chat_filter))
        add(CommandHandler("resume", self._cmd_resume, filters=self._chat_filter))
        add(CommandHandler("ask", self._cmd_ask, filters=self._chat_filter))
        add(CommandHandler("listings", self._cmd_listings, filters=self._chat_filter))
        add(CommandHandler("retry", self._cmd_retry, filters=self._chat_filter))
        add(CommandHandler("resume_agent", self._cmd_resume_agent, filters=self._chat_filter))
        add(CommandHandler("new", self._cmd_new, filters=self._chat_filter))
        add(CommandHandler("mock", self._cmd_mock, filters=self._chat_filter))
        add(CommandHandler("analytics", self._cmd_analytics, filters=self._chat_filter))
        add(CommandHandler("pipeline", self._cmd_pipeline, filters=self._chat_filter))
        add(CommandHandler("finance", self._cmd_finance, filters=self._chat_filter))
        add(CommandHandler("personal", self._cmd_personal, filters=self._chat_filter))
        add(CommandHandler("etsy", self._cmd_etsy, filters=self._chat_filter))
        add(CommandHandler("list", self._cmd_list, filters=self._chat_filter))
        add(CommandHandler("screen", self._cmd_screen, filters=self._chat_filter))

        # Messaggi vocali
        add(MessageHandler(self._chat_filter & filters.VOICE, self._handle_voice))

        # Messaggi testo generici (deve essere l'ultimo handler)
        add(MessageHandler(self._chat_filter & filters.TEXT & ~filters.COMMAND, self._handle_text))

    # ------------------------------------------------------------------
    # Comandi
    # ------------------------------------------------------------------

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/status — stato del sistema."""
        statuses = self.pepe.get_agent_statuses()
        if not statuses:
            await update.message.reply_text("🟢 Sistema attivo. Nessun agente registrato.")
            return

        lines = ["🟢 *Sistema AgentPeXI*\n"]
        status_icons = {"idle": "⚪", "running": "🔵", "error": "🔴"}
        for name, status in statuses.items():
            icon = status_icons.get(status, "❓")
            lines.append(f"{icon} *{name}*: {status}")

        queue_size = self.pepe._queue.qsize()
        lines.append(f"\n📋 Task in coda: {queue_size}")

        domain = self.pepe.get_active_domain()
        domain_icon = "🏪" if domain.name == "etsy_store" else "🧠"
        lines.append(f"\n{domain_icon} *Dominio attivo*: {domain.name}")

        mock_line = "\n🟡 *MOCK MODE ATTIVO*" if self.pepe.mock_mode else ""
        if mock_line:
            lines.append(mock_line)

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/report — chiede report a Pepe."""
        session_id = str(update.effective_chat.id)
        reply = await self.pepe.handle_user_message(
            "Dammi un report sullo stato attuale del sistema e delle attività recenti.",
            source="telegram",
            session_id=session_id,
        )
        await self._reply_chunked(update.message, reply)

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/pause — ferma i worker di Pepe."""
        await self.pepe.stop()
        await update.message.reply_text("⏸️ Worker Pepe fermati.")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/resume — riavvia i worker di Pepe."""
        await self.pepe.start()
        await update.message.reply_text("▶️ Worker Pepe riavviati.")

    async def _cmd_ask(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/ask <domanda> — chiede qualcosa a Pepe."""
        text = " ".join(context.args) if context.args else ""
        if not text:
            await update.message.reply_text("Uso: /ask <la tua domanda>")
            return
        session_id = str(update.effective_chat.id)
        reply = await self.pepe.handle_user_message(text, source="telegram", session_id=session_id)
        await self._reply_chunked(update.message, reply)

    async def _cmd_listings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/listings — lista listing Etsy recenti."""
        cursor = await self.pepe.memory._db.execute(
            "SELECT title, status, sales, revenue FROM etsy_listings ORDER BY created_at DESC LIMIT 10"
        )
        rows = await cursor.fetchall()
        if not rows:
            await update.message.reply_text("Nessun listing trovato.")
            return

        lines = ["📦 *Listing recenti*\n"]
        for r in rows:
            row = dict(r)
            lines.append(
                f"• {row['title'][:40]} — {row['status']} | 🛒 {row['sales']} | €{row['revenue']:.2f}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_retry(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/retry [task_id] — riprova ultimo task fallito o uno specifico."""
        task_id = context.args[0] if context.args else None
        try:
            result = await self.pepe.retry_task(task_id=task_id)
            await update.message.reply_text(
                f"✅ Retry completato: {result.agent_name} → {result.status.value}"
            )
        except ValueError as exc:
            await update.message.reply_text(f"❌ {exc}")
        except RuntimeError as exc:
            await update.message.reply_text(f"⚠️ {exc}")

    async def _cmd_resume_agent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/resume_agent <name> — riattiva agente sospeso."""
        if not context.args:
            await update.message.reply_text("Uso: /resume_agent <nome_agente>")
            return
        name = context.args[0]
        if self.pepe.resume_agent(name):
            await update.message.reply_text(f"✅ Agente *{name}* riattivato.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ Agente *{name}* non trovato o non sospeso.", parse_mode="Markdown")

    async def _cmd_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/new — nuova sessione, azzera conversazione precedente."""
        session_id = str(update.effective_chat.id)
        await self.pepe.memory.clear_session(session_id)
        await update.message.reply_text(
            "✅ Nuova sessione avviata. La conversazione precedente è stata archiviata."
        )

    async def _cmd_analytics(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/analytics — esegue subito il job analytics (senza aspettare le 08:00)."""
        await update.message.reply_text("⏳ Avvio analytics manuale...")
        try:
            from apps.backend.core.models import AgentTask as _AgentTask
            task = _AgentTask(
                agent_name="analytics",
                input_data={},
                source="telegram_manual",
            )
            result = await self.pepe.dispatch_task(task)
            out = result.output_data or {}
            listings_count = len(out.get("listings_analyzed", []))
            await update.message.reply_text(
                f"✅ Analytics completato\n"
                f"Listing analizzati: {listings_count}\n"
                f"Controlla la dashboard per il report completo."
            )
        except Exception as exc:
            logger.error("Analytics manuale fallito: %s", exc)
            await update.message.reply_text(f"❌ Analytics fallito: {exc}")

    async def _cmd_pipeline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/pipeline — avvia manualmente Research → Design → Publisher."""
        if not self.scheduler:
            await update.message.reply_text("❌ Scheduler non disponibile.")
            return
        await update.message.reply_text("⏳ Pipeline avviata — Research in corso...")
        try:
            asyncio.create_task(self.scheduler._run_pipeline())
        except Exception as exc:
            logger.error("Pipeline manuale fallita: %s", exc)
            await update.message.reply_text(f"❌ Pipeline fallita: {exc}")

    async def _cmd_finance(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/finance — avvia manualmente il Finance Agent."""
        if not self.scheduler:
            await update.message.reply_text("❌ Scheduler non disponibile.")
            return
        await update.message.reply_text("⏳ Finance report in avvio...")
        try:
            asyncio.create_task(self.scheduler._run_finance())
        except Exception as exc:
            logger.error("Finance manuale fallito: %s", exc)
            await update.message.reply_text(f"❌ Finance fallito: {exc}")

    async def _cmd_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/list — lista tutti i comandi disponibili."""
        domain = self.pepe.get_active_domain()
        domain_icon = "🧠" if domain.name == "personal" else "🏪"
        lines = [
            f"📋 *Comandi AgentPeXI* ({domain_icon} {domain.name})\n",
            "*— Sistema —*",
            "/status — stato agenti, coda, dominio attivo",
            "/list — questo messaggio",
            "/pause — ferma i worker",
            "/resume — riavvia i worker",
            "/new — nuova sessione (azzera conversazione)",
            "",
            "*— Dominio —*",
            "/personal — passa al dominio Personal (Ollama locale)",
            "/etsy — passa al dominio Etsy store (Claude)",
            "/screen [on|off|status] — gestione Screen Watcher",
            "",
            "*— Etsy —*",
            "/pipeline — avvia manualmente Research → Design → Publisher",
            "/analytics — esegue subito il job analytics",
            "/finance — genera report economico",
            "/listings — lista ultimi 10 listing",
            "/mock [on|off] — attiva/disattiva mock mode",
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

    async def _cmd_screen(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/screen [on|off|status] — gestione Screen Watcher."""
        arg = (context.args[0].lower() if context.args else "status")

        if not self.screen_watcher:
            await update.message.reply_text(
                "❌ *Screen Watcher non disponibile*\n\n"
                "Il servizio non è partito all'avvio del server.\n"
                "Cause probabili: `mss`, `pyobjc` o `Vision` non installati.\n\n"
                "Controlla i log del server per il dettaglio dell'errore.",
                parse_mode="Markdown",
            )
            return

        if arg == "off":
            self.screen_watcher.pause()
            await update.message.reply_text(
                "⏸️ *Screen Watcher in pausa*\n\nNon catturerò più lo schermo.\nUsa /screen on per riprendere.",
                parse_mode="Markdown",
            )

        elif arg == "on":
            self.screen_watcher.resume()
            await update.message.reply_text(
                "▶️ *Screen Watcher attivo*\n\nRiprendo a monitorare lo schermo.",
                parse_mode="Markdown",
            )

        else:  # status (default)
            st = self.screen_watcher.get_status()
            icon = "▶️" if st["active"] else "⏸️"
            stato = "Attivo" if st["active"] else "In pausa"
            last_app = st["last_capture_app"] or "—"
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

    async def _cmd_personal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/personal — passa al dominio Personal (Ollama locale, privacy totale)."""
        self.pepe.set_active_domain(DOMAIN_PERSONAL)
        if self.pepe._ws_broadcast:
            await self.pepe._ws_broadcast({
                "type": "system_status",
                "domain": "personal",
                "message": "Dominio Personal attivato",
            })
        await update.message.reply_text(
            "🧠 *Dominio Personal attivo*\n\n"
            "Sono passato in modalità assistente personale.\n"
            "LLM: Ollama locale (qwen3:4b) — privacy totale, costo zero.\n"
            "Usa /etsy per tornare alla gestione store.",
            parse_mode="Markdown",
        )

    async def _cmd_etsy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/etsy — torna al dominio Etsy store."""
        self.pepe.set_active_domain(DOMAIN_ETSY)
        if self.pepe._ws_broadcast:
            await self.pepe._ws_broadcast({
                "type": "system_status",
                "domain": "etsy_store",
                "message": "Dominio Etsy attivato",
            })
        await update.message.reply_text(
            "🏪 *Dominio Etsy attivo*\n\n"
            "Torno alla gestione dello store.\n"
            "LLM: Claude (Anthropic). Usa /personal per la modalità personale.",
            parse_mode="Markdown",
        )

    async def _cmd_mock(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/mock [on|off] — attiva o disattiva mock mode Etsy."""
        args = context.args or []
        arg = args[0].lower() if args else ""

        if arg == "on":
            self.pepe.set_mock_mode(True)
            if self.pepe._ws_broadcast:
                await self.pepe._ws_broadcast({
                    "type": "system_status",
                    "mock_mode": True,
                    "message": "Mock mode attivato",
                })
            await update.message.reply_text(
                "🟡 *MOCK MODE ATTIVO*\n\n"
                "Etsy API e Replicate sono simulati.\n"
                "I listing vengono salvati nel DB locale.\n"
                "Usa /ask per avviare una pipeline di test.",
                parse_mode="Markdown",
            )

        elif arg == "off":
            self.pepe.set_mock_mode(False)
            if self.pepe._ws_broadcast:
                await self.pepe._ws_broadcast({
                    "type": "system_status",
                    "mock_mode": False,
                    "message": "Mock mode disattivato",
                })
            await update.message.reply_text(
                "✅ *Mock mode disattivato*\n\n"
                "Il sistema tornerà a usare Etsy API reale "
                "non appena i token saranno disponibili.",
                parse_mode="Markdown",
            )

        else:
            status = "🟡 ATTIVO" if self.pepe.mock_mode else "⚫ INATTIVO"
            await update.message.reply_text(
                f"*Mock Mode*: {status}\n\n"
                "Uso: `/mock on` oppure `/mock off`",
                parse_mode="Markdown",
            )

    # ------------------------------------------------------------------
    # Handler messaggi testo
    # ------------------------------------------------------------------

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Messaggio testo → Pepe con session_id da chat_id."""
        text = update.message.text
        session_id = str(update.effective_chat.id)
        reply = await self.pepe.handle_user_message(text, source="telegram", session_id=session_id)
        await self._reply_chunked(update.message, reply)

    # ------------------------------------------------------------------
    # Handler messaggi vocali
    # ------------------------------------------------------------------

    async def _handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Vocale → STT → Pepe → TTS → risposta audio."""
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)

        # Scarica file OGG in temp
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

        try:
            # STT: OGG → testo
            transcription = await self._transcribe(tmp_path)
            if not transcription:
                await update.message.reply_text("🔇 Non ho capito l'audio, riprova.")
                return

            # Invia trascrizione come feedback
            await update.message.reply_text(f"🎤 _{transcription}_", parse_mode="Markdown")

            # Pepe elabora
            session_id = str(update.effective_chat.id)
            reply = await self.pepe.handle_user_message(transcription, source="telegram", session_id=session_id)

            # Risposta testuale
            await update.message.reply_text(reply)

            # TTS: testo → audio
            audio_bytes = await self._synthesize(reply)
            if audio_bytes:
                await update.message.reply_voice(voice=audio_bytes)

        finally:
            # Pulizia file temporaneo
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # STT / TTS helpers (lazy import — moduli voice creati in step 1.8)
    # ------------------------------------------------------------------

    async def _transcribe(self, audio_path: str) -> str:
        """Trascrive audio via faster-whisper (lazy import)."""
        try:
            from apps.backend.voice.stt import transcribe
            return await transcribe(audio_path)
        except ImportError:
            logger.warning("Modulo voice.stt non disponibile — STT disabilitato")
            return ""
        except Exception as exc:
            logger.error("Errore STT: %s", exc)
            return ""

    async def _synthesize(self, text: str) -> bytes | None:
        """Sintetizza audio via ElevenLabs (lazy import)."""
        try:
            from apps.backend.voice.tts import synthesize
            return await synthesize(text)
        except ImportError:
            logger.debug("Modulo voice.tts non disponibile — TTS disabilitato")
            return None
        except Exception as exc:
            logger.error("Errore TTS: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Notifiche (callback registrato in Pepe)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    _TG_LIMIT = 4000  # Telegram max ~4096 — lascia margine

    async def _reply_chunked(self, message: "telegram.Message", text: str) -> None:
        """Invia risposta spezzandola in chunk se supera il limite Telegram."""
        if len(text) <= self._TG_LIMIT:
            await message.reply_text(text)
            return
        # Spezza su newline per non troncare a metà riga
        lines: list[str] = text.splitlines(keepends=True)
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) > self._TG_LIMIT:
                if chunk:
                    await message.reply_text(chunk.rstrip())
                chunk = line
            else:
                chunk += line
        if chunk.strip():
            await message.reply_text(chunk.rstrip())

    async def _send_notification(self, message: str, priority: bool = False) -> None:
        """Invia notifica a Andrea via Telegram, spezzando se necessario."""
        if not self._app or not settings.TELEGRAM_CHAT_ID:
            return
        chat_id = int(settings.TELEGRAM_CHAT_ID)
        text = message  # nessun prefisso emoji — tono consulente
        if len(text) <= self._TG_LIMIT:
            await self._app.bot.send_message(chat_id=chat_id, text=text)
            return
        # Chunk lungo
        lines = text.splitlines(keepends=True)
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) > self._TG_LIMIT:
                if chunk:
                    await self._app.bot.send_message(chat_id=chat_id, text=chunk.rstrip())
                chunk = line
            else:
                chunk += line
        if chunk.strip():
            await self._app.bot.send_message(chat_id=chat_id, text=chunk.rstrip())
