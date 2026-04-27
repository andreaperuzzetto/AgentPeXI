"""PublicationPolicy — regole di cadenza, cooldown e finestre di pubblicazione.

Tutti i parametri sono persistiti nella tabella `config` (chiave-valore) e
modificabili a runtime tramite /policy (Telegram).

Config keys gestite:
    policy.max_per_day           — max listing pubblicati al giorno
    policy.min_gap_hours         — gap minimo tra due publish
    policy.niche_cooldown_days   — giorni di cooldown per stessa niche
    policy.availability_start    — inizio finestra (HH:MM, default "08:00")
    policy.availability_end      — fine finestra  (HH:MM, default "00:00" = mezzanotte)
    policy.etsy_ads_on_publish   — 🔴 [video] "true"/"false"
    policy.etsy_ads_daily_budget — 🔴 [video] EUR/giorno per listing ads
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import aiosqlite

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, str] = {
    "policy.max_per_day":           "5",
    "policy.min_gap_hours":         "2",
    "policy.niche_cooldown_days":   "7",
    "policy.availability_start":    "08:00",
    "policy.availability_end":      "00:00",
    "policy.etsy_ads_on_publish":   "false",   # 🔴 [video]
    "policy.etsy_ads_daily_budget": "1.00",    # 🔴 [video]
}

# Guardia anti-loop infinito in next_available_slot
_MAX_SLOT_SEARCH_DAYS = 14


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _parse_hhmm(s: str) -> tuple[int, int]:
    """Converte "HH:MM" → (hour, minute). Ritorna (0, 0) su errore."""
    try:
        h, m = s.strip().split(":")
        return int(h) % 24, int(m) % 60
    except (ValueError, AttributeError):
        return 0, 0


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class PublicationPolicy:
    """Gestione policy di pubblicazione.

    Usage::

        policy = PublicationPolicy(await memory_manager.get_db())
        await policy.ensure_defaults()
        slot = await policy.next_available_slot()
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
    # Config helpers
    # ------------------------------------------------------------------

    async def _get_str(self, key: str, default: str) -> str:
        cursor = await self._db.execute(
            "SELECT value FROM config WHERE key=?", (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else default

    async def _get_int(self, key: str, default: int) -> int:
        raw = await self._get_str(key, str(default))
        try:
            return int(raw)
        except (ValueError, TypeError):
            return default

    async def _get_float(self, key: str, default: float) -> float:
        raw = await self._get_str(key, str(default))
        try:
            return float(raw)
        except (ValueError, TypeError):
            return default

    # ------------------------------------------------------------------
    # Pubblicazioni di oggi
    # ------------------------------------------------------------------

    def _today_start_ts(self) -> float:
        return datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()

    async def published_today_count(self) -> int:
        """Numero di listing pubblicati oggi (published_at nella giornata UTC corrente)."""
        today = self._today_start_ts()
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM production_queue WHERE status='published' AND published_at >= ?",
            (today,),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def can_publish_today(self) -> bool:
        """True se published today < max_per_day."""
        max_pd = await self._get_int("policy.max_per_day", 5)
        count  = await self.published_today_count()
        return count < max_pd

    # ------------------------------------------------------------------
    # Finestra di disponibilità
    # ------------------------------------------------------------------

    async def is_in_availability_window(self, dt: datetime | None = None) -> bool:
        """True se dt è compreso nella finestra [start, end).

        Default: 08:00–00:00 (mezzanotte del giorno successivo).
        Se start == end la finestra è considerata sempre aperta (24h).
        """
        dt = dt or datetime.now()

        start_str = await self._get_str("policy.availability_start", "08:00")
        end_str   = await self._get_str("policy.availability_end",   "00:00")

        sh, sm = _parse_hhmm(start_str)
        eh, em = _parse_hhmm(end_str)

        start = dt.replace(hour=sh, minute=sm, second=0, microsecond=0)

        if eh == 0 and em == 0:
            # mezzanotte = fine giorno
            end = dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        else:
            end = dt.replace(hour=eh, minute=em, second=0, microsecond=0)
            if end <= start:
                end += timedelta(days=1)

        if start == end:
            return True   # finestra 24h

        return start <= dt < end

    # ------------------------------------------------------------------
    # Cooldown niche
    # ------------------------------------------------------------------

    async def niche_on_cooldown(self, niche: str) -> bool:
        """True se la niche è stata pubblicata negli ultimi niche_cooldown_days."""
        cooldown_days = await self._get_int("policy.niche_cooldown_days", 7)
        cutoff = time.time() - cooldown_days * 86400
        cursor = await self._db.execute(
            """
            SELECT COUNT(*) FROM production_queue
            WHERE niche=? AND status='published' AND published_at >= ?
            """,
            (niche, cutoff),
        )
        row = await cursor.fetchone()
        return (int(row[0]) if row else 0) > 0

    # ------------------------------------------------------------------
    # Slot scheduling
    # ------------------------------------------------------------------

    async def _last_scheduled_ts(self, from_date: datetime) -> float | None:
        """Restituisce lo scheduled_publish_at più recente del giorno from_date."""
        day_start = from_date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        day_end   = day_start + 86400
        cursor = await self._db.execute(
            """
            SELECT MAX(scheduled_publish_at) FROM production_queue
            WHERE scheduled_publish_at >= ? AND scheduled_publish_at < ?
              AND status IN ('scheduled', 'published')
            """,
            (day_start, day_end),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row and row[0] is not None else None

    async def _published_count_on(self, date: datetime) -> int:
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        day_end   = day_start + 86400
        cursor = await self._db.execute(
            """
            SELECT COUNT(*) FROM production_queue
            WHERE status='published'
              AND published_at >= ? AND published_at < ?
            """,
            (day_start, day_end),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def next_available_slot(
        self, from_dt: datetime | None = None
    ) -> datetime:
        """Calcola il prossimo slot di pubblicazione disponibile.

        Algoritmo:
          1. Parti dall'ultimo scheduled_publish_at del giorno (o from_dt).
          2. Aggiungi min_gap_hours.
          3. Se fuori finestra → giorno successivo, ore 08:00.
          4. Se quota giornaliera raggiunta → giorno successivo, ore 08:00.
          5. Ripeti finché non trovi uno slot valido (max 14 giorni).
        """
        from_dt   = from_dt or datetime.now()
        max_pd    = await self._get_int("policy.max_per_day",   5)
        gap_hours = await self._get_int("policy.min_gap_hours", 2)
        start_str = await self._get_str("policy.availability_start", "08:00")
        sh, sm    = _parse_hhmm(start_str)

        candidate_date = from_dt.date()
        deadline = from_dt + timedelta(days=_MAX_SLOT_SEARCH_DAYS)

        for _ in range(_MAX_SLOT_SEARCH_DAYS * 24):
            # Ricava datetime dell'inizio del giorno candidato
            day_dt = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                hour=sh, minute=sm,
            )

            # Quante pubblicazioni già schedate/pubblicate quel giorno?
            count = await self._published_count_on(day_dt)
            if count >= max_pd:
                candidate_date += timedelta(days=1)
                continue

            # Punto di partenza dello slot: ultimo scheduled quel giorno o from_dt
            last_ts = await self._last_scheduled_ts(day_dt)
            if last_ts is not None:
                anchor = datetime.fromtimestamp(last_ts)
            else:
                # Primo slot del giorno: max(from_dt, inizio finestra)
                anchor = max(from_dt, day_dt)

            candidate = anchor + timedelta(hours=gap_hours)

            # Garantisci minuto intero (più leggibile nei log)
            candidate = candidate.replace(second=0, microsecond=0)

            # Controlla finestra disponibilità
            in_window = await self.is_in_availability_window(candidate)
            if in_window and candidate <= deadline:
                return candidate

            # Fuori finestra → prossimo giorno
            candidate_date += timedelta(days=1)

        # Fallback di sicurezza (non dovrebbe mai arrivarci)
        fallback = from_dt + timedelta(days=1)
        return fallback.replace(hour=sh, minute=sm, second=0, microsecond=0)

    # ------------------------------------------------------------------
    # Etsy Ads 🔴
    # ------------------------------------------------------------------

    async def ads_enabled(self) -> bool:
        """True se policy.etsy_ads_on_publish == 'true'."""
        val = await self._get_str("policy.etsy_ads_on_publish", "false")
        return val.strip().lower() == "true"

    async def ads_daily_budget(self) -> float:
        """EUR/giorno configurato per Etsy Ads."""
        return await self._get_float("policy.etsy_ads_daily_budget", 1.00)

    # ------------------------------------------------------------------
    # CRUD config
    # ------------------------------------------------------------------

    async def get_all(self) -> dict[str, str]:
        """Tutte le config policy — per /config command."""
        cursor = await self._db.execute(
            "SELECT key, value FROM config WHERE key LIKE 'policy.%' ORDER BY key"
        )
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def set_config(self, key: str, value: str) -> None:
        """Aggiorna (o inserisce) una config policy."""
        full_key = f"policy.{key}" if not key.startswith("policy.") else key
        await self._db.execute(
            "INSERT OR REPLACE INTO config(key, value, updated_at) VALUES(?, ?, ?)",
            (full_key, value, time.time()),
        )
        await self._db.commit()
