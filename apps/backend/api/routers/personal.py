import logging
from typing import Annotated

import apps.backend.api.state as state
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.api")
router = APIRouter(dependencies=[Depends(state.verify_personal_key)])


@router.get("/api/personal/reminders")
async def get_personal_reminders(limit: Annotated[int, Query(ge=1, le=100)] = 10) -> dict:
    """Prossimi reminder pending ordinati per trigger_at.

    Restituisce `items` con shape attesa dal PersonalPanel:
    {id, message, when (ISO8601), status}
    """
    if not state.memory:
        return {"items": []}
    raw = await state.memory.get_pending_reminders() or []
    items = [
        {
            "id":      r.get("id"),
            "message": r.get("text", ""),
            "when":    r.get("trigger_at", ""),
            "status":  r.get("status", "pending"),
        }
        for r in raw[:limit]
    ]
    return {"items": items}


@router.get("/api/personal/recalls")
async def get_personal_recalls(limit: Annotated[int, Query(ge=1, le=100)] = 10) -> dict:
    """Ultimi N recall completati.

    Restituisce `items` con shape attesa dal PersonalPanel:
    {timestamp, agent, query, status}
    """
    if not state.memory:
        return {"items": []}
    raw = await state.memory.get_personal_recalls(limit) or []
    items = [
        {
            "timestamp": r.get("created_at") or r.get("timestamp", ""),
            "agent":     r.get("agent", "recall"),
            "query":     r.get("query") or r.get("text", ""),
            "status":    "ok" if r.get("status") != "failed" else "error",
        }
        for r in raw
    ]
    return {"items": items}


@router.get("/api/personal/mcp/status")
async def get_mcp_status() -> dict:
    """Stato connessioni MCP: Notion, Gmail, Calendar.
    Notion: ping leggero all'API se token configurato.
    Gmail/Calendar: verifica presenza token OAuth (agenti non ancora implementati).
    """
    import aiohttp

    result: dict[str, str] = {}

    # Notion
    notion_token = getattr(settings, "NOTION_API_TOKEN", "")
    if not notion_token:
        result["notion"] = "not_configured"
    else:
        try:
            timeout = aiohttp.ClientTimeout(total=4)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    "https://api.notion.com/v1/users/me",
                    headers={
                        "Authorization": f"Bearer {notion_token}",
                        "Notion-Version": "2022-06-28",
                    },
                ) as resp:
                    result["notion"] = "ok" if resp.status == 200 else f"error_{resp.status}"
        except Exception:
            result["notion"] = "error"

    # Gmail / Calendar — stesso OAuth; verifica presenza token
    google_token = getattr(settings, "GOOGLE_REFRESH_TOKEN", "")
    if not google_token:
        result["gmail"] = "not_configured"
        result["calendar"] = "not_configured"
    else:
        # Token presente — agenti non ancora implementati, stato "configured"
        result["gmail"] = "configured"
        result["calendar"] = "configured"

    return result


@router.get("/api/personal/stats")
async def get_personal_stats(days: Annotated[int, Query(ge=1, le=365)] = 14) -> dict:
    """Aggregati agenti Personal: task completati/falliti per agente, ultimi N giorni."""
    if not state.memory:
        return {"stats": {}}
    stats = await state.memory.get_domain_agent_stats(domain="personal", days=days)
    return {"stats": stats, "days": days}


@router.get("/api/ollama/status")
async def get_ollama_status() -> dict:
    """Stato Ollama: modello caricato, latenza ultima chiamata, keep_alive."""
    import time
    import aiohttp
    from urllib.parse import urlparse

    parsed = urlparse(settings.OLLAMA_BASE_URL)
    ollama_base = f"{parsed.scheme}://{parsed.netloc}"  # es. http://localhost:11434

    result = {
        "model": settings.OLLAMA_MODEL,
        "loaded": False,
        "latency_ms": None,
        "keep_alive": getattr(settings, "OLLAMA_KEEP_ALIVE", "-1"),
    }

    try:
        timeout = aiohttp.ClientTimeout(total=4)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            t0 = time.monotonic()
            async with session.get(f"{ollama_base}/api/ps") as resp:
                latency = int((time.monotonic() - t0) * 1000)
                result["latency_ms"] = latency
                if resp.status == 200:
                    data = await resp.json()
                    running = [m.get("name", "") for m in data.get("models", [])]
                    result["loaded"] = any(
                        settings.OLLAMA_MODEL in m for m in running
                    )
    except Exception as exc:
        logger.debug("get_ollama_status: Ollama non raggiungibile: %s", exc)

    return result


@router.post("/api/personal/voice/collect")
async def set_collect_mode(body: dict) -> dict:
    """Attiva/disattiva modalità raccolta campioni wake word.

    Body: {"mode": "positive" | "negative" | "off"}
    - positive: salva ogni blob WebM in training_data/positive/real_*.wav
    - negative: salva ogni blob WebM in training_data/negative/real_*.wav
    - off: disattiva la raccolta

    Dopo aver raccolto abbastanza campioni (>=20 per classe):
      python scripts/train_wake_word.py
    """
    from apps.backend.voice import collector
    mode = (body or {}).get("mode", "off")
    if mode not in ("positive", "negative", "off"):
        return JSONResponse(status_code=400, content={"error": "mode deve essere positive | negative | off"})
    collector.set_mode(mode)
    return collector.get_status()


@router.get("/api/personal/voice/collect/status")
async def get_collect_status() -> dict:
    """Stato corrente raccolta campioni: modalità attiva + conteggi per classe."""
    from apps.backend.voice import collector
    return collector.get_status()


@router.post("/api/personal/ask")
async def personal_ask(request: Request, body: dict) -> dict:
    """Endpoint voce: riceve testo trascritto, risponde via Pepe in dominio Personal.
    Usato dal PepeOrb nel frontend — nessuna pipeline, risposta diretta.
    """
    if not state.pepe:
        return JSONResponse(status_code=503, content={"error": "Pepe non inizializzato"})
    text = (body or {}).get("text", "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"error": "Campo 'text' mancante o vuoto"})
    response = await state.pepe.handle_user_message(
        text,
        source="dashboard_voice",
        session_id="dashboard",
    )
    return {"response": response}
