"""Bot Telegram — interfaccia Telegram per AgentPeXI.

Integrazione asincrona con python-telegram-bot v20+.
NON usa run_polling() — si integra nel loop asyncio di FastAPI.

B3/step 3.3: tutti i command handler sono stati estratti in:
  - handlers/autopilot.py  (/run, /stop, callback approve/skip)
  - handlers/system.py     (/status, /report, /pause, /resume, /ask, /new,
                             /mock, /retry, /resume_agent, /personal, /etsy,
                             /screen, /list, /wiki, voice, text)
  - handlers/queue.py      (/listings, /niche, /design, /analytics, /finance,
                             /remind, /reminders, /summarize, /research,
                             /feedback, /urgency)

Questa classe è responsabile solo di: startup/shutdown del bot e notifiche push.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram.ext import Application

from apps.backend.core.config import settings
from apps.backend.telegram.dependencies import BotDependencies
from apps.backend.telegram.formatters import send_chunked
from apps.backend.telegram.middleware import build_chat_filter

if TYPE_CHECKING:
    from apps.backend.core.autopilot_loop import AutopilotLoop
    from apps.backend.core.pepe import Pepe
    from apps.backend.core.scheduler import Scheduler
    from apps.backend.screen.watcher import ScreenWatcher

logger = logging.getLogger("agentpexi.telegram")


class TelegramBot:
    """Bot Telegram per AgentPeXI — startup + notifiche push."""

    def __init__(self, deps: BotDependencies) -> None:
        self.pepe               = deps.pepe
        self.scheduler          = deps.scheduler
        self.screen_watcher     = deps.screen_watcher
        self.autopilot_loop     = deps.autopilot_loop
        self.production_queue   = deps.production_queue
        self.budget_manager     = deps.budget_manager
        self.publication_policy = deps.publication_policy
        self._deps              = deps
        self._app: Application | None = None

        # Fail-closed: lancia RuntimeError se TELEGRAM_CHAT_ID manca.
        self._chat_filter = build_chat_filter(settings.TELEGRAM_CHAT_ID)

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

        # Registra handler via moduli dedicati (B3/step 3.3)
        self._register_handlers()

        # Registra notifier in Pepe
        self.pepe.set_telegram_notifier(self._send_notification)
        self.pepe.set_reminder_notifier(self._send_reminder_notification)

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
        """Delega la registrazione ai moduli handler di B3/step 3.3."""
        assert self._app is not None
        from apps.backend.telegram.handlers import autopilot, queue, system

        autopilot.register(self._app, self._deps, self._chat_filter)
        system.register(self._app, self._deps, self._chat_filter)
        queue.register(self._app, self._deps, self._chat_filter)

    # ------------------------------------------------------------------
    # Notifiche (callback registrato in Pepe)
    # ------------------------------------------------------------------

    async def _send_reminder_notification(self, message: str) -> int:
        """Invia reminder e restituisce il telegram message_id (per ACK via reply)."""
        if not self._app or not settings.TELEGRAM_CHAT_ID:
            return 0
        try:
            sent = await self._app.bot.send_message(
                chat_id=int(settings.TELEGRAM_CHAT_ID), text=message
            )
            return sent.message_id
        except Exception as exc:
            logger.error("_send_reminder_notification fallito: %s", exc)
            return 0

    async def _send_notification(self, message: str, priority: bool = False) -> None:
        """Invia notifica a Andrea via Telegram, spezzando se necessario."""
        if not self._app or not settings.TELEGRAM_CHAT_ID:
            return
        await send_chunked(self._app.bot, int(settings.TELEGRAM_CHAT_ID), message)
