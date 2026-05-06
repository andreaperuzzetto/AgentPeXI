"""B-13 TDD: pinterest_costs_month() in FinanceTracker + pinterest_costs_eur in /api/finance/summary."""
from __future__ import annotations

import pytest
import aiosqlite
from unittest.mock import AsyncMock, MagicMock

from apps.backend.core.finance_tracker import FinanceTracker

_SCHEMA = """
CREATE TABLE IF NOT EXISTS revenue_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    etsy_listing_id TEXT    NOT NULL,
    order_id        TEXT    UNIQUE,
    gross_eur       REAL    NOT NULL,
    etsy_fee_eur    REAL    NOT NULL,
    net_eur         REAL    NOT NULL,
    design_cost_eur REAL    DEFAULT 0.0,
    listing_fee_eur REAL    DEFAULT 0.18,
    sold_at         REAL    NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS pinterest_queue (
    id              INTEGER  PRIMARY KEY AUTOINCREMENT,
    pin_variant     INTEGER  NOT NULL DEFAULT 1,
    image_path      TEXT     NOT NULL DEFAULT '',
    title           TEXT     NOT NULL DEFAULT '',
    description     TEXT     NOT NULL DEFAULT '',
    board_id        TEXT     NOT NULL DEFAULT 'board1',
    scheduled_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at    DATETIME,
    status          TEXT     DEFAULT 'pending',
    cost_image_gen  FLOAT    DEFAULT 0.0,
    cost_llm        FLOAT    DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at REAL
);
"""

_PIN_INSERT = (
    "INSERT INTO pinterest_queue "
    "(pin_variant, image_path, title, description, board_id, scheduled_at, published_at, status, cost_image_gen, cost_llm) "
    "VALUES (?, '', 'T', 'D', 'b1', ?, ?, ?, ?, ?)"
)


@pytest.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_SCHEMA)
        await conn.commit()
        yield conn


@pytest.fixture
async def tracker(db):
    memory = MagicMock()
    memory.get_db = AsyncMock(return_value=db)
    return FinanceTracker(memory=memory)


# ---------------------------------------------------------------------------
# pinterest_costs_month
# ---------------------------------------------------------------------------

async def test_pinterest_costs_month_zero_when_empty(tracker):
    """Returns 0.0 when pinterest_queue has no rows."""
    result = await tracker.pinterest_costs_month(year=2025, month=4)
    assert result == 0.0


async def test_pinterest_costs_month_sums_image_and_llm(tracker, db):
    """Sums cost_image_gen + cost_llm for all published pins in the requested month."""
    await db.execute(_PIN_INSERT, (1, "2025-04-10 00:00:00", "2025-04-10 12:00:00", "published", 0.05, 0.02))
    await db.execute(_PIN_INSERT, (2, "2025-04-15 00:00:00", "2025-04-15 12:00:00", "published", 0.03, 0.01))
    await db.commit()
    result = await tracker.pinterest_costs_month(year=2025, month=4)
    assert abs(result - 0.11) < 1e-6   # 0.05+0.02+0.03+0.01


async def test_pinterest_costs_month_excludes_other_months(tracker, db):
    """Excludes pins published in different months."""
    await db.execute(_PIN_INSERT, (1, "2025-03-01 00:00:00", "2025-03-01 12:00:00", "published", 1.00, 1.00))
    await db.commit()
    result = await tracker.pinterest_costs_month(year=2025, month=4)
    assert result == 0.0


async def test_pinterest_costs_month_excludes_unpublished(tracker, db):
    """Excludes pending/failed pins (status != 'published')."""
    await db.execute(_PIN_INSERT, (1, "2025-04-05 00:00:00", "2025-04-05 12:00:00", "pending", 5.00, 5.00))
    await db.commit()
    result = await tracker.pinterest_costs_month(year=2025, month=4)
    assert result == 0.0


async def test_pinterest_costs_month_december_does_not_overflow(tracker, db):
    """December pins are found correctly (no year-wrap bug)."""
    await db.execute(_PIN_INSERT, (1, "2024-12-15 00:00:00", "2024-12-15 10:00:00", "published", 0.10, 0.05))
    await db.commit()
    result = await tracker.pinterest_costs_month(year=2024, month=12)
    assert abs(result - 0.15) < 1e-6
