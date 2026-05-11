"""Handler Telegram — AutopilotLoop (Blocco 2) + Bundle approval (C.1).

Comandi: /run, /stop
Callback: approve:{id} / skip:{id} dalla inline keyboard di approvazione.
Callback: bundle_approve:{cluster_id} / bundle_decline:{cluster_id} dal bundle blueprint.

Il keyboard builder vive in telegram/callbacks.py (B3/step 3.5).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from apps.backend.telegram.callbacks import build_approval_keyboard  # noqa: F401 (re-export)
from apps.backend.telegram.callbacks import _parse_bundle_callback
from apps.backend.telegram.middleware import is_authorized

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.autopilot")


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


async def cmd_approve(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/approve <item_id> — approva un listing dalla production queue."""
    loop = deps.autopilot_loop
    if loop is None:
        await update.message.reply_text("⚠️ AutopilotLoop non disponibile.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Uso: /approve <item_id>")
        return
    try:
        item_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ item_id deve essere un numero intero.")
        return
    await loop.register_approval(item_id, "approved")
    await update.message.reply_text(f"✅ Approvazione registrata per item {item_id}.")
async def cmd_queue(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/queue [clear] — mostra o svuota la coda production."""
    loop = deps.autopilot_loop
    if loop is None:
        await update.message.reply_text("⚠️ AutopilotLoop non disponibile.")
        return
    action = (context.args[0] if context.args else "")
    msg = await loop.cmd_queue(action)
    await update.message.reply_text(msg)


async def cmd_skip(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/skip <item_id> — salta un listing dalla production queue."""
    loop = deps.autopilot_loop
    if loop is None:
        await update.message.reply_text("⚠️ AutopilotLoop non disponibile.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Uso: /skip <item_id>")
        return
    try:
        item_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ item_id deve essere un numero intero.")
        return
    await loop.register_approval(item_id, "skipped_user")
    await update.message.reply_text(f"⏭ Skip registrato per item {item_id}.")


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
        await loop.register_approval(item_id, "approved")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(f"✅ Approvazione registrata per item {item_id}.")
        except Exception:
            logger.exception("Unexpected error")
    elif action == "skip":
        await loop.register_approval(item_id, "skipped_user")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(f"⏭ Skip registrato per item {item_id}.")
        except Exception:
            logger.exception("Unexpected error")


# ---------------------------------------------------------------------------
# Bundle callback handler (C.1)
# ---------------------------------------------------------------------------

async def handle_bundle_callback(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """CallbackQueryHandler — gestisce bundle_approve/bundle_decline dal blueprint."""
    query = update.callback_query
    if query is None:
        return

    if not is_authorized(query.from_user.id):
        await query.answer("Non autorizzato.")
        return

    await query.answer()

    parsed = _parse_bundle_callback(query.data or "")
    if parsed is None:
        return

    action, cluster_id = parsed
    status = "approved" if action == "approve" else "declined"
    emoji = "✅" if action == "approve" else "❌"

    # Store decision in ChromaDB for downstream retrieval (C.3)
    memory = getattr(deps, "memory", None)
    if memory is not None:
        try:
            await memory.store_insight(
                text=f"Bundle {status}: cluster_id={cluster_id}",
                metadata={
                    "type": "bundle_approval",
                    "cluster_id": cluster_id,
                    "status": status,
                },
            )
        except Exception:
            logger.exception("Errore store_insight bundle_approval cluster=%s", cluster_id)

    try:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"{emoji} Bundle {status}: cluster {cluster_id}.")
    except Exception:
        logger.exception("Unexpected error bundle callback")



def register(
    app: Application,
    deps: "BotDependencies",
    chat_filter,
) -> None:
    """Registra tutti gli handler autopilot nell'Application."""
    from functools import partial

    add = app.add_handler
    add(CommandHandler("run",     partial(cmd_run,     deps), filters=chat_filter))
    add(CommandHandler("stop",    partial(cmd_stop,    deps), filters=chat_filter))
    add(CommandHandler("approve", partial(cmd_approve, deps), filters=chat_filter))
    add(CommandHandler("skip",    partial(cmd_skip,    deps), filters=chat_filter))
    add(CommandHandler("queue",   partial(cmd_queue,   deps), filters=chat_filter))
    # CallbackQueryHandler non usa chat_filter — auth via is_authorized nel handler
    add(CallbackQueryHandler(partial(handle_approval_callback, deps)))
    add(CallbackQueryHandler(
        partial(handle_bundle_callback, deps),
        pattern=r"^bundle_(approve|decline):[a-f0-9]{12}$",
    ))
