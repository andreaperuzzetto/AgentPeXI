"""Middleware di autenticazione per il bot Telegram.

Centralizza tutta la logica di autenticazione/autorizzazione in un posto solo,
in modo che handler modules (step 3.3+) non debbano reimplementarla.
"""

from __future__ import annotations

from telegram.ext import filters

from apps.backend.core.config import settings


def build_chat_filter(chat_id: str | int) -> filters.BaseFilter:
    """Costruisce il filtro ``filters.Chat`` per l'utente autorizzato.

    Fail-closed: se ``TELEGRAM_CHAT_ID`` non è configurato lancia
    ``RuntimeError`` prima che il bot parta (stesso comportamento pre-B3).

    Args:
        chat_id: ID numerico o stringa dell'utente autorizzato.

    Returns:
        ``filters.Chat`` che ammette solo quel chat_id.

    Raises:
        RuntimeError: se ``chat_id`` è vuoto/nullo.
    """
    if not chat_id:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID non configurato in .env — "
            "impostarlo per evitare che qualsiasi utente possa interagire col bot"
        )
    return filters.Chat(chat_id=int(chat_id))


def is_authorized(user_id: int | str) -> bool:
    """Verifica che ``user_id`` corrisponda all'utente autorizzato.

    Usato nei ``CallbackQueryHandler`` dove il filtro ``filters.Chat``
    non viene applicato automaticamente da python-telegram-bot.

    Args:
        user_id: ``query.from_user.id`` estratto dalla callback.

    Returns:
        ``True`` solo se corrisponde a ``settings.TELEGRAM_CHAT_ID``.
    """
    if not settings.TELEGRAM_CHAT_ID:
        return False
    return str(user_id) == str(settings.TELEGRAM_CHAT_ID)
