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

import re

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


# ---------------------------------------------------------------------------
# Bundle blueprint approval (C.1)
# ---------------------------------------------------------------------------

_BUNDLE_CB_RE = re.compile(r"^bundle_(approve|decline):([a-f0-9]{12})$")


def build_bundle_keyboard(cluster_id: str) -> InlineKeyboardMarkup:
    """[✅ Approva Bundle] [❌ Declina] per un bundle blueprint della cluster.

    Callback data:
      "bundle_approve:{cluster_id}"  → ChromaDB: type=bundle_approval, status=approved
      "bundle_decline:{cluster_id}"  → ChromaDB: type=bundle_approval, status=declined
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approva Bundle", callback_data=f"bundle_approve:{cluster_id}"),
        InlineKeyboardButton("❌ Declina",        callback_data=f"bundle_decline:{cluster_id}"),
    ]])


def _parse_bundle_callback(data: str) -> tuple[str, str] | None:
    """Parse bundle callback_data. Returns (action, cluster_id) or None if invalid.

    Valid formats:
      "bundle_approve:<12-hex-chars>"
      "bundle_decline:<12-hex-chars>"
    """
    m = _BUNDLE_CB_RE.match(data)
    if not m:
        return None
    return m.group(1), m.group(2)

