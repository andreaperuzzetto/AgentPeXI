"""Tests per EtsySectionsService (PA-6)."""
from __future__ import annotations

import pytest
import aiosqlite

from apps.backend.core.etsy_sections_service import EtsySectionsService


_DDL = """
CREATE TABLE IF NOT EXISTS etsy_sections (
    section_id       TEXT    PRIMARY KEY,
    section_name     TEXT    NOT NULL,
    created_at       DATETIME,
    listing_count    INTEGER DEFAULT 0,
    last_listing_at  DATETIME,
    is_active        BOOLEAN DEFAULT 1
);
CREATE TABLE IF NOT EXISTS niche_section_map (
    niche_key        TEXT    PRIMARY KEY,
    section_id       TEXT    REFERENCES etsy_sections(section_id),
    mapped_by        TEXT,
    mapped_at        DATETIME,
    auto_confidence  FLOAT
);
CREATE TABLE IF NOT EXISTS uncategorized_niches (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    niche_key            TEXT    NOT NULL,
    detected_at          DATETIME,
    listing_id           TEXT,
    status               TEXT    DEFAULT 'pending',
    suggested_section_id TEXT,
    suggested_confidence FLOAT,
    UNIQUE (niche_key, status)
);
"""

_SECTIONS = [
    {"shop_section_id": "s1", "title": "Party & Celebrations", "active_listing_count": 10},
    {"shop_section_id": "s2", "title": "Wedding", "active_listing_count": 3},
]


@pytest.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_DDL)
        await conn.commit()
        yield conn


@pytest.fixture
async def svc(db):
    return EtsySectionsService(db)


@pytest.mark.asyncio
async def test_sync_sections_inserts(svc, db):
    """sync_sections() inserisce i record in etsy_sections."""
    await svc.sync_sections(_SECTIONS)
    cursor = await db.execute("SELECT COUNT(*) AS n FROM etsy_sections")
    row = await cursor.fetchone()
    assert row["n"] == 2


@pytest.mark.asyncio
async def test_sync_sections_upserts(svc, db):
    """sync_sections() con stessa section_id aggiorna il nome senza duplicare."""
    await svc.sync_sections(_SECTIONS)
    updated = [{"shop_section_id": "s1", "title": "Party (updated)", "active_listing_count": 15}]
    await svc.sync_sections(updated)
    cursor = await db.execute("SELECT section_name, listing_count FROM etsy_sections WHERE section_id = 's1'")
    row = await cursor.fetchone()
    assert row["section_name"] == "Party (updated)"
    assert row["listing_count"] == 15
    cursor2 = await db.execute("SELECT COUNT(*) AS n FROM etsy_sections")
    row2 = await cursor2.fetchone()
    assert row2["n"] == 2


@pytest.mark.asyncio
async def test_map_niche_inserts(svc, db):
    """map_niche() inserisce in niche_section_map."""
    await svc.sync_sections(_SECTIONS)
    await svc.map_niche("wedding_planner", "s2")
    result = await svc.get_section_for_niche("wedding_planner")
    assert result == "s2"


@pytest.mark.asyncio
async def test_get_section_for_niche_none_when_missing(svc):
    """get_section_for_niche() ritorna None se la niche non è mappata."""
    result = await svc.get_section_for_niche("nonexistent_niche")
    assert result is None


@pytest.mark.asyncio
async def test_map_niche_upserts(svc, db):
    """map_niche() aggiorna section_id se niche già presente."""
    await svc.sync_sections(_SECTIONS)
    await svc.map_niche("wedding_planner", "s1")
    await svc.map_niche("wedding_planner", "s2")
    result = await svc.get_section_for_niche("wedding_planner")
    assert result == "s2"


@pytest.mark.asyncio
async def test_add_to_uncategorized_inserts(svc, db):
    """add_to_uncategorized() inserisce la niche in uncategorized_niches."""
    await svc.add_to_uncategorized("kids_party", listing_id="L123")
    cursor = await db.execute("SELECT * FROM uncategorized_niches WHERE niche_key = 'kids_party'")
    row = await cursor.fetchone()
    assert row is not None
    assert row["status"] == "pending"
    assert row["listing_id"] == "L123"


