"""Handler Telegram — AutopilotLoop (Blocco 2).

Comandi: /run, /stop
Callback: approve:{id} / skip:{id} dalla inline keyboard di approvazione.

Il keyboard builder (build_approval_keyboard) verrà spostato in
callbacks.py al passo 3.5; per ora rimane qui accanto agli handler
che lo usano.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from apps.backend.telegram.middleware import is_authorized

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.autopilot")


# ---------------------------------------------------------------------------
# Keyboard builder
# ---------------------------------------------------------------------------

def build_approval_keyboard(item_id: int) -> InlineKeyboardMarkup:
    """Restituisce la inline keyboard [✅ Approva] [⏭ Salta] per un item."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approva", callback_data=f"approve:{item_id}"),
        InlineKeyboardButton("⏭ Salta",   callback_data=f"skip:{item_id}"),
    ]])


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_run(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/run — avvia o riprende l'AutopilotLoop."""
    loop = deps.autopilot_loop
    if loop is None:
        await update.message.reply_text("⚠️ AutopilotLoop non disponibile.")
        return
    msg = await loop.cmd_run()
    await update.message.reply_text(msg)


async def cmd_stop(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/stop — mette l'AutopilotLoop in paused_manual."""
    loop = deps.autopilot_loop
    if loop is None:
        await update.message.reply_text("⚠️ AutopilotLoop non disponibile.")
        return
    msg = await loop.cmd_stop()
    await update.message.reply_text(msg)


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

async def handle_approval_callback(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """CallbackQueryHandler — gestisce Approva/Salta dalla inline keyboard."""
    query = update.callback_query
    if query is None:
        return

    if not is_authorized(query.from_user.id):
        await query.answer("Non autorizzato.")
        return

    await query.answer()

    data = query.data or ""
    if ":" not in data:
        return

    action, _, raw_id = data.partition(":")
    try:
        item_id = int(raw_id)
    except ValueError:
        return

    loop = deps.autopilot_loop
    if loop is None:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if action == "approve":
        loop.register_approval(item_id, "approved")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(f"✅ Approvazione registrata per item {item_id}.")
        except Exception:
            pass

    elif action == "skip":
        loop.register_approval(item_id, "skipped_user")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(f"⏭ Skip registrato per item {item_id}.")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(
    app: Application,
    deps: "BotDependencies",
    chat_filter,
) -> None:
    """Registra tutti gli handler autopilot nell'Application."""
    from functools import partial

    add = app.add_handler
    add(CommandHandler("run",  partial(cmd_run,  deps), filters=chat_filter))
    add(CommandHandler("stop", partial(cmd_stop, deps), filters=chat_filter))
    # CallbackQueryHandler non usa chat_filter — auth via is_authorized nel handler
    add(CallbackQueryHandler(partial(handle_approval_callback, deps)))
