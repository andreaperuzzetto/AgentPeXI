"""conftest.py — pre-imports real modules before test_block1_integration.py
stubs them via sys.modules.setdefault().

test_block1_integration.py uses sys.modules.setdefault() for package-level
modules (so pre-importing them here prevents stubbing) and explicitly replaces
apps.backend.core.models (handled separately in test_core_models.py via
importlib loading from disk).
"""
from __future__ import annotations

import pytest
import aiosqlite
from unittest.mock import AsyncMock, MagicMock

# Pre-import these so block1's setdefault() is a no-op for them:
import apps.backend.tools.file_gen  # noqa: F401 — ensures apps.backend.tools is a real package
import apps.backend.core.budget_manager  # noqa: F401 — imports real aiosqlite too
import apps.backend.agents._design.colors  # noqa: F401
import apps.backend.agents._design.utils  # noqa: F401
import apps.backend.agents._design.scoring  # noqa: F401
import apps.backend.core._pepe._confidence  # noqa: F401
import apps.backend.core._pepe._llm  # noqa: F401
import apps.backend.agents.base  # noqa: F401
import apps.backend.core._pepe._pipeline  # noqa: F401
import apps.backend.api.state as state_mod


@pytest.fixture
async def seeded_shop_identity_db():
    """Fixture that seeds shop_identity table with 3 records, record 1 is active."""
    # Create in-memory DB
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row  # Enable dict-like access
    
    # Create shop_identity table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS shop_identity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aesthetic_name TEXT NOT NULL,
            palette_primary TEXT NOT NULL,
            palette_secondary TEXT NOT NULL,
            palette_accent TEXT NOT NULL,
            mockup_style TEXT NOT NULL,
            tone TEXT NOT NULL,
            logo_path TEXT,
            banner_path TEXT,
            approved_at REAL NOT NULL,
            approved_by TEXT NOT NULL,
            is_active INTEGER DEFAULT 0
        )
    """)
    await db.commit()
    
    # Insert 3 test records
    await db.executemany(
        """
        INSERT INTO shop_identity 
        (aesthetic_name, palette_primary, palette_secondary, palette_accent, mockup_style, tone, logo_path, banner_path, approved_at, approved_by, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("Option A", "#FF0000", "#00FF00", "#0000FF", "flat", "professional", None, None, 1234567890.0, "andrea", 1),
            ("Option B", "#AA0000", "#00AA00", "#0000AA", "realistic", "casual", None, None, 1234567891.0, "andrea", 0),
            ("Option C", "#550000", "#005500", "#000055", "minimalist", "friendly", None, None, 1234567892.0, "andrea", 0),
        ]
    )
    await db.commit()
    
    # Mock state.memory to return this DB
    mock_memory = MagicMock()
    mock_memory.get_db = AsyncMock(return_value=db)
    original_memory = state_mod.memory
    state_mod.memory = mock_memory
    
    yield db
    
    # Cleanup
    state_mod.memory = original_memory
    await db.close()
