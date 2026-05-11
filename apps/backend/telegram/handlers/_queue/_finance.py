"""Finance handler."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import apps.backend.api.state as app_state
from telegram import Update
from telegram.ext import ContextTypes

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.queue")


async def _run_and_notify(coro, chat_id: int, bot) -> None:
    """Run *coro*, then send a Telegram notification regardless of outcome (CNC-031)."""
    try:
        await coro
        await bot.send_message(chat_id, "✅ Finance report completato")
    except Exception as exc:
        await bot.send_message(chat_id, f"❌ Errore finance: {exc}")
        logger.exception("Background finance task failed")


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
    chat_id = update.effective_chat.id
    if app_state.task_registry is not None:
        app_state.task_registry.create_task(
            _run_and_notify(deps.scheduler._run_finance(), chat_id, context.bot),
            name="finance_manual",
        )
    else:
        task = asyncio.create_task(
            _run_and_notify(deps.scheduler._run_finance(), chat_id, context.bot),
            name="finance_manual",
        )
        task.add_done_callback(
            lambda t: logger.error("Finance manuale fallito: %s", t.exception())
            if not t.cancelled() and t.exception() else None
        )
