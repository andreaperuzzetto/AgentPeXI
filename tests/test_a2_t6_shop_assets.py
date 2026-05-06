# tests/test_a2_t6_shop_assets.py
"""Tests for generate_shop_assets() in generators_mixin."""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import aiosqlite


@pytest.fixture
async def db_with_active_identity():
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""
            CREATE TABLE shop_identity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aesthetic_name TEXT NOT NULL, palette_primary TEXT NOT NULL DEFAULT '',
                palette_secondary TEXT NOT NULL DEFAULT '', palette_accent TEXT NOT NULL DEFAULT '',
                mockup_style TEXT NOT NULL DEFAULT '', tone TEXT NOT NULL DEFAULT '',
                logo_path TEXT, banner_path TEXT, approved_at TEXT,
                approved_by TEXT NOT NULL DEFAULT 'andrea', is_active INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.commit()
        from apps.backend.core.shop_identity_service import ShopIdentityService
        svc = ShopIdentityService(db)
        identity_id = await svc.create(
            aesthetic_name="Test Brand",
            palette_primary="#C9A84C",
            palette_secondary="#F2D0C4",
            palette_accent="#FAF7F2",
            mockup_style="flat_lay",
            tone="warm",
        )
        await svc.set_active(identity_id)
        yield db, identity_id


@pytest.mark.asyncio
async def test_generate_shop_assets_updates_identity(db_with_active_identity, tmp_path):
    """generate_shop_assets should update logo_path and banner_path on the active identity."""
    db, identity_id = db_with_active_identity

    mock_image_gen = AsyncMock()
    mock_image_gen.generate_digital_art = AsyncMock(
        side_effect=lambda brief, path, mock_mode=False: _write_fake_png(path)
    )

    from apps.backend.agents._design.generators_mixin import _DesignGeneratorsMixin

    class _MockGenerators(_DesignGeneratorsMixin):
        def __init__(self):
            self._image_gen = mock_image_gen
            self._get_mock_mode = lambda: False
            self.storage = MagicMock()
            self.storage.base_path = tmp_path
            self._memory = AsyncMock()

    agent = _MockGenerators()
    await agent.generate_shop_assets(identity_id=identity_id, db=db, output_dir=tmp_path)

    from apps.backend.core.shop_identity_service import ShopIdentityService
    svc = ShopIdentityService(db)
    record = await svc.get_active()
    assert record is not None
    assert record.logo_path is not None
    assert record.banner_path is not None
    assert "logo" in record.logo_path
    assert "banner" in record.banner_path


def _write_fake_png(path) -> Path:
    """Helper: writes a minimal placeholder PNG and returns the path."""
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return p
