"""Tests for finance_tracker pure functions and FinanceTracker DB methods."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from apps.backend.core.finance_tracker import (
    calculate_net,
    break_even_price,
    FinanceTracker,
    ETSY_TRANSACTION_PCT,
    ETSY_PAYMENT_PCT,
    EUR_USD_RATE,
    GOAL_EUR_DEFAULT,
)

# ---------------------------------------------------------------------------
# Pure module-level functions
# ---------------------------------------------------------------------------

def test_calculate_net_basic():
    result = calculate_net(gross_eur=10.0, design_cost_usd=0.0, listing_fee_usd=0.0)
    assert result["gross_eur"] == 10.0
    assert 0 < result["net_eur"] < 10.0
    assert result["margin_pct"] > 0


def test_calculate_net_zero_gross():
    result = calculate_net(gross_eur=0.0, design_cost_usd=0.0)
    assert result["margin_pct"] == 0.0


def test_calculate_net_with_design_cost():
    without = calculate_net(10.0, design_cost_usd=0.0)
    with_cost = calculate_net(10.0, design_cost_usd=5.0)
    assert with_cost["net_eur"] < without["net_eur"]


def test_calculate_net_with_listing_fee():
    result = calculate_net(10.0, design_cost_usd=0.0, listing_fee_usd=0.20)
    assert result["listing_fee_eur"] > 0


def test_calculate_net_transaction_fee_in_result():
    result = calculate_net(10.0, design_cost_usd=0.0)
    expected_fee_pct = (ETSY_TRANSACTION_PCT + ETSY_PAYMENT_PCT) * 100
    assert abs(result["margin_pct"] - (100 - expected_fee_pct)) < 1


def test_calculate_net_returns_required_keys():
    result = calculate_net(9.99, design_cost_usd=1.0)
    for key in ["gross_eur", "transaction_fee", "listing_fee_eur", "design_cost_eur", "net_eur", "margin_pct"]:
        assert key in result


def test_break_even_price_zero_cost():
    price = break_even_price(design_cost_usd=0.0, listing_fee_usd=0.0)
    assert price == 0.0


def test_break_even_price_positive_cost():
    price = break_even_price(design_cost_usd=2.0, listing_fee_usd=0.20)
    assert price > 0


def test_break_even_price_higher_cost_raises_price():
    cheap = break_even_price(design_cost_usd=1.0)
    expensive = break_even_price(design_cost_usd=5.0)
    assert expensive > cheap


def test_calculate_net_net_eur_type():
    result = calculate_net(7.99, design_cost_usd=0.5)
    assert isinstance(result["net_eur"], float)
    assert isinstance(result["margin_pct"], float)


# ---------------------------------------------------------------------------
# Static method
# ---------------------------------------------------------------------------

def test_generate_review_request_template_contains_niche():
    result = FinanceTracker._generate_review_request_template("wedding planner")
    assert "wedding planner" in result
    assert "Thank you" in result
    assert "review" in result.lower()


def test_generate_review_request_template_returns_string():
    result = FinanceTracker._generate_review_request_template("budget tracker")
    assert isinstance(result, str)
    assert len(result) > 50


# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS revenue_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    etsy_listing_id TEXT    NOT NULL,
    order_id        TEXT    UNIQUE,
    niche           TEXT,
    product_type    TEXT,
    gross_eur       REAL    NOT NULL,
    etsy_fee_eur    REAL    NOT NULL,
    net_eur         REAL    NOT NULL,
    design_cost_eur REAL    DEFAULT 0.0,
    listing_fee_eur REAL    DEFAULT 0.18,
    sold_at         REAL    NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS production_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'planned',
    published_at REAL,
    llm_cost_usd REAL DEFAULT 0.0,
    image_cost_usd REAL DEFAULT 0.0,
    listing_fee_usd REAL DEFAULT 0.20
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL
);
"""


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
# monthly_summary
# ---------------------------------------------------------------------------

