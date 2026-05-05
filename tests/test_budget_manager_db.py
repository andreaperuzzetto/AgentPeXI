"""Tests for BudgetManager with real aiosqlite in-memory SQLite."""
from __future__ import annotations

import time

import aiosqlite
import pytest

from apps.backend.core.budget_manager import BudgetManager, BudgetStatus, BudgetSummary


@pytest.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        await conn.execute("""
            CREATE TABLE config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at REAL
            )
        """)
        await conn.execute("""
            CREATE TABLE production_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                llm_cost_usd REAL DEFAULT 0.0,
                image_cost_usd REAL DEFAULT 0.0,
                listing_fee_usd REAL DEFAULT 0.0,
                status TEXT DEFAULT 'pending',
                published_at REAL,
                created_at REAL,
                updated_at REAL
            )
        """)
        await conn.commit()
        yield conn


@pytest.fixture
async def budget(db):
    mgr = BudgetManager(db)
    await mgr.ensure_defaults()
    return mgr


# ---------------------------------------------------------------------------
# ensure_defaults
# ---------------------------------------------------------------------------

async def test_ensure_defaults_creates_config_rows(db):
    mgr = BudgetManager(db)
    await mgr.ensure_defaults()
    cursor = await db.execute("SELECT COUNT(*) FROM config WHERE key LIKE 'budget.%'")
    row = await cursor.fetchone()
    assert row[0] >= 4  # 4 default keys


async def test_ensure_defaults_idempotent(db):
    mgr = BudgetManager(db)
    await mgr.ensure_defaults()
    await mgr.ensure_defaults()  # second call must not raise or duplicate
    cursor = await db.execute("SELECT COUNT(*) FROM config WHERE key='budget.daily_llm_usd'")
    row = await cursor.fetchone()
    assert row[0] == 1


# ---------------------------------------------------------------------------
# today_llm_cost / today_image_cost after record_costs
# ---------------------------------------------------------------------------

async def test_record_costs_llm_cost_reflected(db, budget):
    # Insert a row with created_at = now
    now = time.time()
    cursor = await db.execute(
        "INSERT INTO production_queue (created_at, updated_at) VALUES (?, ?)",
        (now, now),
    )
    await db.commit()
    item_id = cursor.lastrowid

    await budget.record_costs(item_id, llm=0.05, image=0.0)
    cost = await budget.today_llm_cost()
    assert cost >= 0.05


async def test_record_costs_image_cost_reflected(db, budget):
    now = time.time()
    cursor = await db.execute(
        "INSERT INTO production_queue (created_at, updated_at) VALUES (?, ?)",
        (now, now),
    )
    await db.commit()
    item_id = cursor.lastrowid

    await budget.record_costs(item_id, llm=0.0, image=0.01)
    cost = await budget.today_image_cost()
    assert cost >= 0.01


# ---------------------------------------------------------------------------
# check_budget
# ---------------------------------------------------------------------------

async def test_check_budget_returns_ok_when_low(budget):
    status = await budget.check_budget()
    assert status == BudgetStatus.OK


async def test_check_budget_returns_exceeded_after_large_cost(db, budget):
    # Insert a row with today's timestamp and a very large llm cost
    now = time.time()
    cursor = await db.execute(
        "INSERT INTO production_queue (llm_cost_usd, created_at, updated_at) VALUES (?, ?, ?)",
        (999.0, now, now),
    )
    await db.commit()

    status = await budget.check_budget()
    assert status == BudgetStatus.EXCEEDED


async def test_check_budget_returns_warning_near_threshold(db, budget):
    # Set a very low limit so 0.40 / 0.50 = 80% → WARNING
    await budget.set_limit("daily_llm_usd", 0.50)
    now = time.time()
    cursor = await db.execute(
        "INSERT INTO production_queue (llm_cost_usd, created_at, updated_at) VALUES (?, ?, ?)",
        (0.40, now, now),
    )
    await db.commit()

    status = await budget.check_budget()
    assert status in (BudgetStatus.WARNING, BudgetStatus.EXCEEDED)


# ---------------------------------------------------------------------------
# get_status_summary
# ---------------------------------------------------------------------------

async def test_get_status_summary_returns_budget_summary(budget):
    summary = await budget.get_status_summary()
    assert isinstance(summary, BudgetSummary)
    assert summary.llm_limit > 0
    assert summary.status in BudgetStatus.__members__.values()


# ---------------------------------------------------------------------------
# set_limit / get_limits round-trip
# ---------------------------------------------------------------------------

async def test_set_limit_get_limits_roundtrip(budget):
    await budget.set_limit("daily_llm_usd", 2.50)
    limits = await budget.get_limits()
    assert limits["daily_llm_usd"] == pytest.approx(2.50)


async def test_set_limit_with_full_key_prefix(budget):
    await budget.set_limit("budget.daily_image_usd", 3.00)
    limits = await budget.get_limits()
    assert limits["daily_image_usd"] == pytest.approx(3.00)


async def test_get_limits_returns_all_keys(budget):
    limits = await budget.get_limits()
    assert "daily_llm_usd" in limits
    assert "daily_image_usd" in limits
    assert "daily_listing_fee_usd" in limits
    assert "warn_threshold" in limits
