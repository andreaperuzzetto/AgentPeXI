"""Finance handler."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.queue")


async def cmd_finance(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/finance — avvia manualmente il Finance Agent."""
    if not deps.scheduler:
        await update.message.reply_text("❌ Scheduler non disponibile.")
        return
    await update.message.reply_text("⏳ Finance report in avvio...")
    task = asyncio.create_task(deps.scheduler._run_finance(), name="finance_manual")
    task.add_done_callback(
        lambda t: logger.error("Finance manuale fallito: %s", t.exception())
        if not t.cancelled() and t.exception() else None
    )
