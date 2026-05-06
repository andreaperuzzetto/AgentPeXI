"""Pinterest API router — auth-status endpoint."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

import apps.backend.api.state as state

logger = logging.getLogger("agentpexi.api")
router = APIRouter(dependencies=[Depends(state.verify_personal_key)])


@router.get("/api/pinterest/auth-status")
async def pinterest_auth_status() -> dict:
    """Ritorna lo stato della connessione OAuth Pinterest.

    Risposta: { connected: bool, expires_at: str | null, last_refresh: str | null }
    """
    if not state.memory:
        return JSONResponse(
            status_code=503,
            content={"error": "Memory non inizializzata"},
        )

    tokens = await state.memory.get_oauth_tokens("pinterest")
    if not tokens:
        return {"connected": False, "expires_at": None, "last_refresh": None}

    expires_at_str = tokens["expires_at"]
    expires_at = datetime.fromisoformat(expires_at_str)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    connected = datetime.now(timezone.utc) < expires_at
    return {
        "connected": connected,
        "expires_at": expires_at_str,
        "last_refresh": tokens.get("updated_at"),
    }
