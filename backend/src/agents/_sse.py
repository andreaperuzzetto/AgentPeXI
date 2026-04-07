from __future__ import annotations

import json
import os

import redis.asyncio as aioredis


async def _publish_sse(
    run_id: str,
    event_type: str,
    agent: str,
    payload: dict,
) -> None:
    """Pubblica un evento SSE su Redis per il frontend. No-op se run_id è vuoto."""
    if not run_id:
        return

    message = json.dumps(
        {
            "event_type": event_type,
            "agent": agent,
            "payload": payload,
        },
        default=str,
    )

    r = aioredis.from_url(os.environ["REDIS_URL"])
    try:
        await r.publish(f"run_events:{run_id}", message)
    finally:
        await r.aclose()
