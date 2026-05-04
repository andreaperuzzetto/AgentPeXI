"""Notifications mixin for Pepe."""
from __future__ import annotations

import logging
from typing import Callable, Coroutine

logger = logging.getLogger("agentpexi.pepe")


class NotificationsMixin:

    async def notify_telegram(self, message: str, priority: bool = False) -> None:
        """Invia notifica via Telegram se il notifier è configurato."""
        if self._telegram_notifier:
            try:
                await self._telegram_notifier(message, priority)
            except Exception as exc:
                logger.error("Errore notifica Telegram: %s", exc)

    def set_telegram_notifier(self, fn: Callable[[str, bool], Coroutine]) -> None:
        """Registra il callback per notifiche Telegram (chiamato dal bot module)."""
        self._telegram_notifier = fn

    def set_reminder_notifier(self, fn: Callable[[str], Coroutine]) -> None:
        """Registra il callback per reminder — ritorna il message_id Telegram."""
        self._reminder_notifier = fn

    async def send_reminder_notification(self, message: str) -> int:
        """Invia reminder via Telegram e restituisce message_id (per ACK via reply).
        Ritorna 0 se il notifier non è configurato o fallisce."""
        if self._reminder_notifier:
            try:
                return await self._reminder_notifier(message)
            except Exception as exc:
                logger.error("send_reminder_notification fallito: %s", exc)
        return 0
