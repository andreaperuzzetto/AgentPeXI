"""Formatting utilities per il bot Telegram.

Funzioni pure — nessuna dipendenza da TelegramBot o da services.
Importabili sia da bot.py che dai futuri handler modules (step 3.3+).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import telegram

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------

TG_LIMIT = 4000
"""Limite caratteri per messaggio Telegram (max API ~4096, lasciamo margine)."""

# ---------------------------------------------------------------------------
# Escape
# ---------------------------------------------------------------------------

_MD_SPECIAL = ("_", "*", "`", "[")


def md_escape(text: str) -> str:
    """Escapa i caratteri speciali Markdown v1 nei valori dinamici.

    Usare per nomi nicchia, file path, output LLM e qualsiasi valore
    che possa contenere ``_  *  `  [`` che romperebbero il parse Telegram.
    """
    for ch in _MD_SPECIAL:
        text = text.replace(ch, f"\\{ch}")
    return text


# ---------------------------------------------------------------------------
# Chunked send — reply
# ---------------------------------------------------------------------------

async def reply_chunked(message: "telegram.Message", text: str) -> None:
    """Invia risposta spezzandola in chunk da max TG_LIMIT se necessario.

    Spezza su newline per non troncare a metà riga.
    """
    if len(text) <= TG_LIMIT:
        await message.reply_text(text)
        return

    lines = text.splitlines(keepends=True)
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) > TG_LIMIT:
            if chunk:
                await message.reply_text(chunk.rstrip())
            chunk = line
        else:
            chunk += line
    if chunk.strip():
        await message.reply_text(chunk.rstrip())


# ---------------------------------------------------------------------------
# Chunked send — bot.send_message (notifiche push)
# ---------------------------------------------------------------------------

async def send_chunked(bot: "telegram.Bot", chat_id: int, text: str) -> None:
    """Invia un testo lungo via ``bot.send_message`` spezzandolo in chunk.

    Usato per notifiche push dove non c'è un ``Update`` disponibile.
    """
    if len(text) <= TG_LIMIT:
        await bot.send_message(chat_id=chat_id, text=text)
        return

    lines = text.splitlines(keepends=True)
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) > TG_LIMIT:
            if chunk:
                await bot.send_message(chat_id=chat_id, text=chunk.rstrip())
            chunk = line
        else:
            chunk += line
    if chunk.strip():
        await bot.send_message(chat_id=chat_id, text=chunk.rstrip())
