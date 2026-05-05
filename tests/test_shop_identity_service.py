"""Tests per ShopIdentityService (PA-5)."""
from __future__ import annotations

import pytest
import aiosqlite

from apps.backend.core.shop_identity_service import ShopIdentityService, ShopIdentityRecord


# --------------------------------------------------------------------------
# Fixture: DB in-memory con tabella shop_identity
# --------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS shop_identity (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    aesthetic_name    TEXT    NOT NULL,
    palette_primary   TEXT    NOT NULL,
    palette_secondary TEXT    NOT NULL,
    palette_accent    TEXT    NOT NULL,
    mockup_style      TEXT    NOT NULL,
    tone              TEXT    NOT NULL,
    logo_path         TEXT,
    banner_path       TEXT,
    approved_at       DATETIME,
    approved_by       TEXT    DEFAULT 'andrea',
    is_active         BOOLEAN DEFAULT 0
);
"""

_SAMPLE: dict = {
    "aesthetic_name": "Minimalist Forest",
    "palette_primary": "#1A1A1A",
    "palette_secondary": "#FFFFFF",
    "palette_accent": "#4CAF50",
    "mockup_style": "flat_lay",
    "tone": "calm_professional",
}


@pytest.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_DDL)
        await conn.commit()
        yield conn


@pytest.fixture
async def svc(db):
    return ShopIdentityService(db)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_active_returns_none_when_empty(svc):
    """get_active() ritorna None quando non ci sono identity."""
    result = await svc.get_active()
    assert result is None


@pytest.mark.asyncio
async def test_create_inserts_record(svc):
    """create() inserisce un record e ritorna un id intero positivo."""
    new_id = await svc.create(**_SAMPLE)
    assert isinstance(new_id, int)
    assert new_id > 0


@pytest.mark.asyncio
async def test_get_active_returns_none_before_set_active(svc):
    """Il record appena creato non è attivo di default."""
    await svc.create(**_SAMPLE)
    result = await svc.get_active()
    assert result is None


@pytest.mark.asyncio
async def test_set_active_activates_record(svc):
    """set_active(id) rende is_active=True per il record indicato."""
    new_id = await svc.create(**_SAMPLE)
    await svc.set_active(new_id)
    result = await svc.get_active()
    assert result is not None
    assert result.id == new_id
    assert result.is_active is True
    assert result.aesthetic_name == "Minimalist Forest"


@pytest.mark.asyncio
async def test_set_active_deactivates_previous(svc):
    """set_active(id2) disattiva l'identity precedentemente attiva."""
    id1 = await svc.create(**_SAMPLE)
    id2 = await svc.create(**{**_SAMPLE, "aesthetic_name": "Boho Spring"})
    await svc.set_active(id1)
    await svc.set_active(id2)
    active = await svc.get_active()
    assert active is not None
    assert active.id == id2


@pytest.mark.asyncio
async def test_list_options_returns_all(svc):
    """list_options() ritorna tutte le identity."""
    id1 = await svc.create(**_SAMPLE)
    id2 = await svc.create(**{**_SAMPLE, "aesthetic_name": "Boho Spring"})
    await svc.set_active(id1)
    options = await svc.list_options()
    assert len(options) == 2
    ids = {o.id for o in options}
    assert id1 in ids and id2 in ids


@pytest.mark.asyncio
async def test_list_options_empty(svc):
    """list_options() ritorna lista vuota se non ci sono identity."""
    options = await svc.list_options()
    assert options == []


@pytest.mark.asyncio
async def test_record_fields(svc):
    """ShopIdentityRecord contiene tutti i campi attesi."""
    new_id = await svc.create(**_SAMPLE)
    await svc.set_active(new_id)
    rec = await svc.get_active()
    assert rec is not None
    assert isinstance(rec, ShopIdentityRecord)
    assert rec.palette_primary == "#1A1A1A"
    assert rec.palette_secondary == "#FFFFFF"
    assert rec.palette_accent == "#4CAF50"
    assert rec.mockup_style == "flat_lay"
    assert rec.tone == "calm_professional"
    assert rec.logo_path is None
    assert rec.approved_by == "andrea"


@pytest.mark.asyncio
async def test_only_one_active_at_a_time(svc):
    """Non possono coesistere due identity attive."""
    id1 = await svc.create(**_SAMPLE)
    id2 = await svc.create(**{**_SAMPLE, "aesthetic_name": "Boho Spring"})
    id3 = await svc.create(**{**_SAMPLE, "aesthetic_name": "Corporate Blue"})
    await svc.set_active(id1)
    await svc.set_active(id2)
    await svc.set_active(id3)
    options = await svc.list_options()
    active_count = sum(1 for o in options if o.is_active)
    assert active_count == 1
    active = await svc.get_active()
    assert active is not None
    assert active.id == id3