@pytest.mark.asyncio
async def test_add_to_uncategorized_no_duplicate_pending(svc, db):
    """add_to_uncategorized() non inserisce duplicati pending per la stessa niche."""
    await svc.add_to_uncategorized("kids_party")
    await svc.add_to_uncategorized("kids_party")
    cursor = await db.execute("SELECT COUNT(*) AS n FROM uncategorized_niches WHERE niche_key = 'kids_party'")
    row = await cursor.fetchone()
    assert row["n"] == 1


@pytest.mark.asyncio
async def test_add_to_uncategorized_with_suggestion(svc, db):
    """add_to_uncategorized() salva suggested_section_id e suggested_confidence."""
    await svc.add_to_uncategorized(
        "summer_wedding",
        suggested_section_id="s2",
        suggested_confidence=0.87,
    )
    cursor = await db.execute("SELECT * FROM uncategorized_niches WHERE niche_key = 'summer_wedding'")
    row = await cursor.fetchone()
    assert row["suggested_section_id"] == "s2"
    assert abs(row["suggested_confidence"] - 0.87) < 0.001


@pytest.mark.asyncio
async def test_map_niche_mapped_by_auto(svc, db):
    """map_niche() salva mapped_by='auto' e auto_confidence."""
    await svc.sync_sections(_SECTIONS)
    await svc.map_niche("autumn_wedding", "s2", mapped_by="auto", auto_confidence=0.92)
    cursor = await db.execute(
        "SELECT mapped_by, auto_confidence FROM niche_section_map WHERE niche_key = 'autumn_wedding'"
    )
    row = await cursor.fetchone()
    assert row["mapped_by"] == "auto"
    assert abs(row["auto_confidence"] - 0.92) < 0.001


@pytest.mark.asyncio
async def test_sync_sections_batch_inserts_all(svc, db):
    """sync_sections() deve inserire tutti gli N elementi del batch in un'unica operazione.

    Verifica che il contratto funzioni correttamente con un batch arbitrariamente grande,
    non solo con i 2 elementi dei test base.
    """
    batch = [
        {"shop_section_id": f"sec{i}", "title": f"Section {i}", "active_listing_count": i * 5}
        for i in range(1, 11)  # 10 sezioni
    ]
    await svc.sync_sections(batch)
    cursor = await db.execute("SELECT COUNT(*) AS n FROM etsy_sections")
    row = await cursor.fetchone()
    assert row["n"] == 10
    # Verifica che titoli e conteggi siano corretti
    cursor2 = await db.execute(
        "SELECT section_name, listing_count FROM etsy_sections WHERE section_id = 'sec5'"
    )
    row2 = await cursor2.fetchone()
    assert row2["section_name"] == "Section 5"
    assert row2["listing_count"] == 25


@pytest.mark.asyncio
async def test_sync_sections_empty_list_is_noop(svc, db):
    """sync_sections([]) non deve inserire nulla né sollevare eccezioni."""
    await svc.sync_sections([])
    cursor = await db.execute("SELECT COUNT(*) AS n FROM etsy_sections")
    row = await cursor.fetchone()
    assert row["n"] == 0


@pytest.mark.asyncio
async def test_add_to_uncategorized_concurrent_no_duplicate(svc, db):
    """Chiamate concorrenti su add_to_uncategorized con la stessa niche_key
    non devono creare righe duplicate (race condition check-then-insert).

    Richiede UNIQUE(niche_key, status) + INSERT OR IGNORE per essere atomico.
    """
    import asyncio

    await asyncio.gather(
        svc.add_to_uncategorized("kids_party"),
        svc.add_to_uncategorized("kids_party"),
    )
    cursor = await db.execute(
        "SELECT COUNT(*) AS n FROM uncategorized_niches"
        " WHERE niche_key = 'kids_party' AND status = 'pending'"
    )
    row = await cursor.fetchone()
    assert row["n"] == 1, (
        f"Expected 1 pending row but got {row['n']} — "
        "concurrent check-then-insert race condition not fixed"
    )


