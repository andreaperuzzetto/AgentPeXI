"""B-12: BudgetGauges 4th Pinterest gauge — backend pinterest_cost_today tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock, MagicMock

import aiosqlite
import pytest

# ---------------------------------------------------------------------------
# Minimal schema — only tables needed by get_cost_breakdown
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name  TEXT,
    status      TEXT DEFAULT 'completed',
    total_cost_usd FLOAT DEFAULT 0.0,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT,
    cost_usd  FLOAT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    model            TEXT DEFAULT 'claude-haiku',
    cost_usd         FLOAT DEFAULT 0.0,
    input_tokens     INTEGER DEFAULT 0,
    output_tokens    INTEGER DEFAULT 0,
    cache_read_tokens  INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    timestamp        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS production_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    image_cost_usd FLOAT DEFAULT 0.0,
    listing_fee_usd FLOAT DEFAULT 0.0,
    status         TEXT DEFAULT 'published',
    published_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pinterest_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    cost_image_gen FLOAT DEFAULT 0.0,
    cost_llm       FLOAT DEFAULT 0.0,
    status         TEXT DEFAULT 'published',
    published_at   DATETIME
);
"""

# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

async def _make_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA)
    await db.commit()
    return db


def _make_mixin(db: aiosqlite.Connection):
    """Instantiate the AnalyticsMixin in isolation via the assembler."""
    from apps.backend.core._memory._analytics import AnalyticsMixin

    class Stub(AnalyticsMixin):
        def __init__(self, db):
            self._db = db

    return Stub(db)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cost_breakdown_includes_pinterest_cost_today_key():
    """get_cost_breakdown deve contenere la chiave pinterest_cost_today."""
    db = await _make_db()
    mixin = _make_mixin(db)

    result = await mixin.get_cost_breakdown(period_days=7)
    await db.close()

    assert "pinterest_cost_today" in result


@pytest.mark.asyncio
async def test_cost_breakdown_pinterest_cost_today_zero_when_empty():
    """pinterest_cost_today deve essere 0.0 se pinterest_queue è vuota."""
    db = await _make_db()
    mixin = _make_mixin(db)

    result = await mixin.get_cost_breakdown(period_days=7)
    await db.close()

    assert result["pinterest_cost_today"] == 0.0


@pytest.mark.asyncio
async def test_cost_breakdown_pinterest_cost_today_sums_today_pins():
    """pinterest_cost_today deve sommare cost_image_gen dei pin pubblicati oggi."""
    db = await _make_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "INSERT INTO pinterest_queue (cost_image_gen, cost_llm, status, published_at)"
        " VALUES (?,?,?,?)",
        (0.01, 0.001, "published", today),
    )
    await db.execute(
        "INSERT INTO pinterest_queue (cost_image_gen, cost_llm, status, published_at)"
        " VALUES (?,?,?,?)",
        (0.02, 0.002, "published", today),
    )
    await db.commit()

    mixin = _make_mixin(db)
    result = await mixin.get_cost_breakdown(period_days=7)
    await db.close()

    # sum of cost_image_gen only (spec: "Traccia cost_image_gen totale")
    assert abs(result["pinterest_cost_today"] - 0.03) < 1e-9


@pytest.mark.asyncio
async def test_cost_breakdown_pinterest_cost_today_excludes_other_days():
    """pinterest_cost_today deve escludere pin pubblicati in giorni precedenti."""
    db = await _make_db()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "INSERT INTO pinterest_queue (cost_image_gen, status, published_at)"
        " VALUES (?,?,?)",
        (0.05, "published", yesterday),  # should be excluded
    )
    await db.execute(
        "INSERT INTO pinterest_queue (cost_image_gen, status, published_at)"
        " VALUES (?,?,?)",
        (0.01, "published", today),  # included
    )
    await db.commit()

    mixin = _make_mixin(db)
    result = await mixin.get_cost_breakdown(period_days=7)
    await db.close()

    assert abs(result["pinterest_cost_today"] - 0.01) < 1e-9


@pytest.mark.asyncio
async def test_cost_breakdown_pinterest_cost_today_excludes_unpublished():
    """pinterest_cost_today deve escludere pin con status != published."""
    db = await _make_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "INSERT INTO pinterest_queue (cost_image_gen, status, published_at)"
        " VALUES (?,?,?)",
        (0.05, "pending", today),  # should be excluded
    )
    await db.commit()

    mixin = _make_mixin(db)
    result = await mixin.get_cost_breakdown(period_days=7)
    await db.close()

    assert result["pinterest_cost_today"] == 0.0
