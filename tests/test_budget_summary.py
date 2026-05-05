"""Tests for BudgetSummary dataclass properties (pure calculation, no DB)."""
from __future__ import annotations

import pytest

from apps.backend.core.budget_manager import BudgetSummary, BudgetStatus


def _make_summary(
    llm_today=0.0, image_today=0.0, fee_today=0.0,
    llm_limit=0.50, image_limit=1.00, fee_limit=1.00,
    warn_threshold=0.75,
    status=BudgetStatus.OK,
):
    return BudgetSummary(
        llm_today=llm_today,
        image_today=image_today,
        fee_today=fee_today,
        llm_limit=llm_limit,
        image_limit=image_limit,
        fee_limit=fee_limit,
        warn_threshold=warn_threshold,
        status=status,
    )


# ---------------------------------------------------------------------------
# llm_pct
# ---------------------------------------------------------------------------

def test_budget_summary_llm_pct_calculation():
    s = _make_summary(llm_today=0.25, llm_limit=0.50)
    assert s.llm_pct == pytest.approx(0.5)


def test_budget_summary_llm_pct_zero_limit_no_division_error():
    s = _make_summary(llm_today=0.10, llm_limit=0.0)
    assert s.llm_pct == 0.0


def test_budget_summary_llm_pct_full_usage():
    s = _make_summary(llm_today=0.50, llm_limit=0.50)
    assert s.llm_pct == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# image_pct
# ---------------------------------------------------------------------------

def test_budget_summary_image_pct_calculation():
    s = _make_summary(image_today=0.40, image_limit=1.00)
    assert s.image_pct == pytest.approx(0.4)


def test_budget_summary_image_pct_zero_limit_no_division_error():
    s = _make_summary(image_today=0.50, image_limit=0.0)
    assert s.image_pct == 0.0


# ---------------------------------------------------------------------------
# fee_pct
# ---------------------------------------------------------------------------

def test_budget_summary_fee_pct_calculation():
    s = _make_summary(fee_today=0.20, fee_limit=1.00)
    assert s.fee_pct == pytest.approx(0.2)


def test_budget_summary_fee_pct_zero_limit_no_division_error():
    s = _make_summary(fee_today=0.20, fee_limit=0.0)
    assert s.fee_pct == 0.0


# ---------------------------------------------------------------------------
# total_today
# ---------------------------------------------------------------------------

def test_budget_summary_total_today():
    s = _make_summary(llm_today=0.10, image_today=0.20, fee_today=0.05)
    assert s.total_today == pytest.approx(0.35)


def test_budget_summary_total_today_zeros():
    s = _make_summary()
    assert s.total_today == 0.0


# ---------------------------------------------------------------------------
# total_limit
# ---------------------------------------------------------------------------

def test_budget_summary_total_limit():
    s = _make_summary(llm_limit=0.50, image_limit=1.00, fee_limit=1.00)
    assert s.total_limit == pytest.approx(2.50)


def test_budget_summary_total_limit_zeros():
    s = _make_summary(llm_limit=0.0, image_limit=0.0, fee_limit=0.0)
    assert s.total_limit == 0.0


# ---------------------------------------------------------------------------
# status field
# ---------------------------------------------------------------------------

def test_budget_summary_status_ok():
    s = _make_summary(status=BudgetStatus.OK)
    assert s.status == BudgetStatus.OK


def test_budget_summary_status_exceeded():
    s = _make_summary(status=BudgetStatus.EXCEEDED)
    assert s.status == BudgetStatus.EXCEEDED


def test_budget_summary_status_warning():
    s = _make_summary(status=BudgetStatus.WARNING)
    assert s.status == BudgetStatus.WARNING
