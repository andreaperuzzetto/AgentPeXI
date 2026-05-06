"""Telegram command handlers — queue/operations (thin dispatcher)."""
from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from telegram.ext import Application, CommandHandler

from apps.backend.telegram.handlers._queue import (
    cmd_analytics,
    cmd_bundle,
    cmd_design_etsy,
    cmd_feedback,
    cmd_finance,
    cmd_ladder,
    cmd_listings,
    cmd_niche,
    cmd_remind,
    cmd_remind_list,
    cmd_research,
    cmd_sections,
    cmd_summarize,
    cmd_urgency,
)

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies


def register(
    app: Application,
    deps: "BotDependencies",
    chat_filter,
) -> None:
    """Registra tutti gli handler Etsy + Personal nell'Application."""
    add = app.add_handler

    # Etsy / pipeline
    add(CommandHandler("listings",  partial(cmd_listings,    deps), filters=chat_filter))
    add(CommandHandler("niche",     partial(cmd_niche,       deps), filters=chat_filter))
    add(CommandHandler("design",    partial(cmd_design_etsy, deps), filters=chat_filter))
    add(CommandHandler("analytics", partial(cmd_analytics,   deps), filters=chat_filter))
    add(CommandHandler("ladder",    partial(cmd_ladder,      deps), filters=chat_filter))
    add(CommandHandler("bundle",    partial(cmd_bundle,      deps), filters=chat_filter))
    add(CommandHandler("finance",   partial(cmd_finance,     deps), filters=chat_filter))
    add(CommandHandler("sections",  partial(cmd_sections,    deps), filters=chat_filter))

    # Personal
    add(CommandHandler("remind",    partial(cmd_remind,      deps), filters=chat_filter))
    add(CommandHandler("reminders", partial(cmd_remind_list, deps), filters=chat_filter))
    add(CommandHandler("summarize", partial(cmd_summarize,   deps), filters=chat_filter))
    add(CommandHandler("research",  partial(cmd_research,    deps), filters=chat_filter))
    add(CommandHandler("feedback",  partial(cmd_feedback,    deps), filters=chat_filter))
    add(CommandHandler("urgency",   partial(cmd_urgency,     deps), filters=chat_filter))
