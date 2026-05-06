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

        Non inserisce duplicati: la UNIQUE(niche_key, status) + INSERT OR IGNORE
        garantisce atomicità eliminando la race condition check-then-insert.
        """
        await self._db.execute(
            """
            INSERT OR IGNORE INTO uncategorized_niches
                (niche_key, detected_at, listing_id, suggested_section_id, suggested_confidence)
            VALUES (?, ?, ?, ?, ?)
            """,
            (niche_key, _now_iso(), listing_id, suggested_section_id, suggested_confidence),
        )
        await self._db.commit()

    async def get_sections_with_uncategorized_counts(self) -> list[dict]:
        """Returns all active sections with global count of pending uncategorized niches.

        pending_uncategorized is a global count (not per-section):
        how many niches are not yet mapped to any section.
        """
        cursor = await self._db.execute(
            """
            SELECT
                es.section_id,
                es.section_name,
                es.listing_count,
                es.last_listing_at,
                (SELECT COUNT(*) FROM uncategorized_niches WHERE status = 'pending') AS pending_uncategorized
            FROM etsy_sections es
            WHERE es.is_active = 1
            ORDER BY es.listing_count DESC, es.section_name ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def suggest_section_for_niche(
        self,
        niche_key: str,
        min_confidence: float = 0.3,
    ) -> tuple[str | None, float | None]:
        """Fuzzy-match niche_key against active Etsy section names.

        Algorithm: word overlap recall against section name words.
        Example: 'wedding_invitation_printable' → words={'wedding','invitation','printable'}
                 'Wedding' → words={'wedding'} → overlap=1/1=1.0

        Returns (section_id, confidence) if confidence >= min_confidence, else (None, None).
        """
        cursor = await self._db.execute(
            "SELECT section_id, section_name FROM etsy_sections WHERE is_active = 1"
        )
        sections = await cursor.fetchall()
        if not sections:
            return None, None

        niche_words = set(niche_key.replace("_", " ").lower().split())
        best_id: str | None = None
        best_conf: float = 0.0

        for row in sections:
            # Normalize: "Party & Celebrations" → {"party", "celebrations"}
            section_words = {
                w.strip().lower()
                for w in row["section_name"].replace("&", " ").split()
                if len(w.strip()) > 2
            }
            if not section_words:
                continue
            intersection = niche_words & section_words
            # Recall against section words (more stable than niche words)
            conf = len(intersection) / len(section_words)
            if conf > best_conf:
                best_conf = conf
                best_id = str(row["section_id"])

        if best_conf >= min_confidence:
            return best_id, round(best_conf, 3)
        return None, None

    async def update_section_listing_count(
        self,
        section_id: str,
        listing_id: str,  # noqa: ARG002 — reserved for future FK
    ) -> None:
        """Increment listing_count and update last_listing_at for a section.

        Called by _publish_mixin after a successful publish with an assigned section_id.
        """
        await self._db.execute(
            """
            UPDATE etsy_sections
            SET listing_count   = listing_count + 1,
                last_listing_at = ?
            WHERE section_id = ?
            """,
            (_now_iso(), section_id),
        )
        await self._db.commit()

    async def get_stale_sections(self, min_days_inactive: int = 60) -> list[dict]:
        """Returns active sections with no listing for more than min_days_inactive days.

        Sections with last_listing_at IS NULL (never updated) are considered stale.
        """
        cursor = await self._db.execute(
            """
            SELECT section_id, section_name, last_listing_at, listing_count
            FROM etsy_sections
            WHERE is_active = 1
              AND (
                  last_listing_at IS NULL
                  OR datetime(last_listing_at) < datetime('now', ? || ' days')
              )
            ORDER BY last_listing_at ASC
            """,
            (f"-{min_days_inactive}",),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