@pytest.mark.asyncio
async def test_get_sections_with_uncategorized_counts_empty(svc, db):
    """Nessuna sezione → lista vuota, no error."""
    result = await svc.get_sections_with_uncategorized_counts()
    assert result == []


@pytest.mark.asyncio
async def test_get_sections_with_uncategorized_counts_no_pending(svc, db):
    """Sezioni esistenti, nessuna pending uncategorized → pending_uncategorized=0."""
    await svc.sync_sections(_SECTIONS)
    result = await svc.get_sections_with_uncategorized_counts()
    assert len(result) == 2
    assert all(r["pending_uncategorized"] == 0 for r in result)
    assert "section_id" in result[0]
    assert "section_name" in result[0]
    assert "listing_count" in result[0]
    assert "last_listing_at" in result[0]


@pytest.mark.asyncio
async def test_get_sections_with_uncategorized_counts_with_pending(svc, db):
    """Con pending uncategorized, il conteggio globale compare su tutte le sezioni."""
    await svc.sync_sections(_SECTIONS)
    await svc.add_to_uncategorized("mystery_niche")
    result = await svc.get_sections_with_uncategorized_counts()
    assert all(r["pending_uncategorized"] == 1 for r in result)


@pytest.mark.asyncio
async def test_suggest_section_for_niche_no_match(svc, db):
    """Niche senza overlap con sezioni → (None, None)."""
    await svc.sync_sections(_SECTIONS)
    section_id, conf = await svc.suggest_section_for_niche("xyz_abstract_concept")
    assert section_id is None
    assert conf is None


@pytest.mark.asyncio
async def test_suggest_section_for_niche_match(svc, db):
    """'party_planner_printable' deve matchare 'Party & Celebrations' con confidence > 0."""
    await svc.sync_sections(_SECTIONS)
    section_id, conf = await svc.suggest_section_for_niche("party_planner_printable")
    assert section_id == "s1", f"Expected s1 (Party), got {section_id}"
    assert conf is not None and conf > 0.0


@pytest.mark.asyncio
async def test_suggest_section_returns_best_match(svc, db):
    """'wedding_invitation_printable' matcha 'Wedding' meglio di 'Party & Celebrations'."""
    await svc.sync_sections(_SECTIONS)
    section_id, conf = await svc.suggest_section_for_niche("wedding_invitation_printable")
    assert section_id == "s2", f"Expected s2 (Wedding), got {section_id}"
    assert conf is not None and conf >= 0.3


@pytest.mark.asyncio
async def test_update_section_listing_count(svc, db):
    """update_section_listing_count() incrementa listing_count e imposta last_listing_at."""
    await svc.sync_sections(_SECTIONS)
    # section s1 parte da listing_count=10
    await svc.update_section_listing_count("s1", "listing-abc")
    cursor = await db.execute(
        "SELECT listing_count, last_listing_at FROM etsy_sections WHERE section_id = 's1'"
    )
    row = await cursor.fetchone()
    assert row["listing_count"] == 11
    assert row["last_listing_at"] is not None


@pytest.mark.asyncio
async def test_get_stale_sections_empty_when_fresh(svc, db):
    """Sezioni appena aggiornate non risultano stale."""
    await svc.sync_sections(_SECTIONS)
    await db.execute("UPDATE etsy_sections SET last_listing_at = datetime('now')")
    await db.commit()
    stale = await svc.get_stale_sections(min_days_inactive=60)
    assert stale == []


@pytest.mark.asyncio
async def test_get_stale_sections_returns_old(svc, db):
    """Sezioni senza last_listing_at (NULL) risultano stale."""
    await svc.sync_sections(_SECTIONS)
    # last_listing_at è NULL di default dopo sync_sections
    stale = await svc.get_stale_sections(min_days_inactive=60)
    assert len(stale) == 2  # entrambe NULL = mai aggiornate = stale
    assert all("section_name" in s for s in stale)
