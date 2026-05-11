"""Tests for FinanceAgent fee calculations and BudgetManager status summary."""
from __future__ import annotations

import time

import aiosqlite
import pytest

from apps.backend.agents._finance._calculations_mixin import _CalculationsMixin
from apps.backend.core.budget_manager import BudgetManager, BudgetStatus, BudgetSummary


# ---------------------------------------------------------------------------
# Fixtures — in-memory SQLite with budget schema
# ---------------------------------------------------------------------------

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
                listing_fee_usd REAL DEFAULT 0.20,
                status TEXT DEFAULT 'pending_design',
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
# _calculate_etsy_fees — exact numeric output
# ---------------------------------------------------------------------------

class TestCalculateEtsyFees:
    def test_known_inputs_exact_values(self):
        result = _CalculationsMixin._calculate_etsy_fees(
            revenue_eur=100.0,
            num_sales=5,
            num_active_listings=3,
        )
        assert result["transaction_fee_eur"] == pytest.approx(6.5, abs=1e-4)
        assert result["payment_fee_pct_eur"] == pytest.approx(3.0, abs=1e-4)
        assert result["payment_fee_fixed_eur"] == pytest.approx(1.15, abs=1e-4)
        assert result["listing_fee_eur"] == pytest.approx(0.54, abs=1e-4)
        assert result["total_fees_eur"] == pytest.approx(11.19, abs=1e-4)

    def test_zero_revenue_no_exception(self):
        result = _CalculationsMixin._calculate_etsy_fees(0.0, 0, 0)
        assert result["total_fees_eur"] == 0.0
        assert result["effective_fee_pct"] == 0.0

    def test_effective_fee_pct_correct(self):
        """For revenue=100, total=11.19 → effective_fee_pct ≈ 11.19%."""
        result = _CalculationsMixin._calculate_etsy_fees(100.0, 5, 3)
        assert result["effective_fee_pct"] == pytest.approx(11.19, abs=0.01)

    def test_single_sale_single_listing(self):
        """Minimal real case: 1 sale, 1 listing, revenue=10."""
        result = _CalculationsMixin._calculate_etsy_fees(10.0, 1, 1)
        expected_total = (
            10.0 * 0.065   # transaction
            + 10.0 * 0.030  # payment pct
            + 1 * 0.23      # payment fixed
            + 1 * 0.18      # listing
        )
        assert result["total_fees_eur"] == pytest.approx(expected_total, abs=1e-4)

    def test_all_fee_components_present(self):
        result = _CalculationsMixin._calculate_etsy_fees(50.0, 2, 5)
        assert set(result.keys()) == {
            "transaction_fee_eur",
            "payment_fee_pct_eur",
            "payment_fee_fixed_eur",
            "listing_fee_eur",
            "total_fees_eur",
            "effective_fee_pct",
        }


# ---------------------------------------------------------------------------
# Margin / ROI via pure arithmetic (using fee calculator as building block)
# ---------------------------------------------------------------------------

class TestMarginRoi:
    def test_positive_margin(self):
        """revenue - cost - fees > 0 → positive margin."""
        fees = _CalculationsMixin._calculate_etsy_fees(50.0, 1, 1)
        margin = 50.0 - 5.0 - fees["total_fees_eur"]
        assert margin > 0

    def test_negative_margin_when_fees_exceed_revenue(self):
        """Very low revenue with many listings → margin can go negative."""
        fees = _CalculationsMixin._calculate_etsy_fees(1.0, 1, 10)
        margin = 1.0 - 2.0 - fees["total_fees_eur"]  # cost=2 > revenue=1
        assert margin < 0

    def test_roi_positive(self):
        """ROI = margin / cost > 0 when profitable."""
        cost = 5.0
        fees = _CalculationsMixin._calculate_etsy_fees(50.0, 1, 1)
        margin = 50.0 - cost - fees["total_fees_eur"]
        roi = margin / cost
        assert roi > 0

    def test_roi_negative(self):
        """ROI < 0 when unprofitable."""
        cost = 100.0
        fees = _CalculationsMixin._calculate_etsy_fees(50.0, 1, 1)
        margin = 50.0 - cost - fees["total_fees_eur"]
        roi = margin / cost
        assert roi < 0


# ---------------------------------------------------------------------------
# BudgetManager.get_status_summary()
# ---------------------------------------------------------------------------

class TestBudgetStatusSummary:
    async def test_summary_has_expected_attributes(self, budget):
        """get_status_summary() returns a BudgetSummary with all expected fields."""
        summary = await budget.get_status_summary()
        assert isinstance(summary, BudgetSummary)
        assert hasattr(summary, "llm_today")
        assert hasattr(summary, "image_today")
        assert hasattr(summary, "fee_today")
        assert hasattr(summary, "llm_limit")
        assert hasattr(summary, "image_limit")
        assert hasattr(summary, "fee_limit")
        assert hasattr(summary, "warn_threshold")
        assert hasattr(summary, "status")

    async def test_empty_queue_status_ok(self, budget):
        """No items in queue → all costs zero → status OK."""
        summary = await budget.get_status_summary()
        assert summary.status == BudgetStatus.OK
        assert summary.llm_today == 0.0
        assert summary.image_today == 0.0
        assert summary.fee_today == 0.0

    async def test_status_exceeded_when_over_limit(self, db, budget):
        """Inserting LLM cost above limit → status EXCEEDED."""
        now = time.time()
        await db.execute(
            "INSERT INTO production_queue (llm_cost_usd, image_cost_usd, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (1.0, 0.0, now, now),  # default limit is 0.50 USD
        )
        await db.commit()
        summary = await budget.get_status_summary()
        assert summary.status == BudgetStatus.EXCEEDED

    async def test_status_warning_at_75_percent(self, db, budget):
        """LLM cost at 75% of limit → status WARNING."""
        await budget.set_limit("daily_llm_usd", 1.00)
        now = time.time()
        await db.execute(
            "INSERT INTO production_queue (llm_cost_usd, image_cost_usd, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (0.75, 0.0, now, now),
        )
        await db.commit()
        summary = await budget.get_status_summary()
        assert summary.status == BudgetStatus.WARNING

    async def test_computed_properties_correct(self, db, budget):
        """total_today and total_limit are computed from components."""
        now = time.time()
        await db.execute(
            "INSERT INTO production_queue (llm_cost_usd, image_cost_usd, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (0.1, 0.2, now, now),
        )
        await db.commit()
        summary = await budget.get_status_summary()
        assert summary.total_today == pytest.approx(0.1 + 0.2 + 0.0, abs=1e-6)
        assert summary.total_limit == pytest.approx(
            summary.llm_limit + summary.image_limit + summary.fee_limit, abs=1e-6
        )