async def test_monthly_summary_empty_db(tracker):
    result = await tracker.monthly_summary()
    assert result["n_sales"] == 0
    assert result["gross_eur"] == 0.0
    assert result["net_eur"] == 0.0
    assert "year" in result and "month" in result


async def test_monthly_summary_with_sales(tracker, db):
    now = time.time()
    await db.execute(
        "INSERT INTO revenue_events (etsy_listing_id, gross_eur, etsy_fee_eur, net_eur, sold_at) VALUES (?,?,?,?,?)",
        ("listing_1", 9.99, 1.05, 8.94, now),
    )
    await db.commit()
    result = await tracker.monthly_summary()
    assert result["n_sales"] == 1
    assert abs(result["gross_eur"] - 9.99) < 0.01


async def test_monthly_summary_explicit_month(tracker):
    result = await tracker.monthly_summary(year=2024, month=1)
    assert result["year"] == 2024
    assert result["month"] == 1
    assert result["n_sales"] == 0


async def test_monthly_summary_december_wraps(tracker):
    result = await tracker.monthly_summary(year=2024, month=12)
    assert result["month"] == 12


async def test_monthly_summary_nonzero_gross_has_margin(tracker, db):
    now = time.time()
    await db.execute(
        "INSERT INTO revenue_events (etsy_listing_id, gross_eur, etsy_fee_eur, net_eur, sold_at) VALUES (?,?,?,?,?)",
        ("listing_2", 15.0, 1.5, 13.5, now),
    )
    await db.commit()
    result = await tracker.monthly_summary()
    assert result["margin_pct"] != 0.0


# ---------------------------------------------------------------------------
# goal_progress
# ---------------------------------------------------------------------------

async def test_goal_progress_empty(tracker):
    result = await tracker.goal_progress()
    assert result["goal_eur"] > 0
    assert result["current_net_eur"] == 0.0
    assert result["pct"] == 0.0
    assert "on_track" in result


async def test_goal_progress_explicit_goal(tracker):
    result = await tracker.goal_progress(goal_eur=100.0)
    assert result["goal_eur"] == 100.0


async def test_goal_progress_reads_config(tracker, db):
    await db.execute(
        "INSERT INTO config (key, value, updated_at) VALUES (?,?,?)",
        ("finance.goal_eur", "1000.0", time.time()),
    )
    await db.commit()
    result = await tracker.goal_progress()
    assert result["goal_eur"] == 1000.0


# ---------------------------------------------------------------------------
# top_earners
# ---------------------------------------------------------------------------

async def test_top_earners_empty(tracker):
    result = await tracker.top_earners()
    assert result == []


async def test_top_earners_with_sales(tracker, db):
    now = time.time()
    await db.execute(
        "INSERT INTO revenue_events (etsy_listing_id, niche, product_type, gross_eur, etsy_fee_eur, net_eur, sold_at) VALUES (?,?,?,?,?,?,?)",
        ("listing_1", "planner", "printable_pdf", 9.99, 1.0, 8.99, now),
    )
    await db.commit()
    result = await tracker.top_earners(limit=5, days=7)
    assert len(result) == 1
    assert result[0]["listing_id"] == "listing_1"


# ---------------------------------------------------------------------------
# cost_per_listing_avg / break_even_price_for_avg
# ---------------------------------------------------------------------------

async def test_cost_per_listing_avg_empty(tracker):
    result = await tracker.cost_per_listing_avg()
    assert result["n_listings"] == 0
    assert result["avg_total_usd"] == 0.0


async def test_cost_per_listing_avg_with_data(tracker, db):
    now = time.time()
    await db.execute(
        "INSERT INTO production_queue (status, published_at, llm_cost_usd, image_cost_usd) VALUES (?,?,?,?)",
        ("published", now, 0.10, 0.05),
    )
    await db.commit()
    result = await tracker.cost_per_listing_avg(days=1)
    assert result["n_listings"] == 1
    assert abs(result["avg_llm_usd"] - 0.10) < 0.001


async def test_break_even_price_for_avg(tracker):
    price = await tracker.break_even_price_for_avg()
    assert isinstance(price, float)
    assert price >= 0
