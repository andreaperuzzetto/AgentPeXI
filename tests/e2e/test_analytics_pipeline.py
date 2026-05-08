"""tests/e2e/test_analytics_pipeline.py — A.3 gate: Analytics step criteria.

  AN1: log_agent_task() inserts a row in agent_logs with correct agent_name + task_id
  AN2: finalize_agent_task() updates status to "completed" and stores cost_usd
  AN3: get_task_by_id() returns finalized row with listing_id in input_data (cost chain)
  AN4a: calculate_net() decomposes gross_eur into gross/net/fees correctly
  AN4b: design_cost_eur is propagated in calculate_net() breakdown
  AN5a: break_even_price() returns positive value for positive design cost
  AN5b: higher design cost → higher break-even price
"""
from __future__ import annotations

import pytest

from apps.backend.core.finance_tracker import break_even_price, calculate_net

from tests.e2e.conftest import _make_memory_manager


# ---------------------------------------------------------------------------
# AN1: log_agent_task creates a row in agent_logs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an1_log_agent_task_creates_row(tmp_path):
    """log_agent_task() inserts a row in agent_logs with correct agent_name and task_id."""
    mm = _make_memory_manager(tmp_path)
    await mm.init()
    await mm.log_agent_task(
        agent_name="design",
        task_id="e2e-design-an1",
        status="running",
        input_data={"niche": "weekly planner", "listing_id": "csv_20260508"},
    )
    db = await mm.get_db()
    cursor = await db.execute(
        "SELECT agent_name, task_id, status FROM agent_logs WHERE task_id = ?",
        ("e2e-design-an1",),
    )
    row = await cursor.fetchone()
    assert row is not None, "No row inserted in agent_logs"
    assert row[0] == "design", f"Expected agent_name='design', got {row[0]!r}"
    assert row[1] == "e2e-design-an1"
    assert row[2] == "running"


# ---------------------------------------------------------------------------
# AN2: finalize_agent_task updates status and cost_usd
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an2_finalize_agent_task_updates_cost(tmp_path):
    """finalize_agent_task() updates status to 'completed' and stores cost_usd."""
    mm = _make_memory_manager(tmp_path)
    await mm.init()
    await mm.log_agent_task(
        agent_name="publisher",
        task_id="e2e-pub-an2",
        status="running",
    )
    await mm.finalize_agent_task(
        task_id="e2e-pub-an2",
        status="completed",
        cost_usd=0.042,
        total_cost_usd=0.042,
    )
    db = await mm.get_db()
    cursor = await db.execute(
        "SELECT status, cost_usd FROM agent_logs WHERE task_id = ?",
        ("e2e-pub-an2",),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "completed", f"Expected 'completed', got {row[0]!r}"
    assert abs(row[1] - 0.042) < 1e-6, f"Expected cost_usd=0.042, got {row[1]}"


# ---------------------------------------------------------------------------
# AN3: get_task_by_id roundtrip — listing_id present in input_data (cost chain)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an3_get_task_by_id_roundtrip_with_listing_id(tmp_path):
    """get_task_by_id() returns finalized row with listing_id in input_data and non-negative cost."""
    mm = _make_memory_manager(tmp_path)
    await mm.init()
    listing_id = "csv_20260508_analytics_e2e"
    await mm.log_agent_task(
        agent_name="analytics",
        task_id="e2e-an3",
        status="running",
        input_data={"listing_id": listing_id, "views": 0, "orders": 0},
    )
    await mm.finalize_agent_task(
        task_id="e2e-an3",
        status="completed",
        cost_usd=0.015,
        total_cost_usd=0.015,
    )
    task = await mm.get_task_by_id("e2e-an3")
    assert task is not None, "Task not found via get_task_by_id"
    assert task["status"] == "completed"
    assert task["input_data"]["listing_id"] == listing_id, (
        f"listing_id not propagated: {task['input_data']}"
    )
    assert task["cost_usd"] >= 0, "cost_usd must be non-negative"


# ---------------------------------------------------------------------------
# AN4: calculate_net() — cost breakdown math
# ---------------------------------------------------------------------------

def test_an4a_calculate_net_returns_correct_fields():
    """calculate_net() decomposes gross_eur into all required fields."""
    result = calculate_net(gross_eur=5.00, design_cost_usd=0.10)
    for key in ("gross_eur", "net_eur", "transaction_fee", "listing_fee_eur",
                "design_cost_eur", "margin_pct"):
        assert key in result, f"Missing key: {key!r}"
    assert result["net_eur"] < result["gross_eur"], "Net must be less than gross after fees"
    assert result["net_eur"] > 0, "Net revenue must be positive for €5 listing"


def test_an4b_calculate_net_design_cost_propagated():
    """design_cost_eur is positive and reduces net when design_cost_usd > 0."""
    result = calculate_net(gross_eur=5.00, design_cost_usd=1.00)
    assert result["design_cost_eur"] > 0, "design_cost_eur not propagated"
    result_no_cost = calculate_net(gross_eur=5.00, design_cost_usd=0.0)
    assert result["net_eur"] < result_no_cost["net_eur"], (
        "Higher design cost should reduce net_eur"
    )


# ---------------------------------------------------------------------------
# AN5: break_even_price() — positive, monotone in design cost
# ---------------------------------------------------------------------------

def test_an5a_break_even_price_is_positive():
    """break_even_price() returns a positive EUR price for standard design cost."""
    price = break_even_price(design_cost_usd=0.05)
    assert price > 0, f"break_even_price should be positive, got {price}"


def test_an5b_break_even_price_increases_with_design_cost():
    """Higher design cost → higher break-even price."""
    cheap = break_even_price(design_cost_usd=0.05)
    expensive = break_even_price(design_cost_usd=1.00)
    assert expensive > cheap, (
        f"Higher design cost should increase break-even: cheap={cheap}, expensive={expensive}"
    )
