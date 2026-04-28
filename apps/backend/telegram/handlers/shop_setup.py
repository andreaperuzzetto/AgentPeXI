"""Handler Telegram — Shop Setup (Blocco 3 / step 3.6).

Comandi:
  /shop          — preview stato corrente dello shop Etsy (API reale o mock)
  /shopsetup     — wizard guidato per configurare lo shop da zero (stub → B5)

Implementazione completa del wizard in Blocco 5:
  - scelta nome shop, titolo, about, banner
  - prime niche + tipo prodotto
  - prima pubblicazione guidata
  - configurazione ads
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

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

    # Dati listing dalla memory (source of truth locale)
    try:
        listings = await deps.pepe.memory.get_etsy_listings(limit=200)
        listing_count = len(listings)
        active_count  = sum(1 for l in listings if l.get("state") == "active")
    except Exception:
        listing_count = active_count = 0

    mock_badge = "  _(mock mode)_" if etsy.mock_mode else ""
    shop_name  = shop.get("shop_name") or shop.get("name") or "—"
    title      = shop.get("title") or "—"
    currency   = shop.get("currency_code") or "EUR"
    vacation   = shop.get("is_vacation", False)
    url        = shop.get("url") or f"https://www.etsy.com/shop/{shop_name}"

    vacation_line = "\n🏖 *Vacation mode ATTIVO*" if vacation else ""

    lines = [
        f"🏪 *Shop Etsy*{mock_badge}",
        "",
        f"📛 Nome: `{shop_name}`",
        f"📝 Titolo: {title}",
        f"💶 Valuta: {currency}",
        f"🔗 URL: {url}",
        "",
        f"📦 Listing nel DB locale: {listing_count}  (attivi: {active_count})",
        vacation_line,
        "",
        "Per configurare o ottimizzare lo shop: `/shopsetup`",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /shopsetup — wizard stub (B5)
# ---------------------------------------------------------------------------

_SETUP_STEPS = [
    "1️⃣  Nome e titolo shop",
    "2️⃣  Sezione About + banner",
    "3️⃣  Prima niche + tipo prodotto",
    "4️⃣  Policy spedizione & rimborsi",
    "5️⃣  Configurazione Etsy Ads",
    "6️⃣  Prima pubblicazione guidata",
]


async def cmd_shopsetup(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/shopsetup — wizard di configurazione shop (stub B5)."""
    steps = "\n".join(f"  {s}" for s in _SETUP_STEPS)
    await update.message.reply_text(
        "🛠 *Shop Setup Wizard*\n\n"
        "Questo wizard guiderà la configurazione completa dello shop Etsy "
        "in 6 passi:\n\n"
        f"{steps}\n\n"
        "⚠️ _Implementazione completa in Blocco 5._\n\n"
        "Per ora puoi:\n"
        "• `/shop` — vedere lo stato attuale dello shop\n"
        "• `/policy` — configurare limiti di pubblicazione\n"
        "• `/budget` — configurare limiti di spesa\n"
        "• `/niche <nome>` — avviare una pipeline per una niche specifica",
        parse_mode="Markdown",
    )


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
