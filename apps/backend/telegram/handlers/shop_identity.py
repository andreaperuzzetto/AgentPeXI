# apps/backend/telegram/handlers/shop_identity.py
"""Handler Telegram — Shop Visual Identity (A.2).

Comandi:
  /style_guide   — genera 3 opzioni style guide (o ri-mostra quelle esistenti)
                   → invia messaggio con InlineKeyboard [Approva 1] [Approva 2] [Approva 3]

Callbacks:
  approve_identity:<id>   — approva l'opzione con quell'ID
"""
from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from apps.backend.telegram.formatters import md_escape
from apps.backend.core.shop_identity_service import ShopIdentityService
from apps.backend.agents.design import DesignAgent

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.shop_identity")

# ──────────────────────────────────────────────────────────────────────────────
# /style_guide
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_style_guide(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/style_guide — genera 3 opzioni brand identity o mostra quelle esistenti."""
    db = await deps.pepe.memory.get_db()
    svc = ShopIdentityService(db)
    options = await svc.list_options()

    if not options:
        # Generate new options
        await update.message.reply_text("🎨 Genero 3 opzioni brand identity via AI…")
        try:
            from apps.backend.agents.market_data import MarketDataAgent
            agent = MarketDataAgent(memory=deps.pepe.memory, mock_mode=False)
            ids = await agent.generate_style_options(db=db)
            options = await svc.list_options()
            logger.info("style-guide: generated %d options (ids=%s)", len(ids), ids)
        except Exception as exc:
            logger.exception("generate_style_options failed: %s", exc)
            await update.message.reply_text(f"⚠️ Errore generazione opzioni: {exc}")
            return

    # Format and send with inline keyboard
    lines = ["*🎨 Opzioni Brand Identity — scegli quella da approvare:*\n"]
    buttons = []
    for i, opt in enumerate(options, 1):
        status = "✅ ATTIVA" if opt.is_active else f"Opzione {i}"
        tone_text = opt.tone[:77] + "..." if len(opt.tone) > 80 else opt.tone
        lines.append(
            f"*{status}: {md_escape(opt.aesthetic_name)}*\n"
            f"  Palette: `{opt.palette_primary}` · `{opt.palette_secondary}` · `{opt.palette_accent}`\n"
            f"  Mockup: {md_escape(opt.mockup_style)} \\| Tone: {md_escape(tone_text)}"
        )
        if not opt.is_active:
            buttons.append(
                InlineKeyboardButton(
                    f"Approva {i}: {opt.aesthetic_name[:20]}",
                    callback_data=f"approve_identity:{opt.id}",
                )
            )

    keyboard = InlineKeyboardMarkup([[btn] for btn in buttons]) if buttons else None
    text = "\n\n".join(lines)

    await update.message.reply_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=keyboard,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Callback: approve_identity:<id>
# ──────────────────────────────────────────────────────────────────────────────

async def cb_approve_identity(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Callback InlineKeyboard — approva l'opzione identity selezionata."""
    query = update.callback_query
    await query.answer()

    try:
        identity_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("⚠️ Callback non valido.")
        return

    db = await deps.pepe.memory.get_db()
    svc = ShopIdentityService(db)

    try:
        await svc.set_active(identity_id)
        record = await svc.get_active()
        if record is None:
            await query.edit_message_text("⚠️ Identità non trovata dopo l'attivazione.")
            return

        await query.edit_message_text(
            f"✅ *Brand Identity attivata\\!*\n\n"
            f"*{md_escape(record.aesthetic_name)}*\n"
            f"Palette: `{record.palette_primary}` · `{record.palette_secondary}` · `{record.palette_accent}`\n"
            f"Mockup: {md_escape(record.mockup_style)}\n"
            f"Tone: {md_escape(record.tone[:97] + '...' if len(record.tone) > 100 else record.tone)}\n\n"
            f"Ora puoi usare `/shop\\-description` per generare la descrizione dello shop\n"
            f"o `/generate\\_assets` per logo e banner\\.",
            parse_mode="MarkdownV2",
        )
        logger.info("shop_identity: activated id=%d name=%s", record.id, record.aesthetic_name)
    except ValueError as exc:
        await query.edit_message_text(f"⚠️ Errore: {exc}")
    except Exception as exc:
        logger.exception("cb_approve_identity failed: %s", exc)
        await query.edit_message_text(f"⚠️ Errore interno: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# /generate_assets
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_generate_assets(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/generate_assets — genera logo e banner per la shop identity attiva."""
    db = await deps.pepe.memory.get_db()
    svc = ShopIdentityService(db)
    identity = await svc.get_active()
    if identity is None:
        await update.message.reply_text(
            "⚠️ Nessuna brand identity attiva\\. Usa /style\\_guide per approvarne una prima\\.",
            parse_mode="MarkdownV2",
        )
        return

    await update.message.reply_text(
        f"🖼 Genero logo e banner per *{md_escape(identity.aesthetic_name)}*…",
        parse_mode="MarkdownV2",
    )
    try:
        design = DesignAgent(
            anthropic_client=deps.pepe.anthropic_client,
            memory=deps.pepe.memory,
            storage=deps.pepe.storage,
        )
        result = await design.generate_shop_assets(identity_id=str(identity.id), db=db)
        await update.message.reply_text(
            f"✅ Assets generati\\!\n"
            f"Logo: `{md_escape(result['logo_path'])}`\n"
            f"Banner: `{md_escape(result['banner_path'])}`",
            parse_mode="MarkdownV2",
        )
    except Exception as exc:
        logger.exception("generate_shop_assets failed: %s", exc)
        await update.message.reply_text(f"⚠️ Errore: {exc}")


async def cmd_shop_description(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/shop_description — genera la descrizione shop per l'identity attiva."""
    db = await deps.pepe.memory.get_db()
    svc = ShopIdentityService(db)
    identity = await svc.get_active()
    if identity is None:
        await update.message.reply_text(
            "⚠️ Nessuna brand identity attiva. Usa /style_guide prima."
        )
        return
    await update.message.reply_text("✍️ Genero descrizione shop…")
    try:
        design = DesignAgent(memory=deps.pepe.memory)
        description = await design.generate_shop_description(identity)
        await update.message.reply_text(
            f"📝 *Descrizione Shop:*\n\n{md_escape(description)}",
            parse_mode="MarkdownV2"
        )
    except Exception as exc:
        logger.exception("generate_shop_description failed: %s", exc)
        await update.message.reply_text(f"⚠️ Errore: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# Registration
# ──────────────────────────────────────────────────────────────────────────────

def register(
    app: Application,
    deps: "BotDependencies",
    chat_filter,  # telegram.ext.filters.BaseFilter
) -> None:
    """Registra handler shop_identity nell'Application Telegram."""
    add = app.add_handler

    add(CommandHandler(
        "style_guide",
        partial(cmd_style_guide, deps),
        filters=chat_filter,
    ))
    add(CommandHandler(
        "generate_assets",
        partial(cmd_generate_assets, deps),
        filters=chat_filter,
    ))
    add(CommandHandler(
        "shop_description",
        partial(cmd_shop_description, deps),
        filters=chat_filter,
    ))
    add(CallbackQueryHandler(
        partial(cb_approve_identity, deps),
        pattern=r"^approve_identity:\d+$",
    ))
