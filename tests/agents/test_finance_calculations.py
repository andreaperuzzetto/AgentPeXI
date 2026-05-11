"""Coverage tests for apps/backend/agents/_finance/_calculations_mixin.py.

Target: 100% line coverage.
All methods are pure static or simple instance methods — no mocking needed.
Uses pytest.mark.parametrize for numeric helpers and explicit branch-hitting
fixtures for _calculate_finance_confidence and _parse_json_response.
"""
from __future__ import annotations

import pytest

from apps.backend.agents._finance._calculations_mixin import _CalculationsMixin
from apps.backend.agents._finance.constants import (
    ETSY_LISTING_FEE_EUR,
    ETSY_PAYMENT_FEE_FIXED_EUR,
    ETSY_PAYMENT_FEE_PCT,
    ETSY_TRANSACTION_FEE_PCT,
    USD_EUR_RATE,
)


class TestCalculationsMixin:
    """Tests for _CalculationsMixin — pure static helpers + confidence scorer."""

    # ------------------------------------------------------------------
    # _usd_to_eur
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "usd, expected",
        [
            (0.0, 0.0),
            (1.0, round(1.0 * USD_EUR_RATE, 6)),
            (100.0, round(100.0 * USD_EUR_RATE, 6)),
            (10.5, round(10.5 * USD_EUR_RATE, 6)),
        ],
    )
    def test_usd_to_eur(self, usd: float, expected: float) -> None:
        assert _CalculationsMixin._usd_to_eur(usd) == expected

    # ------------------------------------------------------------------
    # _calculate_etsy_fees
    # ------------------------------------------------------------------

    def test_calculate_etsy_fees_zero(self) -> None:
        result = _CalculationsMixin._calculate_etsy_fees(0.0, 0, 0)
        assert result["transaction_fee_eur"] == 0.0
        assert result["payment_fee_pct_eur"] == 0.0
        assert result["payment_fee_fixed_eur"] == 0.0
        assert result["listing_fee_eur"] == 0.0
        assert result["total_fees_eur"] == 0.0
        assert result["effective_fee_pct"] == 0.0  # else 0.0 branch (revenue == 0)

    def test_calculate_etsy_fees_positive(self) -> None:
        revenue, sales, listings = 100.0, 5, 10
        result = _CalculationsMixin._calculate_etsy_fees(revenue, sales, listings)

        expected_tx = round(revenue * ETSY_TRANSACTION_FEE_PCT, 4)
        expected_pct = round(revenue * ETSY_PAYMENT_FEE_PCT, 4)
        expected_fixed = round(sales * ETSY_PAYMENT_FEE_FIXED_EUR, 4)
        expected_lst = round(listings * ETSY_LISTING_FEE_EUR, 4)
        expected_total = round(expected_tx + expected_pct + expected_fixed + expected_lst, 4)
        expected_eff = round(expected_total / revenue * 100, 2)

        assert result["transaction_fee_eur"] == expected_tx
        assert result["payment_fee_pct_eur"] == expected_pct
        assert result["payment_fee_fixed_eur"] == expected_fixed
        assert result["listing_fee_eur"] == expected_lst
        assert result["total_fees_eur"] == expected_total
        assert result["effective_fee_pct"] == expected_eff

    def test_calculate_etsy_fees_zero_revenue_nonzero_sales(self) -> None:
        """revenue_eur=0 with sales/listings → effective_fee_pct = 0.0 (else branch)."""
        result = _CalculationsMixin._calculate_etsy_fees(0.0, 3, 5)
        assert result["effective_fee_pct"] == 0.0
        assert result["payment_fee_fixed_eur"] == round(3 * ETSY_PAYMENT_FEE_FIXED_EUR, 4)
        assert result["listing_fee_eur"] == round(5 * ETSY_LISTING_FEE_EUR, 4)

    # ------------------------------------------------------------------
    # _calculate_finance_confidence — all branches
    # ------------------------------------------------------------------

    def test_confidence_full_data(self) -> None:
        """All data present → score = 1.0, no missing messages.

        Branch coverage:
          - costs_eur > 0 → +0.45
          - model_costs truthy → if body (pass) executed
          - total_rev > 0 and active > 0 → +0.25
          - niches_with_data >= 2 → +0.20
          - has_7d and has_30d → +0.10
        """
        mixin = _CalculationsMixin()
        score, missing = mixin._calculate_finance_confidence(
            costs_eur=100.0,
            revenue_stats={"total_revenue_eur": 500.0, "active_count": 10},
            niche_roi=[{"listing_count": 5}, {"listing_count": 3}],
            model_costs=[{"model": "gpt-4", "cost": 0.05}],
            trend={
                "revenue_7d": 50.0,
                "cost_7d": 10.0,
                "revenue_30d": 200.0,
                "cost_30d": 40.0,
            },
        )
        assert score == 1.0
        assert missing == []

    def test_confidence_all_empty(self) -> None:
        """No data at all → minimum score 0.15, all four missing messages added.

        Branch coverage:
          - costs_eur == 0 → else: +0.15, missing "LLM"
          - model_costs falsy → if body skipped
          - total_rev == 0 and active == 0 → else: missing "listing attivo"
          - niches_with_data == 0 → else: missing "nicchia"
          - neither has_7d nor has_30d → else: missing "trend"
        """
        mixin = _CalculationsMixin()
        score, missing = mixin._calculate_finance_confidence(
            costs_eur=0.0,
            revenue_stats={},
            niche_roi=[],
            model_costs=[],
            trend={},
        )
        assert score == 0.15
        assert len(missing) == 4
        assert any("LLM" in m for m in missing)
        assert any("listing attivo" in m for m in missing)
        assert any("nicchia" in m for m in missing)
        assert any("trend" in m for m in missing)

    def test_confidence_active_no_revenue_one_niche_only_30d(self) -> None:
        """Partial data: active listings but no revenue, 1 niche, only 30d trend.

        Branch coverage:
          - costs_eur > 0 → +0.45
          - model_costs falsy → skipped
          - active > 0 but total_rev == 0 → elif: +0.10, missing revenue message
          - niches_with_data == 1 → elif: +0.12, missing niche message
          - has_30d only → elif: +0.05, missing 30d message
        """
        mixin = _CalculationsMixin()
        score, missing = mixin._calculate_finance_confidence(
            costs_eur=50.0,
            revenue_stats={"active_count": 5},
            niche_roi=[{"listing_count": 2}],
            model_costs=[],
            trend={"revenue_30d": 100.0},
        )
        # 0.45 + 0.10 + 0.12 + 0.05 = 0.72
        assert score == 0.72
        assert any("revenue €0" in m for m in missing)
        assert any("1 nicchia" in m for m in missing)
        assert any("30d" in m for m in missing)

    def test_confidence_only_7d_trend_hits_else_branch(self) -> None:
        """7d data only → has_7d=True, has_30d=False → else branch for trend.

        Also exercises model_costs truthy (if body) with costs_eur=0.
        """
        mixin = _CalculationsMixin()
        score, missing = mixin._calculate_finance_confidence(
            costs_eur=0.0,
            revenue_stats={},
            niche_roi=[],
            model_costs=[{"model": "gpt-3.5"}],
            trend={"cost_7d": 5.0},
        )
        # 0.15 + 0 + 0 + 0 = 0.15; trend else branch
        assert score == 0.15
        assert any("trend" in m and "disponibili" in m for m in missing)

    def test_confidence_score_never_exceeds_one(self) -> None:
        """min(score, 1.0) is always applied — score is capped."""
        mixin = _CalculationsMixin()
        score, _ = mixin._calculate_finance_confidence(
            costs_eur=999.0,
            revenue_stats={"total_revenue_eur": 9999.0, "active_count": 99},
            niche_roi=[{"listing_count": 10}, {"listing_count": 20}],
            model_costs=[{"model": "gpt-4"}],
            trend={"revenue_7d": 1.0, "revenue_30d": 1.0},
        )
        assert score <= 1.0

    def test_confidence_cost_via_cost_7d_and_revenue_7d(self) -> None:
        """Verify has_7d=True via cost_7d only, has_30d=True via cost_30d only."""
        mixin = _CalculationsMixin()
        score, missing = mixin._calculate_finance_confidence(
            costs_eur=10.0,
            revenue_stats={"total_revenue_eur": 50.0, "active_count": 2},
            niche_roi=[{"listing_count": 3}, {"listing_count": 1}],
            model_costs=[],
            trend={"cost_7d": 1.0, "cost_30d": 5.0},
        )
        assert score == 1.0
        assert missing == []

    # ------------------------------------------------------------------
    # _parse_json_response — all branches
    # ------------------------------------------------------------------

    def test_parse_direct_valid_json(self) -> None:
        """Direct JSON parse succeeds immediately."""
        result = _CalculationsMixin._parse_json_response('{"key": "value", "n": 42}')
        assert result == {"key": "value", "n": 42}

    def test_parse_no_braces_returns_none(self) -> None:
        """Plain text with no braces → all regexes fail → None."""
        result = _CalculationsMixin._parse_json_response("not json at all")
        assert result is None

    def test_parse_code_block_json_valid(self) -> None:
        """Valid JSON inside ```json...``` block → inner try succeeds."""
        text = '```json\n{"a": 1, "b": "hello"}\n```'
        result = _CalculationsMixin._parse_json_response(text)
        assert result == {"a": 1, "b": "hello"}

    def test_parse_code_block_no_prefix_valid(self) -> None:
        """Code block without 'json' prefix — (?:json)? is optional."""
        text = '```\n{"x": 99}\n```'
        result = _CalculationsMixin._parse_json_response(text)
        assert result == {"x": 99}

    def test_parse_code_block_invalid_json_returns_none(self) -> None:
        """Invalid JSON inside ```json...``` block → inner except fires; bare regex also fails → None."""
        text = "```json\n{invalid json content}\n```"
        result = _CalculationsMixin._parse_json_response(text)
        assert result is None

    def test_parse_bare_braces_valid_json(self) -> None:
        """Valid JSON embedded in plain text → bare {…} regex inner try succeeds."""
        text = 'prefix text {"status": "ok", "count": 3} suffix text'
        result = _CalculationsMixin._parse_json_response(text)
        assert result == {"status": "ok", "count": 3}

    def test_parse_bare_braces_invalid_json_returns_none(self) -> None:
        """Invalid JSON in bare {…} → inner except fires → returns None."""
        text = "some text {invalid json here: no quotes} more text"
        result = _CalculationsMixin._parse_json_response(text)
        assert result is None
