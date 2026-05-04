"""Singleton condivisi — inizializzati nel lifespan di main.py.

Tutti i router importano da qui i singleton invece che da main.py
per evitare import circolari e accoppiamento diretto.
"""

from __future__ import annotations

import hmac
import logging
from typing import TYPE_CHECKING, Any

from fastapi import Depends, HTTPException, Request, WebSocket
from slowapi import Limiter
from slowapi.util import get_remote_address

from apps.backend.core.config import settings
from apps.backend.core.memory import MemoryManager

if TYPE_CHECKING:
    from apps.backend.core.pepe import Pepe
    from apps.backend.core.storage import StorageManager
    from apps.backend.core.scheduler import Scheduler
    from apps.backend.core.production_queue import ProductionQueueService
    from apps.backend.core.budget_manager import BudgetManager
    from apps.backend.core.publication_policy import PublicationPolicy
    from apps.backend.core.autopilot_loop import AutopilotLoop
    from apps.backend.core.bundle_strategy import BundleStrategy
    from apps.backend.core.shop_optimizer import ShopProfileOptimizer
    from apps.backend.core.etsy_ads import EtsyAdsManager
    from apps.backend.core.finance_tracker import FinanceTracker
    from apps.backend.tools.etsy_api import EtsyAPI
    from apps.backend.telegram.bot import TelegramBot
    from apps.backend.screen.watcher import ScreenWatcher

logger = logging.getLogger("agentpexi.api")


class ConnectionManager:
    """Gestisce connessioni WebSocket attive e broadcast."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info("WS client connesso (%d totali)", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.remove(ws)
        logger.info("WS client disconnesso (%d rimasti)", len(self._connections))

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Invia evento JSON a tutti i client connessi."""
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                self._connections.remove(ws)
            except ValueError:
                pass


ws_manager = ConnectionManager()

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])


async def verify_personal_key(request: Request) -> None:
    """Verifica header X-Personal-Key per endpoint personal e screen.

    Fail-closed: se PERSONAL_API_KEY non è configurata in .env, tutti gli
    endpoint personal/screen restituiscono 403. Impostare la chiave in .env
    per abilitare l'accesso.
    """
    api_key = settings.PERSONAL_API_KEY
    if not api_key:
        raise HTTPException(status_code=403, detail="PERSONAL_API_KEY non configurata")
    key = request.headers.get("X-Personal-Key", "")
    if not hmac.compare_digest(key, api_key):
        raise HTTPException(status_code=403, detail="Unauthorized")


# ---------------------------------------------------------------------------
# Singleton condivisi (assegnati nel lifespan di main.py)
# ---------------------------------------------------------------------------

memory: MemoryManager | None = None
pepe: "Pepe | None" = None
storage: "StorageManager | None" = None
etsy_api: "EtsyAPI | None" = None
scheduler: "Scheduler | None" = None
screen_watcher: "ScreenWatcher | None" = None

# Blocco 2 — Autonomy Layer
production_queue:   "ProductionQueueService | None" = None
budget_manager:     "BudgetManager | None" = None
publication_policy: "PublicationPolicy | None" = None
autopilot_loop:     "AutopilotLoop | None" = None
telegram_bot:       "TelegramBot | None" = None

# Blocco 4/5 — Intelligence & Growth Layer
bundle_strategy:    "BundleStrategy | None" = None
shop_optimizer:     "ShopProfileOptimizer | None" = None
etsy_ads_manager:   "EtsyAdsManager | None" = None
finance_tracker:    "FinanceTracker | None" = None
