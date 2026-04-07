from __future__ import annotations

from typing import AsyncGenerator

import structlog
from fastapi import Cookie, Depends, HTTPException, status
from jwt.exceptions import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import decode_access_token
from db.session import get_db_session

log = structlog.get_logger()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency FastAPI per ottenere una sessione DB async."""
    async with get_db_session() as session:
        yield session


async def get_current_operator(
    access_token: str | None = Cookie(default=None),
) -> str:
    """
    Valida il cookie httpOnly `access_token`.
    Restituisce l'email dell'operatore se valido.
    Solleva 401 se assente o invalido.
    """
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "not_authenticated", "message": "Cookie access_token mancante", "detail": {}},
        )
    try:
        email = decode_access_token(access_token)
    except InvalidTokenError:
        log.warning("api.auth.invalid_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "message": "Token non valido o scaduto", "detail": {}},
        )
    return email


# Alias tipizzato per uso nei router
CurrentOperator = Depends(get_current_operator)
