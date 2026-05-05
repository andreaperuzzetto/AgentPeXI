"""ShopIdentityService — gestione identità di brand del negozio Etsy.

Segue il pattern ProductionQueueService: riceve aiosqlite.Connection
direttamente e non ha dipendenze sul layer MemoryManager.
"""
from __future__ import annotations

from dataclasses import dataclass

import aiosqlite


@dataclass
class ShopIdentityRecord:
    """Rappresentazione in-memory di un record shop_identity."""

    id: int
    aesthetic_name: str
    palette_primary: str
    palette_secondary: str
    palette_accent: str
    mockup_style: str
    tone: str
    logo_path: str | None
    banner_path: str | None
    approved_at: str | None
    approved_by: str
    is_active: bool


def _row_to_record(row: aiosqlite.Row) -> ShopIdentityRecord:
    return ShopIdentityRecord(
        id=row["id"],
        aesthetic_name=row["aesthetic_name"],
        palette_primary=row["palette_primary"],
        palette_secondary=row["palette_secondary"],
        palette_accent=row["palette_accent"],
        mockup_style=row["mockup_style"],
        tone=row["tone"],
        logo_path=row["logo_path"],
        banner_path=row["banner_path"],
        approved_at=row["approved_at"],
        approved_by=row["approved_by"],
        is_active=bool(row["is_active"]),
    )


class ShopIdentityService:
    """Servizio CRUD per shop_identity. Una sola identity può essere is_active=1."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def get_active(self) -> ShopIdentityRecord | None:
        """Ritorna l'identity attiva, o None se nessuna è attiva."""
        cursor = await self._db.execute(
            "SELECT * FROM shop_identity WHERE is_active = 1 LIMIT 1"
        )
        row = await cursor.fetchone()
        return _row_to_record(row) if row else None

    async def set_active(self, identity_id: int) -> None:
        """Imposta `identity_id` come unica identity attiva (disattiva le altre).

        Usa un singolo UPDATE atomico per evitare stati intermedi in cui nessuna
        identity è attiva. Raises ValueError se identity_id non esiste.
        """
        row = await (
            await self._db.execute(
                "SELECT 1 FROM shop_identity WHERE id = ?", (identity_id,)
            )
        ).fetchone()
        if row is None:
            raise ValueError(f"ShopIdentity {identity_id} not found")
        # Single atomic statement: sets is_active=1 for the target, 0 for all others.
        # SQLite boolean expression (id = ?) evaluates to 1 or 0.
        await self._db.execute(
            "UPDATE shop_identity SET is_active = (id = ?)", (identity_id,)
        )
        await self._db.commit()

    async def list_options(self) -> list[ShopIdentityRecord]:
        """Ritorna tutte le identity (attive e non) ordinate per id."""
        cursor = await self._db.execute(
            "SELECT * FROM shop_identity ORDER BY id"
        )
        rows = await cursor.fetchall()
        return [_row_to_record(r) for r in rows]

    async def create(
        self,
        *,
        aesthetic_name: str,
        palette_primary: str,
        palette_secondary: str,
        palette_accent: str,
        mockup_style: str,
        tone: str,
        logo_path: str | None = None,
        banner_path: str | None = None,
        approved_by: str = "andrea",
    ) -> int:
        """Inserisce una nuova identity (non attiva) e ritorna il suo id."""
        cursor = await self._db.execute(
            """
            INSERT INTO shop_identity
                (aesthetic_name, palette_primary, palette_secondary, palette_accent,
                 mockup_style, tone, logo_path, banner_path, approved_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (aesthetic_name, palette_primary, palette_secondary, palette_accent,
             mockup_style, tone, logo_path, banner_path, approved_by),
        )
        await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    _UPDATABLE_FIELDS = frozenset({
        "aesthetic_name", "palette_primary", "palette_secondary", "palette_accent",
        "mockup_style", "tone", "logo_path", "banner_path",
    })

    async def update(self, identity_id: int, **fields: object) -> ShopIdentityRecord:
        """Aggiorna parzialmente un'identity esistente e ritorna il record aggiornato.

        Raises ValueError se identity_id non esiste o se non viene passato nessun campo.
        Solo i campi in _UPDATABLE_FIELDS sono modificabili.
        """
        if not fields:
            raise ValueError("update() called with no fields to set")
        unknown = set(fields) - self._UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"unknown fields: {unknown}")
        row = await (
            await self._db.execute(
                "SELECT 1 FROM shop_identity WHERE id = ?", (identity_id,)
            )
        ).fetchone()
        if row is None:
            raise ValueError(f"ShopIdentity {identity_id} not found")
        set_clause = ", ".join(f"{col} = ?" for col in fields)
        await self._db.execute(
            f"UPDATE shop_identity SET {set_clause} WHERE id = ?",  # noqa: S608
            (*fields.values(), identity_id),
        )
        await self._db.commit()
        updated_row = await (
            await self._db.execute("SELECT * FROM shop_identity WHERE id = ?", (identity_id,))
        ).fetchone()
        return _row_to_record(updated_row)  # type: ignore[arg-type]
