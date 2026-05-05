"""EtsySectionsService — gestione sezioni Etsy e mappa niche→sezione.

Segue il pattern ProductionQueueService/ShopIdentityService:
riceve aiosqlite.Connection direttamente.
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class EtsySectionsService:
    """Servizio per etsy_sections, niche_section_map, uncategorized_niches."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def sync_sections(self, sections: list[dict]) -> None:
        """Upsert lista sezioni dall'API Etsy in etsy_sections.

        Ogni dict deve avere:
            shop_section_id (str | int)  — ID Etsy
            title (str)                  — nome sezione
            active_listing_count (int)   — optional, default 0

        Usa executemany per un singolo round-trip invece di N execute() separati.
        """
        if not sections:
            return
        params = [
            (
                str(s["shop_section_id"]),
                s["title"],
                _now_iso(),
                int(s.get("active_listing_count", 0)),
            )
            for s in sections
        ]
        await self._db.executemany(
            """
            INSERT INTO etsy_sections (section_id, section_name, created_at, listing_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(section_id) DO UPDATE SET
                section_name  = excluded.section_name,
                listing_count = excluded.listing_count
            """,
            params,
        )
        await self._db.commit()

    async def map_niche(
        self,
        niche_key: str,
        section_id: str,
        mapped_by: str = "human",
        auto_confidence: float | None = None,
    ) -> None:
        """Upsert mappatura niche_key → section_id in niche_section_map."""
        await self._db.execute(
            """
            INSERT INTO niche_section_map
                (niche_key, section_id, mapped_by, mapped_at, auto_confidence)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(niche_key) DO UPDATE SET
                section_id      = excluded.section_id,
                mapped_by       = excluded.mapped_by,
                mapped_at       = excluded.mapped_at,
                auto_confidence = excluded.auto_confidence
            """,
            (niche_key, section_id, mapped_by, _now_iso(), auto_confidence),
        )
        await self._db.commit()

    async def get_section_for_niche(self, niche_key: str) -> str | None:
        """Ritorna section_id per la niche, o None se non mappata."""
        cursor = await self._db.execute(
            "SELECT section_id FROM niche_section_map WHERE niche_key = ?",
            (niche_key,),
        )
        row = await cursor.fetchone()
        return row["section_id"] if row else None

    async def add_to_uncategorized(
        self,
        niche_key: str,
        listing_id: str | None = None,
        suggested_section_id: str | None = None,
        suggested_confidence: float | None = None,
    ) -> None:
        """Inserisce niche non mappata in uncategorized_niches con status='pending'.

        Non inserisce duplicati se già esiste una riga pending per la stessa niche.
        """
        existing = await self._db.execute(
            "SELECT id FROM uncategorized_niches WHERE niche_key = ? AND status = 'pending'",
            (niche_key,),
        )
        if await existing.fetchone():
            return
        await self._db.execute(
            """
            INSERT INTO uncategorized_niches
                (niche_key, detected_at, listing_id, suggested_section_id, suggested_confidence)
            VALUES (?, ?, ?, ?, ?)
            """,
            (niche_key, _now_iso(), listing_id, suggested_section_id, suggested_confidence),
        )
        await self._db.commit()
