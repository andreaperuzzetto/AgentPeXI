"""Inline keyboard builders e callback data helpers — AgentPeXI Telegram.

Centralizza la costruzione di tutte le InlineKeyboardMarkup in modo che
i handler module (handlers/autopilot.py, handlers/config.py, ecc.) possano
importarle senza duplicazioni.

Convenzione callback_data:
  "<action>:<id>"   — azione su un item identificato da intero
  "<action>:<key>"  — azione su una chiave stringa

Registrazione dei CallbackQueryHandler: rimane in handlers/autopilot.py
(o nel modulo proprietario dell'azione) per coerenza con il pattern
register(app, deps, chat_filter).
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# ---------------------------------------------------------------------------
# Approvazione item AutopilotLoop
# ---------------------------------------------------------------------------

def build_approval_keyboard(item_id: int) -> InlineKeyboardMarkup:
    """[✅ Approva] [⏭ Salta] per un item della ProductionQueue.

    Callback data:
      "approve:<item_id>"  → loop.register_approval(item_id, "approved")
      "skip:<item_id>"     → loop.register_approval(item_id, "skipped_user")
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approva", callback_data=f"approve:{item_id}"),
        InlineKeyboardButton("⏭ Salta",   callback_data=f"skip:{item_id}"),
    ]])


# ---------------------------------------------------------------------------
# Shop Setup preview confirmation
# ---------------------------------------------------------------------------

def build_setup_keyboard() -> InlineKeyboardMarkup:
    """[✅ Applica] [❌ Annulla] — conferma inline dopo il preview shop.

    Callback data:
      "approve_setup"  → applica il profilo shop ottimizzato
      "skip_setup"     → annulla senza modificare il profilo
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Applica",  callback_data="approve_setup"),
        InlineKeyboardButton("❌ Annulla",  callback_data="skip_setup"),
    ]])


# ---------------------------------------------------------------------------
# Etsy Ads toggle confirmation
# ---------------------------------------------------------------------------

def build_ads_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    """Bottoni toggle per Etsy Ads nello status display.

    Args:
        enabled: stato corrente degli Ads.

    Callback data:
      "ads_confirm:on"   → abilita Etsy Ads
      "ads_confirm:off"  → disabilita Etsy Ads
    """
    if enabled:
        toggle_btn = InlineKeyboardButton("⚫ Disabilita Ads", callback_data="ads_confirm:off")
    else:
        toggle_btn = InlineKeyboardButton("✅ Abilita Ads", callback_data="ads_confirm:on")

    return InlineKeyboardMarkup([[toggle_btn]])
