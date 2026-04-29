"""ProductionQueueService — state machine per il pipeline design→publish.

Gestisce il ciclo di vita di ogni listing:
    pending_design → pending_approval → approved → scheduled → published
                   ↘ failed           ↘ skipped            ↘ failed

Il service non ha dipendenze su altri service layer — riceve la connessione DB
direttamente tramite `get_db()` di MemoryManager.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class ProductionQueueItem:
    """Rappresentazione in-memory di un record production_queue."""

    id: int
    niche: str
    product_type: str              # digital_print | digital_art_png | svg_bundle | bundle
                                   # POD (B5/5.5 scaffolding — attivi quando POD_ENABLED=True):
                                   #   pod_print | pod_mug | pod_tshirt
    keywords: list[str]          # deserializzata da JSON
    entry_score: float

    # stato
    status: str                  # pending_design | pending_approval | approved |
                                 # scheduled | published | skipped | failed | discarded

    # design
    design_prompt: str | None
    image_url: str | None
    thumbnail_path: str | None
    listing_title: str | None
    listing_description: str | None
    listing_tags: list[str] | None
    listing_price: float | None

    # approvazione
    approval_sent_at: float | None
    approval_message_id: int | None
    approval_chat_id: int | None
    skip_reason: str | None      # 'user' | 'timeout' | 'budget' | 'policy'
    skip_count_user: int
    skip_count_timeout: int

    # pubblicazione
    scheduled_publish_at: float | None
    published_at: float | None
    etsy_listing_id: str | None

    # costi
    llm_cost_usd: float
    image_cost_usd: float
    listing_fee_usd: float       # 🔴 [video] $0.20 per listing al publish
    ads_activated: int           # 🔴 [video] 1 se Etsy Ads attivate
    ads_paused: int              # [FE-0.1] 1 se campagna ads messa in pausa

    # meta
    loop_run_id: str | None
    created_at: float
    updated_at: float

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "ProductionQueueItem":
        """Costruisce da aiosqlite.Row (dict-like)."""
        d = dict(row)
        return cls(
            id=d["id"],
            niche=d["niche"],
            product_type=d["product_type"],
            keywords=_loads_list(d.get("keywords")),
            entry_score=d.get("entry_score") or 0.0,
            status=d["status"],
            design_prompt=d.get("design_prompt"),
            image_url=d.get("image_url"),
            thumbnail_path=d.get("thumbnail_path"),
            listing_title=d.get("listing_title"),
            listing_description=d.get("listing_description"),
            listing_tags=_loads_list(d.get("listing_tags")),
            listing_price=d.get("listing_price"),
            approval_sent_at=d.get("approval_sent_at"),
            approval_message_id=d.get("approval_message_id"),
            approval_chat_id=d.get("approval_chat_id"),
            skip_reason=d.get("skip_reason"),
            skip_count_user=d.get("skip_count_user") or 0,
            skip_count_timeout=d.get("skip_count_timeout") or 0,
            scheduled_publish_at=d.get("scheduled_publish_at"),
            published_at=d.get("published_at"),
            etsy_listing_id=d.get("etsy_listing_id"),
            llm_cost_usd=d.get("llm_cost_usd") or 0.0,
            image_cost_usd=d.get("image_cost_usd") or 0.0,
            listing_fee_usd=d.get("listing_fee_usd") or 0.20,
            ads_activated=d.get("ads_activated") or 0,
            ads_paused=d.get("ads_paused") or 0,
            loop_run_id=d.get("loop_run_id"),
            created_at=_to_float(d.get("created_at")),
            updated_at=_to_float(d.get("updated_at")),
        )


# ---------------------------------------------------------------------------
# Helpers interni
# ---------------------------------------------------------------------------

def _loads_list(raw: Any) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def _dumps_list(lst: list | None) -> str | None:
    if lst is None:
        return None
    return json.dumps(lst, ensure_ascii=False)


def _to_float(v: Any) -> float:
    """Converte timestamp: unix float o testo ISO → float."""
    if v is None:
        return time.time()
    if isinstance(v, (int, float)):
        return float(v)
    # ISO string da CURRENT_TIMESTAMP sqlite
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v)).timestamp()
    except ValueError:
        return time.time()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ProductionQueueService:
    """State machine per production_queue.

    Usage::

        from apps.backend.core.memory import memory_manager
        queue = ProductionQueueService(await memory_manager.get_db())
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        cursor = await self._db.execute(sql, params)
        return await cursor.fetchone()

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        cursor = await self._db.execute(sql, params)
        return await cursor.fetchall()

    def _now(self) -> float:
        return time.time()

    # ------------------------------------------------------------------
    # Write — creazione e transizioni
    # ------------------------------------------------------------------

    async def create_item(
        self,
        niche: str,
        product_type: str,
        keywords: list[str],
        entry_score: float = 0.0,
        loop_run_id: str | None = None,
    ) -> int:
        """Crea un nuovo item in pending_design. Restituisce l'id."""
        now = self._now()
        # task_id è UNIQUE NOT NULL nel vecchio schema — generiamo un UUID
        task_id = str(uuid.uuid4())
        cursor = await self._db.execute(
            """
            INSERT INTO production_queue
                (task_id, niche, product_type, keywords, entry_score,
                 status, listing_fee_usd, loop_run_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending_design', 0.20, ?, ?, ?)
            """,
            (task_id, niche, product_type, _dumps_list(keywords), entry_score,
             loop_run_id, now, now),
        )
        await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def set_design_ready(
        self,
        item_id: int,
        design_prompt: str,
        image_url: str,
        thumbnail_path: str,
        title: str,
        description: str,
        tags: list[str],
        price: float,
        llm_cost: float = 0.0,
        image_cost: float = 0.0,
    ) -> None:
        """pending_design → pending_approval."""
        now = self._now()
        await self._db.execute(
            """
            UPDATE production_queue SET
                status              = 'pending_approval',
                design_prompt       = ?,
                image_url           = ?,
                thumbnail_path      = ?,
                listing_title       = ?,
                listing_description = ?,
                listing_tags        = ?,
                listing_price       = ?,
                llm_cost_usd        = ?,
                image_cost_usd      = ?,
                approval_sent_at    = ?,
                updated_at          = ?
            WHERE id = ?
            """,
            (design_prompt, image_url, thumbnail_path, title, description,
             _dumps_list(tags), price, llm_cost, image_cost, now, now, item_id),
        )
        await self._db.commit()

    async def set_approved(
        self,
        item_id: int,
        message_id: int | None = None,
        chat_id: int | None = None,
    ) -> None:
        """pending_approval → approved."""
        await self._db.execute(
            """
            UPDATE production_queue SET
                status              = 'approved',
                approval_message_id = ?,
                approval_chat_id    = ?,
                updated_at          = ?
            WHERE id = ?
            """,
            (message_id, chat_id, self._now(), item_id),
        )
        await self._db.commit()

    async def set_skipped(self, item_id: int, reason: str) -> None:
        """→ skipped; aggiorna il contatore appropriato."""
        if reason == "user":
            count_col = "skip_count_user"
        elif reason == "timeout":
            count_col = "skip_count_timeout"
        else:
            count_col = "skip_count_user"   # fallback

        await self._db.execute(
            f"""
            UPDATE production_queue SET
                status     = 'skipped',
                skip_reason = ?,
                {count_col} = COALESCE({count_col}, 0) + 1,
                updated_at  = ?
            WHERE id = ?
            """,
            (reason, self._now(), item_id),
        )
        await self._db.commit()

    async def assign_slot(self, item_id: int, publish_at: float) -> None:
        """approved → scheduled."""
        await self._db.execute(
            """
            UPDATE production_queue SET
                status               = 'scheduled',
                scheduled_publish_at = ?,
                updated_at           = ?
            WHERE id = ?
            """,
            (publish_at, self._now(), item_id),
        )
        await self._db.commit()

    async def set_published(
        self, item_id: int, etsy_listing_id: str
    ) -> None:
        """scheduled → published."""
        now = self._now()
        await self._db.execute(
            """
            UPDATE production_queue SET
                status          = 'published',
                etsy_listing_id = ?,
                published_at    = ?,
                updated_at      = ?
            WHERE id = ?
            """,
            (etsy_listing_id, now, now, item_id),
        )
        await self._db.commit()

    async def set_failed(self, item_id: int, error: str) -> None:
        """→ failed."""
        await self._db.execute(
            """
            UPDATE production_queue SET
                status     = 'failed',
                skip_reason = ?,
                updated_at  = ?
            WHERE id = ?
            """,
            (error[:500], self._now(), item_id),
        )
        await self._db.commit()

    async def set_ads_activated(self, item_id: int) -> None:
        """🔴 Marca ads_activated=1 dopo il publish."""
        await self._db.execute(
            "UPDATE production_queue SET ads_activated=1, updated_at=? WHERE id=?",
            (self._now(), item_id),
        )
        await self._db.commit()

    async def set_ads_paused(self, item_id: int) -> None:
        """[FE-0.1] Marca ads_paused=1 quando EtsyAdsManager mette in pausa una campagna."""
        await self._db.execute(
            "UPDATE production_queue SET ads_paused=1, updated_at=? WHERE id=?",
            (self._now(), item_id),
        )
        await self._db.commit()

    async def discard_stale_approvals(
        self, max_age_seconds: float = 86400
    ) -> int:
        """Marca discarded gli item in pending_approval più vecchi di max_age_seconds.

        Chiamato solo all'avvio (non durante il ciclo) per evitare race condition.
        Restituisce quanti item sono stati scartati.
        """
        cutoff = self._now() - max_age_seconds
        cursor = await self._db.execute(
            """
            UPDATE production_queue
            SET status='discarded', updated_at=?
            WHERE status='pending_approval' AND approval_sent_at < ?
            """,
            (self._now(), cutoff),
        )
        await self._db.commit()
        return cursor.rowcount  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_item(self, item_id: int) -> ProductionQueueItem | None:
        row = await self._fetchone(
            "SELECT * FROM production_queue WHERE id = ?", (item_id,)
        )
        return ProductionQueueItem.from_row(row) if row else None

    async def get_pending_approval(self) -> list[ProductionQueueItem]:
        rows = await self._fetchall(
            "SELECT * FROM production_queue WHERE status='pending_approval' ORDER BY id ASC"
        )
        return [ProductionQueueItem.from_row(r) for r in rows]

    async def get_approved_items(self) -> list[ProductionQueueItem]:
        """Item approvati senza slot ancora assegnato."""
        rows = await self._fetchall(
            """
            SELECT * FROM production_queue
            WHERE status='approved' AND scheduled_publish_at IS NULL
            ORDER BY id ASC
            """
        )
        return [ProductionQueueItem.from_row(r) for r in rows]

    async def get_due_scheduled(self, now: float | None = None) -> list[ProductionQueueItem]:
        """Item scheduled con scheduled_publish_at ≤ now."""
        now = now or self._now()
        rows = await self._fetchall(
            """
            SELECT * FROM production_queue
            WHERE status='scheduled' AND scheduled_publish_at <= ?
            ORDER BY scheduled_publish_at ASC
            """,
            (now,),
        )
        return [ProductionQueueItem.from_row(r) for r in rows]

    async def get_items_by_status(self, status: str) -> list[ProductionQueueItem]:
        rows = await self._fetchall(
            "SELECT * FROM production_queue WHERE status=? ORDER BY id ASC",
            (status,),
        )
        return [ProductionQueueItem.from_row(r) for r in rows]

    async def get_recent(
        self,
        limit: int = 20,
        status: str | None = None,
        days: int | None = None,
    ) -> list[ProductionQueueItem]:
        """Query generica per /list, /history, ecc."""
        conditions: list[str] = []
        params: list[Any] = []

        if status:
            conditions.append("status = ?")
            params.append(status)
        if days:
            cutoff = self._now() - days * 86400
            conditions.append("created_at >= ?")
            params.append(cutoff)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        rows = await self._fetchall(
            f"SELECT * FROM production_queue {where} ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
        return [ProductionQueueItem.from_row(r) for r in rows]

    async def get_last_skipped(
        self, limit: int = 3, reason: str | None = None
    ) -> list[ProductionQueueItem]:
        """Ultimi N item skippati, opzionalmente filtrati per skip_reason."""
        if reason:
            rows = await self._fetchall(
                """
                SELECT * FROM production_queue
                WHERE status='skipped' AND skip_reason=?
                ORDER BY updated_at DESC LIMIT ?
                """,
                (reason, limit),
            )
        else:
            rows = await self._fetchall(
                """
                SELECT * FROM production_queue
                WHERE status='skipped'
                ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            )
        return [ProductionQueueItem.from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Contatori consecutivi (letti da AutopilotLoop)
    # ------------------------------------------------------------------

    async def consecutive_user_skips(self) -> int:
        """Numero di skip 'user' consecutivi (dalla fine, interrotti da approvazione)."""
        rows = await self._fetchall(
            """
            SELECT status, skip_reason FROM production_queue
            ORDER BY id DESC LIMIT 20
            """
        )
        count = 0
        for row in rows:
            d = dict(row)
            if d["status"] == "skipped" and d.get("skip_reason") == "user":
                count += 1
            elif d["status"] == "approved":
                break  # reset
        return count

    async def consecutive_timeouts(self) -> int:
        """Numero di skip 'timeout' consecutivi."""
        rows = await self._fetchall(
            """
            SELECT status, skip_reason FROM production_queue
            ORDER BY id DESC LIMIT 20
            """
        )
        count = 0
        for row in rows:
            d = dict(row)
            if d["status"] == "skipped" and d.get("skip_reason") == "timeout":
                count += 1
            elif d["status"] == "approved":
                break
        return count

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def count_published_today(self) -> int:
        """Quanti listing sono stati pubblicati oggi (UTC)."""
        from datetime import datetime, timezone
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM production_queue WHERE status='published' AND published_at >= ?",
            (today_start,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0
