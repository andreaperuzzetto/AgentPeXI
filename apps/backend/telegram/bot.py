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

if TYPE_CHECKING:
    from apps.backend.core.pepe import Pepe

logger = logging.getLogger("agentpexi.telegram")


class TelegramBot:
    """Bot Telegram per AgentPeXI — comandi + chat + vocale."""

    def __init__(self, pepe: Pepe) -> None:
        self.pepe = pepe
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

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/report — chiede report a Pepe."""
        session_id = str(update.effective_chat.id)
        reply = await self.pepe.handle_user_message(
            "Dammi un report sullo stato attuale del sistema e delle attività recenti.",
            source="telegram",
            session_id=session_id,
        )
        await update.message.reply_text(reply)

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
        await update.message.reply_text(reply)

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

    # ------------------------------------------------------------------
    # Handler messaggi testo
    # ------------------------------------------------------------------

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Messaggio testo → Pepe con session_id da chat_id."""
        text = update.message.text
        session_id = str(update.effective_chat.id)
        reply = await self.pepe.handle_user_message(text, source="telegram", session_id=session_id)
        await update.message.reply_text(reply)

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

    async def _send_notification(self, message: str, priority: bool = False) -> None:
        """Invia notifica a Andrea via Telegram."""
        if not self._app or not settings.TELEGRAM_CHAT_ID:
            return
        chat_id = int(settings.TELEGRAM_CHAT_ID)
        prefix = "🚨 " if priority else "ℹ️ "
        await self._app.bot.send_message(chat_id=chat_id, text=prefix + message)
