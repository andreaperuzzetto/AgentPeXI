import logging

import apps.backend.api.state as state
from fastapi import APIRouter, Depends

logger = logging.getLogger("agentpexi.api")
router = APIRouter()


@router.get("/api/screen/status", dependencies=[Depends(state.verify_personal_key)])
async def get_screen_status() -> dict:
    """Stato corrente del ScreenWatcher — usato per idratazione al WS connect."""
    if state.screen_watcher is None:
        return {
            "available": False,
            "active": False,
            "paused": False,
            "captures_today": 0,
            "last_capture_time": "",
            "last_capture_app": "",
        }
    st = state.screen_watcher.get_status()
    return {
        "available": True,
        **st,
    }


@router.post("/api/screen/toggle", dependencies=[Depends(state.verify_personal_key)])
async def toggle_screen_watcher() -> dict:
    """Attiva o mette in pausa ScreenWatcher.

    - Se attivo (running e non in pausa) → pausa
    - Se in pausa o fermo → riprende
    Risposta: { active: bool, available: bool }
    """
    if state.screen_watcher is None:
        return {"available": False, "active": False}
    st = state.screen_watcher.get_status()
    if st.get("active"):
        state.screen_watcher.pause()
        return {"available": True, "active": False}
    else:
        state.screen_watcher.resume()
        return {"available": True, "active": True}
