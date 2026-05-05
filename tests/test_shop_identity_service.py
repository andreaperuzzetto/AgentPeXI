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


@pytest.mark.asyncio
async def test_set_active_raises_if_identity_not_found(svc):
    """set_active(nonexistent_id) must raise ValueError and leave current active state unchanged.

    With the non-atomic two-step implementation the deactivate-all would
    commit before the activate-one no-ops, silently destroying the active state.
    """
    id1 = await svc.create(**_SAMPLE)
    await svc.set_active(id1)  # id1 is now active

    with pytest.raises(ValueError, match="not found"):
        await svc.set_active(9999)

    # id1 must still be active — the deactivate-all was never committed
    active = await svc.get_active()
    assert active is not None, "Expected id1 to still be active after failed set_active"
    assert active.id == id1


# --------------------------------------------------------------------------
# M6: update() — patch parziale senza ricreare l'identity
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_changes_palette_and_tone(svc):
    """update() aggiorna solo i campi forniti senza toccare gli altri."""
    new_id = await svc.create(**_SAMPLE)
    updated = await svc.update(
        new_id,
        palette_primary="#FF0000",
        tone="bold_playful",
    )
    assert updated is not None
    assert updated.id == new_id
    assert updated.palette_primary == "#FF0000"
    assert updated.tone == "bold_playful"
    # campi non toccati restano invariati
    assert updated.palette_secondary == _SAMPLE["palette_secondary"]
    assert updated.palette_accent == _SAMPLE["palette_accent"]
    assert updated.mockup_style == _SAMPLE["mockup_style"]


@pytest.mark.asyncio
async def test_update_raises_if_identity_not_found(svc):
    """update() solleva ValueError se l'id non esiste."""
    with pytest.raises(ValueError, match="not found"):
        await svc.update(9999, tone="bold_playful")


@pytest.mark.asyncio
async def test_update_raises_if_no_fields_provided(svc):
    """update() solleva ValueError se non viene passato nessun campo."""
    new_id = await svc.create(**_SAMPLE)
    with pytest.raises(ValueError, match="no fields"):
        await svc.update(new_id)
