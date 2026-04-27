"""BudgetManager — monitoraggio costi giornalieri e limiti di spesa.

Traccia tre voci di costo che derivano da production_queue:
  - LLM calls      (llm_cost_usd)
  - Image gen      (image_cost_usd)
  - Listing fee    (listing_fee_usd, 🔴 [video] $0.20 per publish — costo reale Etsy)

I limiti sono persistiti nella tabella `config` (chiave-valore) così sopravvivono
ai restart e sono modificabili da Telegram (/budget).

`check_budget()` considera la voce peggiore tra le tre — se anche una sola supera
la soglia il risultato è WARNING / EXCEEDED.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

import aiosqlite

# ---------------------------------------------------------------------------
# Enums / Dataclasses
# ---------------------------------------------------------------------------

class BudgetStatus(Enum):
    OK       = "ok"
    WARNING  = "warning"    # ≥ 75% su almeno una voce
    EXCEEDED = "exceeded"   # ≥ 100% su almeno una voce


@dataclass
class BudgetSummary:
    """Snapshot dei costi attuali — usato da formatters (/status, /budget)."""

    llm_today:      float
    image_today:    float
    fee_today:      float

    llm_limit:      float
    image_limit:    float
    fee_limit:      float

    warn_threshold: float   # es. 0.75

    status: BudgetStatus

    @property
    def llm_pct(self) -> float:
        return self.llm_today / self.llm_limit if self.llm_limit else 0.0

    @property
    def image_pct(self) -> float:
        return self.image_today / self.image_limit if self.image_limit else 0.0

    @property
    def fee_pct(self) -> float:
        return self.fee_today / self.fee_limit if self.fee_limit else 0.0

    @property
    def total_today(self) -> float:
        return self.llm_today + self.image_today + self.fee_today

    @property
    def total_limit(self) -> float:
        return self.llm_limit + self.image_limit + self.fee_limit


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, str] = {
    "budget.daily_llm_usd":         "0.50",
    "budget.daily_image_usd":       "1.00",
    "budget.daily_listing_fee_usd": "1.00",  # 🔴 5 listing × $0.20
    "budget.warn_threshold":        "0.75",
}


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class BudgetManager:
    """Gestione costi giornalieri con limiti configurabili.

    Usage::

        budget = BudgetManager(await memory_manager.get_db())
        await budget.ensure_defaults()
        status = await budget.check_budget()
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    async def ensure_defaults(self) -> None:
        """Inserisce i default nella tabella config se non già presenti."""
        for key, value in _DEFAULTS.items():
            await self._db.execute(
                "INSERT OR IGNORE INTO config(key, value, updated_at) VALUES(?, ?, ?)",
                (key, value, time.time()),
            )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _today_start(self) -> float:
        """Timestamp Unix dell'inizio della giornata corrente (UTC)."""
        return datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()

    async def _get_config(self, key: str, default: float) -> float:
        cursor = await self._db.execute(
            "SELECT value FROM config WHERE key=?", (key,)
        )
        row = await cursor.fetchone()
        if row is None:
            return default
        try:
            return float(row[0])
        except (ValueError, TypeError):
            return default

    # ------------------------------------------------------------------
    # Costi di oggi
    # ------------------------------------------------------------------

    async def today_llm_cost(self) -> float:
        """Somma llm_cost_usd di tutti gli item creati oggi."""
        today = self._today_start()
        cursor = await self._db.execute(
            "SELECT COALESCE(SUM(llm_cost_usd), 0.0) FROM production_queue WHERE created_at >= ?",
            (today,),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0.0

    async def today_image_cost(self) -> float:
        """Somma image_cost_usd di tutti gli item creati oggi."""
        today = self._today_start()
        cursor = await self._db.execute(
            "SELECT COALESCE(SUM(image_cost_usd), 0.0) FROM production_queue WHERE created_at >= ?",
            (today,),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0.0

    async def today_listing_fee_cost(self) -> float:
        """🔴 Somma listing_fee_usd degli item pubblicati oggi.

        La listing fee è un costo al publish, non alla creazione — si conta solo
        su status='published' E published_at nella giornata corrente.
        """
        today = self._today_start()
        cursor = await self._db.execute(
            """
            SELECT COALESCE(SUM(listing_fee_usd), 0.0)
            FROM production_queue
            WHERE status='published' AND published_at >= ?
            """,
            (today,),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0.0

    # ------------------------------------------------------------------
    # Limiti
    # ------------------------------------------------------------------

    async def get_limits(self) -> dict[str, float]:
        """Legge tutti i limiti dalla tabella config."""
        return {
            "daily_llm_usd":         await self._get_config("budget.daily_llm_usd",         0.50),
            "daily_image_usd":       await self._get_config("budget.daily_image_usd",       1.00),
            "daily_listing_fee_usd": await self._get_config("budget.daily_listing_fee_usd", 1.00),
            "warn_threshold":        await self._get_config("budget.warn_threshold",        0.75),
        }

    async def set_limit(self, key: str, value: float) -> None:
        """Aggiorna un limite nella tabella config.

        key può essere: 'daily_llm_usd', 'daily_image_usd',
                        'daily_listing_fee_usd', 'warn_threshold'
        """
        full_key = f"budget.{key}" if not key.startswith("budget.") else key
        await self._db.execute(
            "INSERT OR REPLACE INTO config(key, value, updated_at) VALUES(?, ?, ?)",
            (full_key, str(value), time.time()),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Check
    # ------------------------------------------------------------------

    async def check_budget(self) -> BudgetStatus:
        """Controlla tutte e 3 le voci; restituisce lo stato peggiore."""
        limits = await self.get_limits()
        warn   = limits["warn_threshold"]

        llm_today   = await self.today_llm_cost()
        image_today = await self.today_image_cost()
        fee_today   = await self.today_listing_fee_cost()

        ratios = [
            llm_today   / limits["daily_llm_usd"]         if limits["daily_llm_usd"]         else 0.0,
            image_today / limits["daily_image_usd"]       if limits["daily_image_usd"]       else 0.0,
            fee_today   / limits["daily_listing_fee_usd"] if limits["daily_listing_fee_usd"] else 0.0,
        ]

        worst = max(ratios)
        if worst >= 1.0:
            return BudgetStatus.EXCEEDED
        if worst >= warn:
            return BudgetStatus.WARNING
        return BudgetStatus.OK

    # ------------------------------------------------------------------
    # Record costi
    # ------------------------------------------------------------------

    async def record_costs(
        self, item_id: int, llm: float, image: float
    ) -> None:
        """Aggiorna llm_cost_usd e image_cost_usd su un item della coda."""
        await self._db.execute(
            """
            UPDATE production_queue
            SET llm_cost_usd=?, image_cost_usd=?, updated_at=?
            WHERE id=?
            """,
            (llm, image, time.time(), item_id),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Summary (per formatters)
    # ------------------------------------------------------------------

    async def get_status_summary(self) -> BudgetSummary:
        """Ritorna snapshot completo — usato da /status e /budget."""
        limits      = await self.get_limits()
        llm_today   = await self.today_llm_cost()
        image_today = await self.today_image_cost()
        fee_today   = await self.today_listing_fee_cost()
        status      = await self.check_budget()

        return BudgetSummary(
            llm_today      = llm_today,
            image_today    = image_today,
            fee_today      = fee_today,
            llm_limit      = limits["daily_llm_usd"],
            image_limit    = limits["daily_image_usd"],
            fee_limit      = limits["daily_listing_fee_usd"],
            warn_threshold = limits["warn_threshold"],
            status         = status,
        )
