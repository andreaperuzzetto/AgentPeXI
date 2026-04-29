"""Handler Telegram — Shop Setup (Blocco 5 / step 5.1).

Comandi:
  /shop                      — preview stato corrente dello shop Etsy
  /shopsetup                 — genera preview titolo + about (NON applica)
  /shopsetup confirm         — applica titolo + about via Etsy API
  /shopsetup niche <nome>    — preview con focus su una niche specifica
  /shopsetup force           — forza aggiornamento anche se niches invariate

Implementazione completa ShopProfileOptimizer in step 5.1:
  - Legge top niches da niche_intelligence (LearningLoop)
  - Genera titolo shop SEO-ottimizzato (max 55 char)
  - Genera About/Announcement via Haiku
  - Confronta con ultima applicazione (config DB) — skip se invariato
  - Applica via Etsy API (title + announcement)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from apps.backend.telegram.formatters import md_escape, reply_chunked

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.shop_setup")


# ---------------------------------------------------------------------------
# /shop — preview shop corrente
# ---------------------------------------------------------------------------

async def cmd_shop(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/shop — mostra lo stato corrente dello shop Etsy."""
    etsy = deps.etsy_api
    if etsy is None:
        await update.message.reply_text("⚠️ EtsyAPI non disponibile.")
        return

    await update.message.reply_text("🔍 Recupero info shop…")

    try:
        shop = await etsy.get_shop()
    except Exception as exc:
        logger.error("get_shop fallito: %s", exc)
        await update.message.reply_text(f"⚠️ Errore API Etsy: {exc}")
        return

    try:
        listings     = await deps.pepe.memory.get_etsy_listings(limit=200)
        listing_count = len(listings)
        active_count  = sum(1 for li in listings if li.get("state") == "active")
    except Exception:
        listing_count = active_count = 0

    mock_badge   = "  _(mock mode)_" if getattr(etsy, "mock_mode", False) else ""
    shop_name    = shop.get("shop_name") or shop.get("name") or "—"
    title        = shop.get("title") or "—"
    announcement = shop.get("announcement") or "—"
    currency     = shop.get("currency_code") or "EUR"
    vacation     = shop.get("is_vacation", False)
    url          = shop.get("url") or f"https://www.etsy.com/shop/{shop_name}"

    vacation_line = "\n🏖 *Vacation mode ATTIVO*" if vacation else ""

    # Mostra l'ultimo titolo applicato da ShopOptimizer (se disponibile)
    last_optimizer_title = ""
    if deps.shop_optimizer is not None:
        cached = await deps.shop_optimizer._get_config("shop_optimizer.last_applied_title")
        if cached:
            last_optimizer_title = f"\n🤖 _Ultimo titolo ottimizzato:_ `{md_escape(cached)}`"

    lines = [
        f"🏪 *Shop Etsy*{mock_badge}",
        "",
        f"📛 Nome: `{md_escape(shop_name)}`",
        f"📝 Titolo: {md_escape(title)}",
        f"💬 Announcement: {md_escape(announcement[:80])}{'…' if len(announcement) > 80 else ''}",
        f"💶 Valuta: {currency}",
        f"🔗 URL: {url}",
        "",
        f"📦 Listing nel DB locale: {listing_count}  (attivi: {active_count})",
        last_optimizer_title,
        vacation_line,
        "",
        "Per ottimizzare il profilo shop: `/shopsetup`",
    ]
    await update.message.reply_text(
        "\n".join(l for l in lines if l is not None),
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# /shopsetup — ottimizzazione profilo shop (B5/5.1)
# ---------------------------------------------------------------------------

async def cmd_shopsetup(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/shopsetup [confirm | force | niche <nome>] — ottimizza profilo shop."""
    optimizer = deps.shop_optimizer

    if optimizer is None:
        # Fallback al messaggio stub se ShopOptimizer non è disponibile
        await update.message.reply_text(
            "⚠️ ShopProfileOptimizer non disponibile.\n"
            "Assicurati che il sistema sia avviato correttamente.",
        )
        return

    args = context.args or []

    # ---- Parsing argomenti ----
    confirm     = False
    force       = False
    focus_niche = None

    if args:
        cmd = args[0].lower()
        if cmd == "confirm":
            confirm = True
        elif cmd == "force":
            force   = True
            confirm = True
        elif cmd == "niche" and len(args) >= 2:
            focus_niche = " ".join(args[1:])
        else:
            # Argomento non riconosciuto — trattalo come nome niche diretto
            focus_niche = " ".join(args)

    # ---- Modalità PREVIEW ----
    if not confirm:
        await update.message.reply_text("🔍 Genero preview profilo shop…")
        try:
            data = await optimizer.preview(focus_niche=focus_niche)
        except Exception as exc:
            logger.error("shopsetup preview fallita: %s", exc)
            await update.message.reply_text(f"⚠️ Errore durante la generazione: {exc}")
            return

        changed_note = (
            "✅ Niches cambiate rispetto all'ultima applicazione — pronto per aggiornare."
            if data["changed"]
            else "ℹ️ Niches invariate — `/shopsetup force` per aggiornare comunque."
        )

        niches_str = ", ".join(data["niches"]) or "—"
        last_title = data.get("last_applied_title", "—")

        msg = (
            f"🏪 *Shop Profile Preview*\n\n"
            f"📊 Niches fonte: `{md_escape(niches_str)}`\n\n"
            f"📝 *Titolo proposto* (Etsy max 55 char):\n"
            f"`{md_escape(data['title'])}`\n\n"
            f"💬 *Announcement / About*:\n"
            f"{md_escape(data['about'])}\n\n"
            f"─────────────────────────\n"
            f"_Ultimo titolo applicato:_ `{md_escape(last_title)}`\n"
            f"{changed_note}\n\n"
            f"Per applicare: `/shopsetup confirm`"
        )
        await reply_chunked(update.message, msg)
        return

    # ---- Modalità CONFIRM / FORCE ----
    await update.message.reply_text(
        "🚀 Applico profilo shop ottimizzato…"
        + (" _(force mode)_" if force else "")
    )

    try:
        result = await optimizer.apply_shop_profile(
            focus_niche=focus_niche,
            force=force,
        )
    except Exception as exc:
        logger.error("shopsetup apply fallito: %s", exc)
        await update.message.reply_text(f"⚠️ Errore durante l'applicazione: {exc}")
        return

    status = result.get("status", "unknown")

    if status == "skipped":
        await update.message.reply_text(
            "ℹ️ Profilo non aggiornato — le niches non sono cambiate.\n"
            "Usa `/shopsetup force` per aggiornare comunque."
        )
        return

    if status == "no_api":
        await update.message.reply_text(
            "⚠️ EtsyAPI non disponibile — profilo generato ma non applicato.\n\n"
            f"*Titolo:* `{md_escape(result['title'])}`\n\n"
            f"*About:*\n{md_escape(result['about'])}",
            parse_mode="Markdown",
        )
        return

    if status == "error":
        err = result.get("error", "errore sconosciuto")
        await update.message.reply_text(
            f"❌ Errore API Etsy: {md_escape(err)}\n\n"
            f"*Titolo generato:* `{md_escape(result['title'])}`",
            parse_mode="Markdown",
        )
        return

    mock_badge = " _(mock mode)_" if status == "mock" else ""
    niches_str = ", ".join(result.get("niches", [])) or "—"

    msg = (
        f"✅ *Profilo shop aggiornato*{mock_badge}\n\n"
        f"📝 *Titolo applicato:*\n`{md_escape(result['title'])}`\n\n"
        f"💬 *Announcement applicato:*\n{md_escape(result['about'])}\n\n"
        f"📊 Niches usate: `{md_escape(niches_str)}`"
    )
    await reply_chunked(update.message, msg)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(
    app: Application,
    deps: "BotDependencies",
    chat_filter,
) -> None:
    """Registra gli handler shop_setup nell'Application."""
    from functools import partial

    add = app.add_handler
    add(CommandHandler("shop",      partial(cmd_shop,      deps), filters=chat_filter))
    add(CommandHandler("shopsetup", partial(cmd_shopsetup, deps), filters=chat_filter))
