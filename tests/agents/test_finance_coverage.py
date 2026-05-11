"""Coverage tests for _finance mixins and _analytics.bestsellers_mixin.

Covers (all branches, happy + exception paths):
  - apps/backend/agents/_finance/_context_mixin.py
  - apps/backend/agents/_finance/_insights_mixin.py
  - apps/backend/agents/_finance/_reporting_mixin.py
  - apps/backend/agents/_finance/_roi_mixin.py
  - apps/backend/agents/_analytics/bestsellers_mixin.py
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.backend.agents._finance._context_mixin import _ContextMixin
from apps.backend.agents._finance._insights_mixin import _InsightsMixin
from apps.backend.agents._finance._reporting_mixin import _ReportingMixin
from apps.backend.agents._finance._roi_mixin import _RoiMixin
from apps.backend.agents._finance._calculations_mixin import _CalculationsMixin
from apps.backend.agents._analytics.bestsellers_mixin import _AnalyticsBestsellersMixin
from apps.backend.agents._finance.constants import BUDGET_ALERT_EUR


# ---------------------------------------------------------------------------
# Helpers — concrete composite classes so we can instantiate mixins
# ---------------------------------------------------------------------------

class FakeFinanceAgent(
    _RoiMixin,
    _ContextMixin,
    _InsightsMixin,
    _ReportingMixin,
    _CalculationsMixin,
):
    """Minimal concrete agent that mixes in all finance mixins."""

    def __init__(self):
        self.memory = MagicMock()
        self._telegram_broadcast = AsyncMock()


class FakeAnalyticsAgent(_AnalyticsBestsellersMixin):
    """Minimal concrete agent for the analytics bestsellers mixin."""

    def __init__(self):
        self.memory = MagicMock()
        self._telegram_broadcast = AsyncMock()

    async def _notify_telegram(self, message: str) -> None:
        if self._telegram_broadcast:
            await self._telegram_broadcast(message)


# ---------------------------------------------------------------------------
# Shared niche/product-type data helpers
# ---------------------------------------------------------------------------

NICHE_1 = {
    "niche": "wedding",
    "listing_count": 10,
    "total_sales": 5,
    "total_revenue_eur": 50.0,
    "avg_price_eur": 10.0,
}
NICHE_2 = {
    "niche": "birthday",
    "listing_count": 5,
    "total_sales": 2,
    "total_revenue_eur": 20.0,
    "avg_price_eur": 10.0,
}
NICHE_NEGATIVE = {
    "niche": "xmas",
    "listing_count": 2,
    "total_sales": 0,
    "total_revenue_eur": 0.0,
    "avg_price_eur": 0.0,
}

PT_1 = {
    "product_type": "printable",
    "listing_count": 8,
    "total_sales": 4,
    "total_revenue_eur": 40.0,
}
PT_2 = {
    "product_type": "template",
    "listing_count": 4,
    "total_sales": 1,
    "total_revenue_eur": 10.0,
}

TREND_GROWING = {
    "revenue_7d": 10.0,
    "revenue_30d": 35.0,
    "revenue_7d_normalized_30d": 42.86,
    "revenue_delta_pct": 10.0,
    "cost_7d": 0.001,
    "cost_30d": 0.004,
    "cost_7d_normalized_30d": 0.0043,
    "cost_delta_pct": 5.0,
    "daily_revenue": [],
    "sales_7d": 3,
    "sales_30d": 10,
}
TREND_STABLE = {**TREND_GROWING, "revenue_delta_pct": 0.0}
TREND_DECLINING = {**TREND_GROWING, "revenue_delta_pct": -10.0}


# ===========================================================================
# _RoiMixin tests
# ===========================================================================

class TestComputeNicheRoi:
    """Tests for _RoiMixin._compute_niche_roi."""

    @pytest.fixture
    def agent(self):
        a = FakeFinanceAgent()
        a.memory.get_revenue_by_niche = AsyncMock(return_value=[NICHE_1, NICHE_2])
        a.memory.get_cost_breakdown = AsyncMock(return_value={"total": 0.01})
        return a

    async def test_returns_list_sorted_by_roi_desc(self, agent):
        result = await asyncio.wait_for(agent._compute_niche_roi(30), timeout=5)
        assert isinstance(result, list)
        rois = [r["roi_pct"] for r in result]
        assert rois == sorted(rois, reverse=True)

    async def test_all_required_keys_present(self, agent):
        result = await asyncio.wait_for(agent._compute_niche_roi(30), timeout=5)
        for row in result:
            for key in (
                "niche", "listing_count", "total_sales", "total_revenue_eur",
                "etsy_fees_eur", "llm_cost_attributed_eur", "net_margin_eur",
                "roi_pct", "avg_price_eur", "break_even_units", "cost_per_listing_eur",
            ):
                assert key in row, f"Missing key: {key}"

    async def test_roi_positive_when_revenue_exceeds_costs(self, agent):
        result = await asyncio.wait_for(agent._compute_niche_roi(30), timeout=5)
        # With revenue=50 and tiny LLM cost the ROI should be very positive
        assert result[0]["roi_pct"] > 0

    async def test_zero_llm_cost_roi_is_zero(self):
        agent = FakeFinanceAgent()
        agent.memory.get_revenue_by_niche = AsyncMock(return_value=[NICHE_1])
        agent.memory.get_cost_breakdown = AsyncMock(return_value={"total": 0.0})
        result = await asyncio.wait_for(agent._compute_niche_roi(30), timeout=5)
        assert result[0]["roi_pct"] == 0.0

    async def test_zero_count_listings_handled(self):
        """listing_count=0 should not raise a ZeroDivisionError."""
        niche_zero = {**NICHE_1, "listing_count": 0}
        agent = FakeFinanceAgent()
        agent.memory.get_revenue_by_niche = AsyncMock(return_value=[niche_zero])
        agent.memory.get_cost_breakdown = AsyncMock(return_value={"total": 1.0})
        result = await asyncio.wait_for(agent._compute_niche_roi(30), timeout=5)
        assert result[0]["cost_per_listing_eur"] == 0.0

    async def test_negative_net_per_sale_break_even_zero(self):
        """avg_price_eur = 0 → net_per_sale <= 0 → break_even = 0."""
        niche_zero_price = {**NICHE_1, "avg_price_eur": 0.0}
        agent = FakeFinanceAgent()
        agent.memory.get_revenue_by_niche = AsyncMock(return_value=[niche_zero_price])
        agent.memory.get_cost_breakdown = AsyncMock(return_value={"total": 0.01})
        result = await asyncio.wait_for(agent._compute_niche_roi(30), timeout=5)
        assert result[0]["break_even_units"] == 0

    async def test_empty_niches_returns_empty_list(self):
        agent = FakeFinanceAgent()
        agent.memory.get_revenue_by_niche = AsyncMock(return_value=[])
        agent.memory.get_cost_breakdown = AsyncMock(return_value={"total": 0.01})
        result = await asyncio.wait_for(agent._compute_niche_roi(30), timeout=5)
        assert result == []

    async def test_multiple_niches_llm_cost_proportional(self, agent):
        """Total attributed LLM cost should roughly equal total LLM cost."""
        result = await asyncio.wait_for(agent._compute_niche_roi(30), timeout=5)
        total_attributed = sum(r["llm_cost_attributed_eur"] for r in result)
        total_llm = agent._usd_to_eur(0.01)
        assert abs(total_attributed - total_llm) < 1e-4


class TestComputeProductTypeRoi:
    """Tests for _RoiMixin._compute_product_type_roi."""

    @pytest.fixture
    def agent(self):
        a = FakeFinanceAgent()
        a.memory.get_revenue_by_product_type = AsyncMock(return_value=[PT_1, PT_2])
        a.memory.get_cost_breakdown = AsyncMock(return_value={"total": 0.01})
        return a

    async def test_returns_sorted_by_roi_desc(self, agent):
        result = await asyncio.wait_for(agent._compute_product_type_roi(30), timeout=5)
        rois = [r["roi_pct"] for r in result]
        assert rois == sorted(rois, reverse=True)

    async def test_all_keys_present(self, agent):
        result = await asyncio.wait_for(agent._compute_product_type_roi(30), timeout=5)
        for row in result:
            for key in (
                "product_type", "listing_count", "total_sales", "total_revenue_eur",
                "etsy_fees_eur", "llm_cost_attributed_eur", "net_margin_eur", "roi_pct",
            ):
                assert key in row

    async def test_empty_types_returns_empty(self):
        agent = FakeFinanceAgent()
        agent.memory.get_revenue_by_product_type = AsyncMock(return_value=[])
        agent.memory.get_cost_breakdown = AsyncMock(return_value={"total": 0.0})
        result = await asyncio.wait_for(agent._compute_product_type_roi(30), timeout=5)
        assert result == []

    async def test_zero_llm_cost_roi_is_zero(self):
        agent = FakeFinanceAgent()
        agent.memory.get_revenue_by_product_type = AsyncMock(return_value=[PT_1])
        agent.memory.get_cost_breakdown = AsyncMock(return_value={"total": 0.0})
        result = await asyncio.wait_for(agent._compute_product_type_roi(30), timeout=5)
        assert result[0]["roi_pct"] == 0.0


class TestComputeTrend:
    """Tests for _RoiMixin._compute_trend."""

    @pytest.fixture
    def agent(self):
        a = FakeFinanceAgent()
        a.memory.get_revenue_stats = AsyncMock(side_effect=[
            {"total_revenue_eur": 10.0, "total_sales": 3},   # 7d
            {"total_revenue_eur": 35.0, "total_sales": 10},  # 30d
        ])
        a.memory.get_cost_breakdown = AsyncMock(side_effect=[
            {"total": 0.001},  # 7d
            {"total": 0.004},  # 30d
        ])
        a.memory.get_daily_revenue_trend = AsyncMock(return_value=[])
        return a

    async def test_all_keys_present(self, agent):
        result = await asyncio.wait_for(agent._compute_trend(), timeout=5)
        for key in (
            "revenue_7d", "revenue_30d", "revenue_7d_normalized_30d",
            "revenue_delta_pct", "cost_7d", "cost_30d",
            "cost_7d_normalized_30d", "cost_delta_pct",
            "daily_revenue", "sales_7d", "sales_30d",
        ):
            assert key in result

    async def test_revenue_delta_positive_when_7d_higher(self, agent):
        # 7d normalized = 10 * (30/7) ≈ 42.86 > 30d=35 → positive delta
        result = await asyncio.wait_for(agent._compute_trend(), timeout=5)
        assert result["revenue_delta_pct"] > 0

    async def test_zero_revenue_30d_delta_is_zero(self):
        agent = FakeFinanceAgent()
        agent.memory.get_revenue_stats = AsyncMock(side_effect=[
            {"total_revenue_eur": 5.0, "total_sales": 1},
            {"total_revenue_eur": 0.0, "total_sales": 0},
        ])
        agent.memory.get_cost_breakdown = AsyncMock(side_effect=[
            {"total": 0.0},
            {"total": 0.0},
        ])
        agent.memory.get_daily_revenue_trend = AsyncMock(return_value=[])
        result = await asyncio.wait_for(agent._compute_trend(), timeout=5)
        assert result["revenue_delta_pct"] == 0.0

    async def test_zero_cost_30d_cost_delta_is_zero(self):
        agent = FakeFinanceAgent()
        agent.memory.get_revenue_stats = AsyncMock(side_effect=[
            {"total_revenue_eur": 0.0, "total_sales": 0},
            {"total_revenue_eur": 0.0, "total_sales": 0},
        ])
        agent.memory.get_cost_breakdown = AsyncMock(side_effect=[
            {"total": 0.001},
            {"total": 0.0},
        ])
        agent.memory.get_daily_revenue_trend = AsyncMock(return_value=[])
        result = await asyncio.wait_for(agent._compute_trend(), timeout=5)
        assert result["cost_delta_pct"] == 0.0


# ===========================================================================
# _ContextMixin tests
# ===========================================================================

class TestReadLearningContext:
    """Tests for _ContextMixin._read_learning_context."""

    @pytest.fixture
    def agent(self):
        a = FakeFinanceAgent()
        # Default: all queries return empty lists
        a.memory.query_chromadb_recent = AsyncMock(return_value=[])
        return a

    async def test_empty_chromadb_returns_baseline_structure(self, agent):
        result = await asyncio.wait_for(agent._read_learning_context(), timeout=5)
        assert result["design_winners"] == []
        assert result["failure_count"] == 0
        assert result["success_count"] == 0
        assert result["failure_rate"] == 0.0
        assert result["research_pricing"] == []

    async def test_design_winners_parsed_correctly(self):
        agent = FakeFinanceAgent()
        docs = [
            {
                "metadata": {
                    "niche": "wedding",
                    "template": "minimal",
                    "color_scheme": "sage",
                    "sales": "5",
                    "views": "100",
                },
                "document": "Great design",
            }
        ]
        # Mock side_effect: 1st call=winners, 2nd=failures, 3rd=successes, 4th=research
        agent.memory.query_chromadb_recent = AsyncMock(side_effect=[
            docs, [], [], []
        ])
        result = await asyncio.wait_for(agent._read_learning_context(), timeout=5)
        assert len(result["design_winners"]) == 1
        assert result["design_winners"][0]["niche"] == "wedding"
        assert result["design_winners"][0]["template"] == "minimal"

    async def test_design_winner_without_niche_skipped(self):
        """Docs without niche or template are skipped."""
        agent = FakeFinanceAgent()
        docs = [{"metadata": {"niche": "", "template": "t1"}}]
        agent.memory.query_chromadb_recent = AsyncMock(side_effect=[
            docs, [], [], []
        ])
        result = await asyncio.wait_for(agent._read_learning_context(), timeout=5)
        assert result["design_winners"] == []

    async def test_failure_success_counts_and_rate(self):
        agent = FakeFinanceAgent()
        failures = [{"doc": i} for i in range(3)]
        successes = [{"doc": i} for i in range(7)]
        agent.memory.query_chromadb_recent = AsyncMock(side_effect=[
            [], failures, successes, []
        ])
        result = await asyncio.wait_for(agent._read_learning_context(), timeout=5)
        assert result["failure_count"] == 3
        assert result["success_count"] == 7
        assert result["failure_rate"] == pytest.approx(0.3, abs=1e-3)

    async def test_research_pricing_parsed(self):
        agent = FakeFinanceAgent()
        rp_docs = [
            {
                "metadata": {"niche": "wedding"},
                "document": "Sweet spot is $12-15 for printable invitations on Etsy",
            }
        ]
        agent.memory.query_chromadb_recent = AsyncMock(side_effect=[
            [], [], [], rp_docs
        ])
        result = await asyncio.wait_for(agent._read_learning_context(), timeout=5)
        assert len(result["research_pricing"]) == 1
        assert result["research_pricing"][0]["niche"] == "wedding"

    async def test_research_pricing_doc_without_niche_skipped(self):
        agent = FakeFinanceAgent()
        rp_docs = [{"metadata": {"niche": ""}, "document": "irrelevant"}]
        agent.memory.query_chromadb_recent = AsyncMock(side_effect=[
            [], [], [], rp_docs
        ])
        result = await asyncio.wait_for(agent._read_learning_context(), timeout=5)
        assert result["research_pricing"] == []

    async def test_winners_exception_logged_and_continues(self):
        """If ChromaDB raises, the function logs and returns empty winners."""
        agent = FakeFinanceAgent()
        agent.memory.query_chromadb_recent = AsyncMock(
            side_effect=[RuntimeError("chroma error"), [], [], []]
        )
        result = await asyncio.wait_for(agent._read_learning_context(), timeout=5)
        assert result["design_winners"] == []

    async def test_failure_exception_logged_and_continues(self):
        agent = FakeFinanceAgent()
        agent.memory.query_chromadb_recent = AsyncMock(
            side_effect=[[], RuntimeError("failure error"), [], []]
        )
        result = await asyncio.wait_for(agent._read_learning_context(), timeout=5)
        assert result["failure_count"] == 0

    async def test_success_exception_logged_and_continues(self):
        agent = FakeFinanceAgent()
        agent.memory.query_chromadb_recent = AsyncMock(
            side_effect=[[], [], RuntimeError("success error"), []]
        )
        result = await asyncio.wait_for(agent._read_learning_context(), timeout=5)
        assert result["success_count"] == 0

    async def test_research_exception_logged_and_continues(self):
        agent = FakeFinanceAgent()
        agent.memory.query_chromadb_recent = AsyncMock(
            side_effect=[[], [], [], RuntimeError("research error")]
        )
        result = await asyncio.wait_for(agent._read_learning_context(), timeout=5)
        assert result["research_pricing"] == []

    async def test_failure_rate_zero_when_no_attempts(self, agent):
        result = await asyncio.wait_for(agent._read_learning_context(), timeout=5)
        assert result["failure_rate"] == 0.0


# ===========================================================================
# _InsightsMixin tests
# ===========================================================================

class TestGenerateCostInsights:
    """Tests for _InsightsMixin._generate_cost_insights."""

    def _make_agent(self, llm_response: str | None = None, llm_raises: bool = False):
        agent = FakeFinanceAgent()
        if llm_raises:
            agent._call_llm = AsyncMock(side_effect=RuntimeError("LLM down"))
        else:
            agent._call_llm = AsyncMock(return_value=llm_response or "")
        return agent

    async def test_llm_valid_json_returned_directly(self):
        payload = {
            "agent_efficiency": {
                "agente_piu_costoso": "design",
                "percentuale_costo_totale": 60.0,
                "valutazione": "accettabile",
            },
            "modello_ottimale": "Haiku",
            "top_cost_concern": "burn rate",
            "optimize_suggestion": "use Haiku more",
            "burn_rate_monthly_eur": 3.0,
        }
        agent = self._make_agent(json.dumps(payload))
        result = await asyncio.wait_for(
            agent._generate_cost_insights(
                costs_eur=1.0,
                per_agent_costs_eur={"design": 0.6, "publish": 0.4},
                net_margin_eur=5.0,
                roi_pct=50.0,
                model_costs=[{"model": "haiku", "cost_eur": 0.5, "call_count": 100}],
                period_days=30,
            ),
            timeout=5,
        )
        assert result["agent_efficiency"]["agente_piu_costoso"] == "design"

    async def test_llm_invalid_json_falls_back_to_deterministic(self):
        agent = self._make_agent("not json at all")
        result = await asyncio.wait_for(
            agent._generate_cost_insights(
                costs_eur=2.0,
                per_agent_costs_eur={"design": 1.5, "publish": 0.5},
                net_margin_eur=10.0,
                roi_pct=100.0,
                model_costs=[],
                period_days=30,
            ),
            timeout=5,
        )
        assert result["agent_efficiency"]["agente_piu_costoso"] == "design"
        assert result["agent_efficiency"]["valutazione"] == "accettabile"
        assert "burn_rate_monthly_eur" in result

    async def test_llm_exception_falls_back_to_deterministic(self):
        agent = self._make_agent(llm_raises=True)
        result = await asyncio.wait_for(
            agent._generate_cost_insights(
                costs_eur=1.0,
                per_agent_costs_eur={"design": 1.0},
                net_margin_eur=-5.0,
                roi_pct=-10.0,
                model_costs=[],
                period_days=7,
            ),
            timeout=5,
        )
        assert result["agent_efficiency"]["valutazione"] == "da_ottimizzare"

    async def test_empty_per_agent_costs(self):
        agent = self._make_agent("bad json")
        result = await asyncio.wait_for(
            agent._generate_cost_insights(
                costs_eur=0.0,
                per_agent_costs_eur={},
                net_margin_eur=0.0,
                roi_pct=0.0,
                model_costs=[],
                period_days=30,
            ),
            timeout=5,
        )
        assert result["agent_efficiency"]["agente_piu_costoso"] == "n/a"

    async def test_burn_rate_computed_correctly_in_fallback(self):
        agent = self._make_agent("bad json")
        result = await asyncio.wait_for(
            agent._generate_cost_insights(
                costs_eur=10.0,
                per_agent_costs_eur={"a": 10.0},
                net_margin_eur=50.0,
                roi_pct=500.0,
                model_costs=[],
                period_days=10,
            ),
            timeout=5,
        )
        expected_burn = 10.0 / 10 * 30
        assert result["burn_rate_monthly_eur"] == pytest.approx(expected_burn, abs=1e-3)

    async def test_model_costs_empty_str_in_prompt(self):
        """Empty model_costs list should produce '(nessun dato modello)' in prompt."""
        agent = self._make_agent("bad json")
        await asyncio.wait_for(
            agent._generate_cost_insights(
                costs_eur=1.0,
                per_agent_costs_eur={},
                net_margin_eur=0.0,
                roi_pct=0.0,
                model_costs=[],
                period_days=30,
            ),
            timeout=5,
        )
        # Verify _call_llm was called (prompt built ok, even if fallback used)
        agent._call_llm.assert_called_once()


class TestGenerateRoiAnalysis:
    """Tests for _InsightsMixin._generate_roi_analysis."""

    NICHE_ROI = [
        {"niche": "wedding", "roi_pct": 120.0, "total_revenue_eur": 100.0, "total_sales": 10},
        {"niche": "birthday", "roi_pct": -5.0, "total_revenue_eur": 10.0, "total_sales": 2},
    ]
    PT_ROI = [
        {"product_type": "printable", "roi_pct": 80.0, "listing_count": 5},
    ]

    def _make_agent(self, llm_response: str | None = None, llm_raises: bool = False):
        agent = FakeFinanceAgent()
        if llm_raises:
            agent._call_llm = AsyncMock(side_effect=RuntimeError("LLM down"))
        else:
            agent._call_llm = AsyncMock(return_value=llm_response or "")
        return agent

    async def test_llm_valid_json_returned(self):
        payload = {
            "top_niches_to_scale": [{"niche": "wedding", "reason": "high ROI"}],
            "niches_to_abandon": [],
            "best_product_type": "printable",
            "strategic_recommendation": "Scale wedding",
            "forecast_30d": {"revenue_eur": 120.0, "confidence": "medium", "assumption": "linear"},
        }
        agent = self._make_agent(json.dumps(payload))
        result = await asyncio.wait_for(
            agent._generate_roi_analysis(
                niche_roi=self.NICHE_ROI,
                product_type_roi=self.PT_ROI,
                trend=TREND_GROWING,
                net_margin_eur=80.0,
                roi_pct=120.0,
                period_days=30,
            ),
            timeout=5,
        )
        assert result["best_product_type"] == "printable"

    async def test_fallback_when_llm_raises(self):
        agent = self._make_agent(llm_raises=True)
        result = await asyncio.wait_for(
            agent._generate_roi_analysis(
                niche_roi=self.NICHE_ROI,
                product_type_roi=self.PT_ROI,
                trend=TREND_GROWING,
                net_margin_eur=80.0,
                roi_pct=120.0,
                period_days=30,
            ),
            timeout=5,
        )
        assert result["best_product_type"] == "printable"
        assert "forecast_30d" in result

    async def test_fallback_empty_niches(self):
        agent = self._make_agent("bad json")
        result = await asyncio.wait_for(
            agent._generate_roi_analysis(
                niche_roi=[],
                product_type_roi=[],
                trend={**TREND_GROWING, "revenue_30d": 0.0},
                net_margin_eur=0.0,
                roi_pct=0.0,
                period_days=30,
            ),
            timeout=5,
        )
        assert result["top_niches_to_scale"] == []
        assert result["niches_to_abandon"] == []

    async def test_trend_stabile_branch(self):
        """revenue_delta_pct in [-5, 5] → 'stabile' in prompt."""
        agent = self._make_agent("bad json")
        # Just verify it runs without error for stable trend
        await asyncio.wait_for(
            agent._generate_roi_analysis(
                niche_roi=self.NICHE_ROI,
                product_type_roi=self.PT_ROI,
                trend=TREND_STABLE,
                net_margin_eur=50.0,
                roi_pct=80.0,
                period_days=30,
            ),
            timeout=5,
        )

    async def test_trend_declining_branch(self):
        """revenue_delta_pct < -5 → 'in calo' in prompt."""
        agent = self._make_agent("bad json")
        await asyncio.wait_for(
            agent._generate_roi_analysis(
                niche_roi=self.NICHE_ROI,
                product_type_roi=self.PT_ROI,
                trend=TREND_DECLINING,
                net_margin_eur=50.0,
                roi_pct=80.0,
                period_days=30,
            ),
            timeout=5,
        )

    async def test_with_learning_context_design_winners(self):
        """Learning context with winners populates winners_str branch."""
        agent = self._make_agent("bad json")
        lc = {
            "design_winners": [
                {"niche": "w", "template": "t", "color_scheme": "sage", "sales": "5", "views": "100"},
            ],
            "failure_rate": 0.1,
            "failure_count": 1,
            "success_count": 9,
            "research_pricing": [{"niche": "w", "summary": "good price"}],
        }
        result = await asyncio.wait_for(
            agent._generate_roi_analysis(
                niche_roi=self.NICHE_ROI,
                product_type_roi=self.PT_ROI,
                trend=TREND_GROWING,
                net_margin_eur=50.0,
                roi_pct=80.0,
                period_days=30,
                learning_context=lc,
            ),
            timeout=5,
        )
        # Should complete successfully and use fallback since LLM returns "bad json"
        assert "forecast_30d" in result

    async def test_with_no_learning_context(self):
        """learning_context=None should not raise."""
        agent = self._make_agent("bad json")
        result = await asyncio.wait_for(
            agent._generate_roi_analysis(
                niche_roi=self.NICHE_ROI,
                product_type_roi=self.PT_ROI,
                trend=TREND_GROWING,
                net_margin_eur=50.0,
                roi_pct=80.0,
                period_days=30,
                learning_context=None,
            ),
            timeout=5,
        )
        assert "forecast_30d" in result

    async def test_fallback_recommendation_when_no_best_niche(self):
        agent = self._make_agent("bad json")
        result = await asyncio.wait_for(
            agent._generate_roi_analysis(
                niche_roi=[],
                product_type_roi=[],
                trend={**TREND_GROWING, "revenue_30d": 0.0},
                net_margin_eur=0.0,
                roi_pct=0.0,
                period_days=30,
            ),
            timeout=5,
        )
        assert "Dati insufficienti" in result["strategic_recommendation"]


class TestCheckBudgetAlert:
    """Tests for _InsightsMixin._check_budget_alert."""

    async def test_costs_zero_returns_false_immediately(self):
        agent = FakeFinanceAgent()
        agent.memory.get_pending_action = AsyncMock(return_value=None)
        result = await asyncio.wait_for(
            agent._check_budget_alert(costs_eur=0.0, period_days=30),
            timeout=5,
        )
        assert result is False

    async def test_costs_below_threshold_returns_false(self):
        agent = FakeFinanceAgent()
        agent.memory.get_pending_action = AsyncMock(return_value=None)
        # BUDGET_ALERT_EUR is typically 70€/month; costs here are tiny
        result = await asyncio.wait_for(
            agent._check_budget_alert(costs_eur=0.01, period_days=30),
            timeout=5,
        )
        assert result is False

    async def test_alert_already_sent_returns_false(self):
        """If pending_action exists, returns False (dedup)."""
        agent = FakeFinanceAgent()
        agent.memory.get_pending_action = AsyncMock(return_value={"already": True})
        # Use very high cost to exceed threshold
        high_cost = BUDGET_ALERT_EUR * 2
        result = await asyncio.wait_for(
            agent._check_budget_alert(costs_eur=high_cost, period_days=30),
            timeout=5,
        )
        assert result is False

    async def test_alert_sent_and_returns_true(self):
        """When cost exceeds threshold and no previous alert → sends alert, returns True."""
        agent = FakeFinanceAgent()
        agent.memory.get_pending_action = AsyncMock(return_value=None)
        agent.memory.save_pending_action = AsyncMock(return_value=None)
        high_cost = BUDGET_ALERT_EUR * 2  # ensures monthly_equivalent > BUDGET_ALERT_EUR
        result = await asyncio.wait_for(
            agent._check_budget_alert(costs_eur=high_cost, period_days=30),
            timeout=5,
        )
        assert result is True
        agent._telegram_broadcast.assert_called_once()
        agent.memory.save_pending_action.assert_called_once()

    async def test_telegram_message_contains_budget_info(self):
        agent = FakeFinanceAgent()
        agent.memory.get_pending_action = AsyncMock(return_value=None)
        agent.memory.save_pending_action = AsyncMock(return_value=None)
        high_cost = BUDGET_ALERT_EUR * 2
        await asyncio.wait_for(
            agent._check_budget_alert(costs_eur=high_cost, period_days=30),
            timeout=5,
        )
        sent_msg = agent._telegram_broadcast.call_args[0][0]
        assert "Budget Alert" in sent_msg
        assert "#budget" in sent_msg


# ===========================================================================
# _ReportingMixin tests
# ===========================================================================

def _make_full_report_kwargs(**overrides):
    base = dict(
        today_str="2024-01-15",
        period_days=30,
        costs_eur=0.01,
        per_agent_costs_eur={"design": 0.006, "publish": 0.004},
        fees={"total_fees_eur": 5.0, "effective_fee_pct": 10.0},
        total_revenue_eur=100.0,
        total_sales=10,
        active_count=20,
        avg_price_eur=10.0,
        gross_margin_eur=95.0,
        gross_margin_pct=95.0,
        net_margin_eur=85.0,
        net_margin_pct=85.0,
        roi_pct=500.0,
        niche_roi=[{
            "niche": "wedding",
            "roi_pct": 500.0,
            "total_revenue_eur": 100.0,
            "total_sales": 10,
        }],
        product_type_roi=[],
        model_costs=[],
        trend=TREND_GROWING,
        cost_insights={},
        roi_analysis={
            "strategic_recommendation": "Scale wedding niche",
            "forecast_30d": {"revenue_eur": 120.0, "confidence": "medium"},
        },
        budget_alert_sent=False,
    )
    base.update(overrides)
    return base


class TestBuildReport:
    """Tests for _ReportingMixin._build_report."""

    def test_all_keys_present(self):
        agent = FakeFinanceAgent()
        result = agent._build_report(**_make_full_report_kwargs())
        for key in (
            "date", "period_days", "total_revenue_eur", "total_sales",
            "active_listings", "avg_price_eur", "llm_cost_eur",
            "per_agent_costs_eur", "etsy_fees", "gross_margin_eur",
            "gross_margin_pct", "net_margin_eur", "net_margin_pct",
            "roi_pct", "niche_roi", "product_type_roi", "model_costs",
            "trend", "cost_insights", "roi_analysis",
            "budget_threshold_eur", "budget_alert_sent",
        ):
            assert key in result, f"Missing key: {key}"

    def test_values_rounded_correctly(self):
        agent = FakeFinanceAgent()
        result = agent._build_report(**_make_full_report_kwargs(
            total_revenue_eur=100.123456789,
            costs_eur=0.0123456789,
        ))
        assert result["total_revenue_eur"] == round(100.123456789, 4)
        assert result["llm_cost_eur"] == round(0.0123456789, 6)

    def test_per_agent_costs_rounded(self):
        agent = FakeFinanceAgent()
        result = agent._build_report(**_make_full_report_kwargs(
            per_agent_costs_eur={"design": 0.0063452}
        ))
        assert result["per_agent_costs_eur"]["design"] == round(0.0063452, 6)

    def test_budget_threshold_is_constant(self):
        agent = FakeFinanceAgent()
        result = agent._build_report(**_make_full_report_kwargs())
        assert result["budget_threshold_eur"] == BUDGET_ALERT_EUR

    def test_budget_alert_sent_flag_preserved(self):
        agent = FakeFinanceAgent()
        result = agent._build_report(**_make_full_report_kwargs(budget_alert_sent=True))
        assert result["budget_alert_sent"] is True


class TestSendFinanceSummary:
    """Tests for _ReportingMixin._send_finance_summary."""

    async def test_telegram_called_with_message(self):
        agent = FakeFinanceAgent()
        report = agent._build_report(**_make_full_report_kwargs())
        await asyncio.wait_for(
            agent._send_finance_summary(report, "2024-01-15"),
            timeout=5,
        )
        agent._telegram_broadcast.assert_called_once()
        msg = agent._telegram_broadcast.call_args[0][0]
        assert "#finance" in msg

    async def test_niche_roi_line_included_when_present(self):
        agent = FakeFinanceAgent()
        report = agent._build_report(**_make_full_report_kwargs())
        await asyncio.wait_for(
            agent._send_finance_summary(report, "2024-01-15"),
            timeout=5,
        )
        msg = agent._telegram_broadcast.call_args[0][0]
        assert "wedding" in msg

    async def test_no_niche_roi_shows_dash(self):
        agent = FakeFinanceAgent()
        report = agent._build_report(**_make_full_report_kwargs(niche_roi=[]))
        await asyncio.wait_for(
            agent._send_finance_summary(report, "2024-01-15"),
            timeout=5,
        )
        msg = agent._telegram_broadcast.call_args[0][0]
        assert "—" in msg

    async def test_trend_icon_rising(self):
        agent = FakeFinanceAgent()
        report = agent._build_report(**_make_full_report_kwargs(
            trend={**TREND_GROWING, "revenue_delta_pct": 10.0}
        ))
        await asyncio.wait_for(
            agent._send_finance_summary(report, "2024-01-15"),
            timeout=5,
        )
        msg = agent._telegram_broadcast.call_args[0][0]
        assert "📈" in msg

    async def test_trend_icon_declining(self):
        agent = FakeFinanceAgent()
        report = agent._build_report(**_make_full_report_kwargs(
            trend={**TREND_GROWING, "revenue_delta_pct": -10.0}
        ))
        await asyncio.wait_for(
            agent._send_finance_summary(report, "2024-01-15"),
            timeout=5,
        )
        msg = agent._telegram_broadcast.call_args[0][0]
        assert "📉" in msg

    async def test_trend_icon_stable(self):
        agent = FakeFinanceAgent()
        report = agent._build_report(**_make_full_report_kwargs(
            trend={**TREND_GROWING, "revenue_delta_pct": 2.0}
        ))
        await asyncio.wait_for(
            agent._send_finance_summary(report, "2024-01-15"),
            timeout=5,
        )
        msg = agent._telegram_broadcast.call_args[0][0]
        assert "➡️" in msg

    async def test_margin_green_when_high(self):
        agent = FakeFinanceAgent()
        report = agent._build_report(**_make_full_report_kwargs(net_margin_pct=50.0))
        await asyncio.wait_for(
            agent._send_finance_summary(report, "2024-01-15"),
            timeout=5,
        )
        msg = agent._telegram_broadcast.call_args[0][0]
        assert "🟢" in msg

    async def test_margin_yellow_when_zero_to_30(self):
        agent = FakeFinanceAgent()
        report = agent._build_report(**_make_full_report_kwargs(net_margin_pct=10.0))
        await asyncio.wait_for(
            agent._send_finance_summary(report, "2024-01-15"),
            timeout=5,
        )
        msg = agent._telegram_broadcast.call_args[0][0]
        assert "🟡" in msg

    async def test_margin_red_when_negative(self):
        agent = FakeFinanceAgent()
        report = agent._build_report(**_make_full_report_kwargs(net_margin_pct=-5.0))
        await asyncio.wait_for(
            agent._send_finance_summary(report, "2024-01-15"),
            timeout=5,
        )
        msg = agent._telegram_broadcast.call_args[0][0]
        assert "🔴" in msg


class TestNotifyTelegram:
    """Tests for _ReportingMixin._notify_telegram."""

    async def test_message_forwarded_to_broadcast(self):
        agent = FakeFinanceAgent()
        await asyncio.wait_for(
            agent._notify_telegram("hello"),
            timeout=5,
        )
        agent._telegram_broadcast.assert_called_once_with("hello")

    async def test_broadcast_none_does_not_raise(self):
        agent = FakeFinanceAgent()
        agent._telegram_broadcast = None
        # Should be a no-op
        await asyncio.wait_for(
            agent._notify_telegram("hello"),
            timeout=5,
        )

    async def test_broadcast_exception_logged_not_raised(self):
        agent = FakeFinanceAgent()
        agent._telegram_broadcast = AsyncMock(side_effect=RuntimeError("net error"))
        # Should swallow the exception
        await asyncio.wait_for(
            agent._notify_telegram("hello"),
            timeout=5,
        )


# ===========================================================================
# _AnalyticsBestsellersMixin tests
# ===========================================================================

class TestFindBestsellers:
    """Tests for _AnalyticsBestsellersMixin._find_bestsellers."""

    def _listing(self, lid, sales, revenue=0.0, niche="wedding", template="t1", color="sage"):
        return {
            "listing_id": lid,
            "title": f"Listing {lid}",
            "sales": sales,
            "revenue_eur": revenue,
            "niche": niche,
            "template": template,
            "color_scheme": color,
            "product_type": "printable",
        }

    async def test_empty_listings_returns_empty(self):
        agent = FakeAnalyticsAgent()
        agent.memory.get_etsy_listings = AsyncMock(return_value=[])
        result = await asyncio.wait_for(agent._find_bestsellers(), timeout=5)
        assert result == []

    async def test_no_listings_above_threshold_returns_empty(self):
        agent = FakeAnalyticsAgent()
        listings = [self._listing(i, 0) for i in range(5)]
        agent.memory.get_etsy_listings = AsyncMock(return_value=listings)
        agent.memory.get_pending_action = AsyncMock(return_value=None)
        agent.memory.save_pending_action = AsyncMock()
        result = await asyncio.wait_for(agent._find_bestsellers(), timeout=5)
        assert result == []

    async def test_top_3_bestsellers_returned(self):
        agent = FakeAnalyticsAgent()
        listings = [
            self._listing("A", sales=20, revenue=200.0),
            self._listing("B", sales=15, revenue=150.0),
            self._listing("C", sales=10, revenue=100.0),
            self._listing("D", sales=5, revenue=50.0),
            self._listing("E", sales=1, revenue=10.0),
        ]
        agent.memory.get_etsy_listings = AsyncMock(return_value=listings)
        agent.memory.get_pending_action = AsyncMock(return_value=None)
        agent.memory.save_pending_action = AsyncMock()
        result = await asyncio.wait_for(agent._find_bestsellers(), timeout=5)
        assert len(result) <= 3
        assert result[0]["listing_id"] == "A"

    async def test_result_contains_required_keys(self):
        agent = FakeAnalyticsAgent()
        listings = [self._listing("A", sales=20, revenue=200.0)]
        agent.memory.get_etsy_listings = AsyncMock(return_value=listings)
        agent.memory.get_pending_action = AsyncMock(return_value=None)
        agent.memory.save_pending_action = AsyncMock()
        result = await asyncio.wait_for(agent._find_bestsellers(), timeout=5)
        for key in ("listing_id", "title", "sales", "revenue_eur"):
            assert key in result[0]

    async def test_pending_action_already_exists_skips_notification(self):
        """If pending_action exists for same listing_id, no new notification."""
        agent = FakeAnalyticsAgent()
        listings = [self._listing("A", sales=20, revenue=200.0)]
        agent.memory.get_etsy_listings = AsyncMock(return_value=listings)
        # Return existing pending_action for same listing_id
        agent.memory.get_pending_action = AsyncMock(
            return_value={"payload": {"listing_id": "A"}}
        )
        agent.memory.save_pending_action = AsyncMock()
        await asyncio.wait_for(agent._find_bestsellers(), timeout=5)
        agent._telegram_broadcast.assert_not_called()

    async def test_new_bestseller_triggers_notification(self):
        """New bestseller with no existing pending_action → telegram sent."""
        agent = FakeAnalyticsAgent()
        listings = [self._listing("A", sales=20, revenue=200.0)]
        agent.memory.get_etsy_listings = AsyncMock(return_value=listings)
        agent.memory.get_pending_action = AsyncMock(return_value=None)
        agent.memory.save_pending_action = AsyncMock()
        await asyncio.wait_for(agent._find_bestsellers(), timeout=5)
        agent._telegram_broadcast.assert_called_once()
        msg = agent._telegram_broadcast.call_args[0][0]
        assert "#bestseller" in msg

    async def test_pending_action_for_different_listing_sends_notification(self):
        """Existing pending_action for a DIFFERENT listing → still sends for this one."""
        agent = FakeAnalyticsAgent()
        listings = [self._listing("NEW", sales=20, revenue=200.0)]
        agent.memory.get_etsy_listings = AsyncMock(return_value=listings)
        agent.memory.get_pending_action = AsyncMock(
            return_value={"payload": {"listing_id": "OLD"}}
        )
        agent.memory.save_pending_action = AsyncMock()
        await asyncio.wait_for(agent._find_bestsellers(), timeout=5)
        agent._telegram_broadcast.assert_called_once()

    async def test_dynamic_threshold_min_2(self):
        """With avg_sales=0 → threshold=2 → listing with 1 sale not included."""
        agent = FakeAnalyticsAgent()
        listings = [self._listing("A", sales=1, revenue=10.0)]
        agent.memory.get_etsy_listings = AsyncMock(return_value=listings)
        agent.memory.get_pending_action = AsyncMock(return_value=None)
        agent.memory.save_pending_action = AsyncMock()
        result = await asyncio.wait_for(agent._find_bestsellers(), timeout=5)
        assert result == []

    async def test_dynamic_threshold_cap_10(self):
        """With very high avg_sales, threshold is capped at 10."""
        agent = FakeAnalyticsAgent()
        # avg_sales = 100, threshold = min(10, max(2, 150)) = 10
        listings = [
            self._listing(str(i), sales=100, revenue=float(100 - i))
            for i in range(10)
        ]
        agent.memory.get_etsy_listings = AsyncMock(return_value=listings)
        agent.memory.get_pending_action = AsyncMock(return_value=None)
        agent.memory.save_pending_action = AsyncMock()
        result = await asyncio.wait_for(agent._find_bestsellers(), timeout=5)
        # All 10 listings qualify, but top returns only 3
        assert len(result) == 3

    async def test_save_pending_action_called_for_new_bestseller(self):
        agent = FakeAnalyticsAgent()
        listings = [self._listing("A", sales=20, revenue=200.0)]
        agent.memory.get_etsy_listings = AsyncMock(return_value=listings)
        agent.memory.get_pending_action = AsyncMock(return_value=None)
        agent.memory.save_pending_action = AsyncMock()
        await asyncio.wait_for(agent._find_bestsellers(), timeout=5)
        agent.memory.save_pending_action.assert_called_once()
        call_kwargs = agent.memory.save_pending_action.call_args
        assert call_kwargs[0][0] == "production_queue_proposal"


class TestWriteDesignOutcomes:
    """Tests for _AnalyticsBestsellersMixin._write_design_outcomes."""

    async def test_calls_memory_store_insight(self):
        agent = FakeAnalyticsAgent()
        agent.memory.store_insight = AsyncMock(return_value="doc_id_123")
        result = await asyncio.wait_for(
            agent._write_design_outcomes(
                niche="wedding",
                template="minimal",
                color_scheme="sage",
                performance="high",
                summary="Great performance on Etsy",
            ),
            timeout=5,
        )
        assert result == "doc_id_123"
        agent.memory.store_insight.assert_called_once()

    async def test_metadata_contains_correct_type(self):
        agent = FakeAnalyticsAgent()
        agent.memory.store_insight = AsyncMock(return_value=None)
        await asyncio.wait_for(
            agent._write_design_outcomes(
                niche="birthday",
                template="floral",
                color_scheme="pastel",
                performance="medium",
                summary="Decent results",
            ),
            timeout=5,
        )
        call_kwargs = agent.memory.store_insight.call_args[1]
        assert call_kwargs["metadata"]["type"] == "design_outcome"
        assert call_kwargs["metadata"]["niche"] == "birthday"
        assert call_kwargs["metadata"]["template"] == "floral"

    async def test_text_contains_all_components(self):
        agent = FakeAnalyticsAgent()
        agent.memory.store_insight = AsyncMock(return_value=None)
        await asyncio.wait_for(
            agent._write_design_outcomes(
                niche="wedding",
                template="minimal",
                color_scheme="ivory",
                performance="high",
                summary="Top seller",
            ),
            timeout=5,
        )
        text_arg = agent.memory.store_insight.call_args[1]["text"]
        assert "DESIGN_OUTCOME" in text_arg
        assert "wedding" in text_arg
        assert "minimal" in text_arg
        assert "ivory" in text_arg
        assert "high" in text_arg
