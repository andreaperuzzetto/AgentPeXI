"""Pinterest API router — auth-status and status endpoints."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

import apps.backend.api.state as state

logger = logging.getLogger("agentpexi.api")
router = APIRouter(dependencies=[Depends(state.verify_personal_key)])


@router.get("/api/pinterest/auth-status")
async def pinterest_auth_status() -> dict[str, Any]:
    """Ritorna lo stato della connessione OAuth Pinterest.

    Risposta: { connected: bool, expires_at: str | null, last_refresh: str | null }
    """
    if not state.memory:
        raise HTTPException(status_code=503, detail="Memory non inizializzata")

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


@router.get("/api/pinterest/status")
async def pinterest_status() -> dict[str, Any]:
    """Stato operativo completo della Pinterest Machine.

    Risposta: {
        delivery_method, connected, pins_today, pins_queued, pins_failed,
        boards: list[{board_id, board_name, section_key, pin_count}],
        cost_today_usd, next_pin_at
    }
    """
    if not state.memory:
        raise HTTPException(status_code=503, detail="Memory non inizializzata")

    delivery_method = os.getenv("PINTEREST_DELIVERY_METHOD", "tailwind")

    # OAuth connected check
    tokens = await state.memory.get_oauth_tokens("pinterest")
    connected = False
    if tokens:
        expires_at_str = tokens["expires_at"]
        expires_at = datetime.fromisoformat(expires_at_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        connected = datetime.now(timezone.utc) < expires_at

    db = await state.memory.get_db()

    # Pin counts
    row = await db.execute_fetchall(
        "SELECT "
        "  SUM(CASE WHEN status='published' AND DATE(published_at)=DATE('now') THEN 1 ELSE 0 END) AS pins_today, "
        "  SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pins_queued, "
        "  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS pins_failed, "
        "  SUM(CASE WHEN DATE(created_at)=DATE('now') THEN COALESCE(cost_image_gen,0)+COALESCE(cost_llm,0) ELSE 0 END) AS cost_today "
        "FROM pinterest_queue"
    )
    counts = dict(row[0]) if row else {}
    pins_today = int(counts.get("pins_today") or 0)
    pins_queued = int(counts.get("pins_queued") or 0)
    pins_failed = int(counts.get("pins_failed") or 0)
    cost_today_usd = round(float(counts.get("cost_today") or 0.0), 6)

    # Next scheduled pending pin
    next_row = await db.execute_fetchall(
        "SELECT MIN(scheduled_at) AS next_pin_at FROM pinterest_queue WHERE status='pending'"
    )
    next_pin_at = None
    if next_row and next_row[0]["next_pin_at"]:
        next_pin_at = next_row[0]["next_pin_at"]

    # Active boards
    boards_rows = await db.execute_fetchall(
        "SELECT board_id, board_name, section_key, pin_count "
        "FROM pinterest_boards WHERE is_active=1 ORDER BY pin_count DESC"
    )
    boards = [
        {
            "board_id": r["board_id"],
            "board_name": r["board_name"],
            "section_key": r["section_key"],
            "pin_count": r["pin_count"],
        }
        for r in boards_rows
    ]

    return {
        "delivery_method": delivery_method,
        "connected": connected,
        "pins_today": pins_today,
        "pins_queued": pins_queued,
        "pins_failed": pins_failed,
        "boards": boards,
        "cost_today_usd": cost_today_usd,
        "next_pin_at": next_pin_at,
    }

