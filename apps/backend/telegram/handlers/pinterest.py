"""Telegram handlers: /pinterest_status, /pinterest_auth, /pinterest_queue."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import partial
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies


async def cmd_pinterest_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    deps: "BotDependencies",
) -> None:
    """/pinterest_status — mostra lo stato di Pinterest Machine."""
    delivery_method = os.getenv("PINTEREST_DELIVERY_METHOD", "tailwind")

    db = await deps.pepe.memory.get_db()

    # OAuth
    tokens = await deps.pepe.memory.get_oauth_tokens("pinterest")
    connected = tokens is not None and bool(tokens.get("access_token"))
    connected_icon = "🟢" if connected else "🔴"

    # Queue counts
    rows = await db.execute_fetchall(
        """
        SELECT
            SUM(CASE WHEN status='pending'   THEN 1 ELSE 0 END) AS pins_queued,
            SUM(CASE WHEN status='failed'    THEN 1 ELSE 0 END) AS pins_failed,
            SUM(CASE WHEN status='published'
                      AND DATE(published_at)=DATE('now') THEN 1 ELSE 0 END) AS pins_today,
            SUM(CASE WHEN DATE(created_at)=DATE('now')
                      THEN cost_image_gen+cost_llm ELSE 0 END)  AS cost_today
        FROM pinterest_queue
        """
    )
    row = dict(rows[0]) if rows else {}
    pins_queued = row.get("pins_queued") or 0
    pins_failed = row.get("pins_failed") or 0
    pins_today  = row.get("pins_today")  or 0
    cost_today  = row.get("cost_today")  or 0.0

    # Next scheduled pin
    next_rows = await db.execute_fetchall(
        "SELECT MIN(scheduled_at) AS next_pin_at FROM pinterest_queue WHERE status='pending'"
    )
    next_pin_at = dict(next_rows[0]).get("next_pin_at") if next_rows else None

    # Active boards
    board_rows = await db.execute_fetchall(
        "SELECT board_name, pin_count FROM pinterest_boards WHERE is_active=1 ORDER BY pin_count DESC"
    )

    lines = [
        "📌 *Pinterest Machine*",
        "",
        f"{connected_icon} Connessione: {'connesso' if connected else 'non connesso'}",
        f"📦 Metodo consegna: `{delivery_method}`",
        "",
        "*Oggi:*",
        f"  • Pin pubblicati: {pins_today}",
        f"  • Pin in coda: {pins_queued}",
        f"  • Pin falliti: {pins_failed}",
        f"  • Costo oggi: ${cost_today:.4f}",
    ]

    if next_pin_at:
        lines += ["", f"⏰ Prossimo pin: `{next_pin_at}`"]

    if board_rows:
        lines += ["", "*Board attivi:*"]
        for br in board_rows:
            r = dict(br)
            lines.append(f"  • {r['board_name']} ({r['pin_count']} pin)")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_pinterest_auth(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    deps: "BotDependencies",
) -> None:
    """/pinterest_auth — stato autenticazione OAuth Pinterest."""
    tokens = await deps.pepe.memory.get_oauth_tokens("pinterest")
    connected = tokens is not None and bool(tokens.get("access_token"))
    connected_icon = "🟢" if connected else "🔴"
    connected_label = "Connesso" if connected else "Non connesso"

    expires_line = ""
    if tokens and tokens.get("expires_at"):
        expires_line = f"\n  • Scadenza: `{tokens['expires_at']}`"

    lines = [
        "🔐 *Pinterest OAuth*",
        "",
        f"{connected_icon} Stato: {connected_label}{expires_line}",
        "",
        "*Per autenticarsi:*",
        "1. Assicurarsi che `PINTEREST_CLIENT_ID` e `PINTEREST_CLIENT_SECRET` siano nel `.env`",
        "2. Eseguire:",
        "```",
        "python apps/backend/tools/pinterest_auth_setup.py",
        "```",
        "3. Aprire il browser all'URL indicato e completare il flusso OAuth",
        "4. Il token verrà salvato automaticamente nel database",
    ]

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_pinterest_queue(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    deps: "BotDependencies",
) -> None:
    """/pinterest_queue — mostra i prossimi 5 pin in coda."""
    db = await deps.pepe.memory.get_db()

    rows = await db.execute_fetchall(
        """
        SELECT id, title, scheduled_at, pin_variant, board_id
        FROM pinterest_queue
        WHERE status='pending'
        ORDER BY scheduled_at ASC
        LIMIT 5
        """
    )

    if not rows:
        await update.message.reply_text(
            "📭 Coda vuota — nessun pin pending in programma.",
            parse_mode="Markdown",
        )
        return

    lines = ["📋 *Prossimi pin in coda:*", ""]
    for r in rows:
        row = dict(r)
        variant = row.get("pin_variant", "?")
        title   = row.get("title", "(senza titolo)")[:60]
        sched   = row.get("scheduled_at", "?")
        lines.append(f"• *[V{variant}]* {title}")
        lines.append(f"  ⏰ `{sched}`")
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(app: Application, deps: "BotDependencies", chat_filter) -> None:
    """Registra i comandi Pinterest nel bot."""
    add = app.add_handler

    add(CommandHandler(
        "pinterest_status",
        partial(cmd_pinterest_status, deps=deps),
        filters=chat_filter,
    ))
    add(CommandHandler(
        "pinterest_auth",
        partial(cmd_pinterest_auth, deps=deps),
        filters=chat_filter,
    ))
    add(CommandHandler(
        "pinterest_queue",
        partial(cmd_pinterest_queue, deps=deps),
        filters=chat_filter,
    ))
