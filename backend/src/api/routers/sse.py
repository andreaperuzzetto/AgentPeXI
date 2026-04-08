"""
SSE router — stream eventi real-time di un run agli operatori.

GET /runs/{run_id}/events
  - text/event-stream
  - Protetto da cookie JWT operatore
  - Sottoscrive al canale Redis `run_events:{run_id}` e trasferisce ogni messaggio come SSE
  - Chiude lo stream quando il run termina (status completed/failed/cancelled)
    oppure alla disconnessione del client (CancelledError)
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncGenerator

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from api.deps import get_current_operator

log = structlog.get_logger()

router = APIRouter(prefix="/runs", tags=["sse"])

_TERMINAL_STATUSES = {"run_completed", "run_failed", "run_cancelled"}
# Timeout dopo cui chiudiamo lo stream se non arrivano eventi (secondi)
_KEEPALIVE_INTERVAL = 15
# Numero max di eventi da trasferire per connessione (sicurezza)
_MAX_EVENTS = 500


async def _event_stream(run_id: str) -> AsyncGenerator[str, None]:
    redis_url = os.environ["REDIS_URL"]
    channel = f"run_events:{run_id}"
    event_count = 0

    r = aioredis.from_url(redis_url)
    pubsub = r.pubsub()
    await pubsub.subscribe(channel)

    try:
        # Keepalive iniziale — dice al browser che la connessione è aperta
        yield ": keepalive\n\n"

        while event_count < _MAX_EVENTS:
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=None),
                    timeout=_KEEPALIVE_INTERVAL,
                )
            except asyncio.TimeoutError:
                # Nessun evento — invia commento keepalive per tenere viva la connessione
                yield ": keepalive\n\n"
                continue

            if message is None:
                await asyncio.sleep(0.05)
                continue

            data = message.get("data", b"")
            if isinstance(data, bytes):
                data = data.decode()

            event_count += 1
            yield f"data: {data}\n\n"

            # Chiudi lo stream se il run è terminato
            try:
                payload = json.loads(data)
                if payload.get("event_type") in _TERMINAL_STATUSES:
                    break
            except (json.JSONDecodeError, AttributeError):
                pass

    except asyncio.CancelledError:
        # Client disconnesso — uscita pulita
        pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await r.aclose()


@router.get("/{run_id}/events")
async def run_events(
    run_id: str,
    _operator: str = Depends(get_current_operator),
) -> StreamingResponse:
    """
    Stream SSE degli eventi di un run.
    Il client deve connettersi con `EventSource` e gestire la riconnessione automatica.
    """
    return StreamingResponse(
        _event_stream(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",     # Disabilita buffering nginx
            "Connection": "keep-alive",
        },
    )
