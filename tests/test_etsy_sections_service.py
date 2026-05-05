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
    suggested_confidence FLOAT
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
