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

TODO B5 — aggiungere qui le keyboard per:
  - shop_setup preview (approve_setup / skip_setup)
  - ads confirmation (ads_confirm / ads_cancel)
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
