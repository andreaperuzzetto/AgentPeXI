"""Coverage tests for the 5 agent assemblers.

Targets (all at 0% coverage before this file):
  - apps/backend/agents/analytics.py   (AnalyticsAgent)
  - apps/backend/agents/finance.py     (FinanceAgent)
  - apps/backend/agents/recall.py      (RecallAgent)
  - apps/backend/agents/remind.py      (RemindAgent)
  - apps/backend/agents/summarize.py   (SummarizeAgent)

MOCK CONTRACT
─────────────
memory      → AsyncMock()  (all await memory.X() work automatically)
client      → AsyncMock()  (anthropic.AsyncAnthropic stub)
etsy_api    → AsyncMock()  (AnalyticsAgent only)
_call_llm   → AsyncMock(return_value="<text>")   (patched on instance)
_call_llm_ollama → AsyncMock(return_value="<text>")

No real DB, no real LLM APIs, no Notion, no Telegram.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.backend.agents.analytics import AnalyticsAgent
from apps.backend.agents.finance import FinanceAgent
from apps.backend.agents.recall import RecallAgent
from apps.backend.agents.remind import RemindAgent
from apps.backend.agents.summarize import SummarizeAgent
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_task(agent_name: str, input_data: dict | None = None) -> AgentTask:
    return AgentTask(agent_name=agent_name, input_data=input_data or {})


def _async_memory() -> AsyncMock:
    """Fully-async MemoryManager mock. Every attribute is an awaitable AsyncMock."""
    mem = AsyncMock()
    mem.log_step = AsyncMock(return_value="step-1")
    return mem


# ═══════════════════════════════════════════════════════════════════════════════
# AnalyticsAgent
# ═══════════════════════════════════════════════════════════════════════════════


def _make_analytics_agent(memory=None, etsy_api=None) -> AnalyticsAgent:
    mem = memory if memory is not None else _async_memory()
    ea = etsy_api if etsy_api is not None else AsyncMock()
    return AnalyticsAgent(
        anthropic_client=AsyncMock(),
        memory=mem,
        etsy_api=ea,
    )


class TestAnalyticsAgentInit:
    def test_attributes_set(self):
        mem = _async_memory()
        ea = AsyncMock()
        pq = MagicMock()
        ll = MagicMock()
        agent = AnalyticsAgent(
            anthropic_client=AsyncMock(),
            memory=mem,
            etsy_api=ea,
            telegram_broadcaster=AsyncMock(),
            production_queue=pq,
            learning_loop=ll,
        )
        assert agent.name == "analytics"
        assert agent.etsy_api is ea
        assert agent.memory is mem
        assert agent._production_queue is pq
        assert agent._learning_loop is ll
        assert agent._remediation_log == {}

    def test_extra_init_kwargs_contains_etsy_api(self):
        agent = _make_analytics_agent()
        kw = agent._extra_init_kwargs()
        assert "etsy_api" in kw
        assert kw["etsy_api"] is agent.etsy_api


class TestAnalyticsAgentRun:
    async def test_run_empty_listings(self):
        agent = _make_analytics_agent()
        agent.memory.get_etsy_listings = AsyncMock(return_value=[])
        result = await agent.run(_make_task("analytics"))
        assert result.status == TaskStatus.COMPLETED
        assert "Nessun listing" in result.output_data.get("message", "")

    async def test_run_only_archived_listings(self):
        agent = _make_analytics_agent()
        agent.memory.get_etsy_listings = AsyncMock(
            return_value=[{"listing_id": 1, "status": "archived"}]
        )
        result = await agent.run(_make_task("analytics"))
        assert result.status == TaskStatus.COMPLETED
        assert "Nessun listing" in result.output_data.get("message", "")

    async def test_run_removed_listing_excluded(self):
        agent = _make_analytics_agent()
        agent.memory.get_etsy_listings = AsyncMock(
            return_value=[{"listing_id": 2, "status": "removed"}]
        )
        result = await agent.run(_make_task("analytics"))
        assert result.status == TaskStatus.COMPLETED

    async def test_run_happy_path(self):
        agent = _make_analytics_agent()
        listing = {"listing_id": 42, "status": "active", "sales": 0}
        agent.memory.get_etsy_listings = AsyncMock(return_value=[listing])
        agent.memory.get_listings_no_views_no_sales = AsyncMock(return_value=[])
        agent.memory.get_listings_no_conversion = AsyncMock(return_value=[])
        agent.memory.get_listings_no_views = AsyncMock(return_value=[])
        agent.memory.update_etsy_listing_stats = AsyncMock()
        agent.memory.store_insight = AsyncMock()
        agent._call_tool = AsyncMock(
            return_value={
                "views": 10,
                "num_favorers": 5,
                "state": "active",
                "price": {"amount": 1000},
                "shop_id": "S1",
            }
        )
        agent._get_listing_sales = AsyncMock(return_value=3)
        agent._find_bestsellers = AsyncMock(return_value=[])
        agent._build_report = AsyncMock(return_value={"synced": 1})
        agent._send_daily_summary = AsyncMock()
        agent._calculate_analytics_confidence = MagicMock(return_value=(0.9, []))
        agent._analyze_no_views_no_sales = AsyncMock()
        agent._analyze_no_conversion = AsyncMock()
        agent._analyze_no_views = AsyncMock()

        result = await agent.run(_make_task("analytics"))

        assert result.status == TaskStatus.COMPLETED
        agent._find_bestsellers.assert_called_once()
        agent._build_report.assert_called_once()
        agent.memory.store_insight.assert_called_once()

    async def test_run_partial_when_low_confidence(self):
        agent = _make_analytics_agent()
        listing = {"listing_id": 7, "status": "active", "sales": 0}
        agent.memory.get_etsy_listings = AsyncMock(return_value=[listing])
        agent.memory.get_listings_no_views_no_sales = AsyncMock(return_value=[])
        agent.memory.get_listings_no_conversion = AsyncMock(return_value=[])
        agent.memory.get_listings_no_views = AsyncMock(return_value=[])
        agent.memory.store_insight = AsyncMock()
        agent._call_tool = AsyncMock(return_value={
            "views": 0, "num_favorers": 0, "state": "active", "price": 0, "shop_id": "S1",
        })
        agent._get_listing_sales = AsyncMock(return_value=None)
        agent._find_bestsellers = AsyncMock(return_value=[])
        agent._build_report = AsyncMock(return_value={})
        agent._send_daily_summary = AsyncMock()
        agent._calculate_analytics_confidence = MagicMock(return_value=(0.3, ["low_data"]))

        result = await agent.run(_make_task("analytics"))
        assert result.status == TaskStatus.PARTIAL

    async def test_run_sync_exception_handled(self):
        """_call_tool raises → _sync_one catches it → synced=[] → run still returns."""
        agent = _make_analytics_agent()
        listing = {"listing_id": 99, "status": "active", "sales": 0}
        agent.memory.get_etsy_listings = AsyncMock(return_value=[listing])
        agent.memory.get_listings_no_views_no_sales = AsyncMock(return_value=[])
        agent.memory.get_listings_no_conversion = AsyncMock(return_value=[])
        agent.memory.get_listings_no_views = AsyncMock(return_value=[])
        agent.memory.store_insight = AsyncMock()
        agent._call_tool = AsyncMock(side_effect=RuntimeError("etsy down"))
        agent._find_bestsellers = AsyncMock(return_value=[])
        agent._build_report = AsyncMock(return_value={})
        agent._send_daily_summary = AsyncMock()
        agent._calculate_analytics_confidence = MagicMock(return_value=(0.5, ["some_miss"]))

        result = await agent.run(_make_task("analytics"))
        assert result.status in (TaskStatus.COMPLETED, TaskStatus.PARTIAL)

    async def test_run_with_failure_items(self):
        """Listings returned by failure queries are dispatched through _analyze_with_sem."""
        agent = _make_analytics_agent()
        bad = {"listing_id": 10, "status": "active", "sales": 0}
        agent.memory.get_etsy_listings = AsyncMock(return_value=[bad])
        agent.memory.get_listings_no_views_no_sales = AsyncMock(return_value=[bad])
        agent.memory.get_listings_no_conversion = AsyncMock(return_value=[])
        agent.memory.get_listings_no_views = AsyncMock(return_value=[])
        agent.memory.update_etsy_listing_stats = AsyncMock()
        agent.memory.store_insight = AsyncMock()
        agent._call_tool = AsyncMock(return_value={
            "views": 0, "num_favorers": 0, "state": "active", "price": 0, "shop_id": "S1",
        })
        agent._get_listing_sales = AsyncMock(return_value=0)
        agent._analyze_no_views_no_sales = AsyncMock()
        agent._analyze_no_conversion = AsyncMock()
        agent._analyze_no_views = AsyncMock()
        agent._find_bestsellers = AsyncMock(return_value=[])
        agent._build_report = AsyncMock(return_value={})
        agent._send_daily_summary = AsyncMock()
        agent._calculate_analytics_confidence = MagicMock(return_value=(0.7, []))

        result = await agent.run(_make_task("analytics"))
        assert result.status in (TaskStatus.COMPLETED, TaskStatus.PARTIAL)
        agent._analyze_no_views_no_sales.assert_called_once_with(bad)

    async def test_run_voice_reply_singular(self):
        """1 listing synced → singular label in reply_voice."""
        agent = _make_analytics_agent()
        listing = {"listing_id": 1, "status": "active", "sales": 0}
        agent.memory.get_etsy_listings = AsyncMock(return_value=[listing])
        agent.memory.get_listings_no_views_no_sales = AsyncMock(return_value=[])
        agent.memory.get_listings_no_conversion = AsyncMock(return_value=[])
        agent.memory.get_listings_no_views = AsyncMock(return_value=[])
        agent.memory.update_etsy_listing_stats = AsyncMock()
        agent.memory.store_insight = AsyncMock()
        agent._call_tool = AsyncMock(return_value={
            "views": 1, "num_favorers": 0, "state": "active", "price": 500, "shop_id": "S1",
        })
        agent._get_listing_sales = AsyncMock(return_value=1)
        agent._find_bestsellers = AsyncMock(return_value=[])
        agent._build_report = AsyncMock(return_value={})
        agent._send_daily_summary = AsyncMock()
        agent._calculate_analytics_confidence = MagicMock(return_value=(0.9, []))

        result = await agent.run(_make_task("analytics"))
        assert "sincronizzato" in result.reply_voice  # singular form


class TestGetListingSales:
    async def test_returns_sum_from_dict(self):
        agent = _make_analytics_agent()
        agent._call_tool = AsyncMock(
            return_value={"results": [{"quantity": 2}, {"quantity": 3}]}
        )
        assert await agent._get_listing_sales("L1", "S1") == 5

    async def test_returns_sum_from_list(self):
        agent = _make_analytics_agent()
        agent._call_tool = AsyncMock(return_value=[{"quantity": 1}, {"quantity": 1}])
        assert await agent._get_listing_sales("L1", "S1") == 2

    async def test_returns_zero_for_empty_results(self):
        agent = _make_analytics_agent()
        agent._call_tool = AsyncMock(return_value={"results": []})
        assert await agent._get_listing_sales("L1", "S1") == 0

    async def test_returns_none_on_exception(self):
        agent = _make_analytics_agent()
        agent._call_tool = AsyncMock(side_effect=Exception("API error"))
        assert await agent._get_listing_sales("L1", "S1") is None

    async def test_unknown_result_type_returns_zero(self):
        """Non-dict non-list result → results=[] → sum=0."""
        agent = _make_analytics_agent()
        agent._call_tool = AsyncMock(return_value="unexpected string")
        assert await agent._get_listing_sales("L1", "S1") == 0

    async def test_quantity_defaults_to_one(self):
        """Missing 'quantity' key defaults to 1."""
        agent = _make_analytics_agent()
        agent._call_tool = AsyncMock(
            return_value={"results": [{"no_qty": True}, {"no_qty": True}]}
        )
        assert await agent._get_listing_sales("L1", "S1") == 2


# ═══════════════════════════════════════════════════════════════════════════════
# FinanceAgent
# ═══════════════════════════════════════════════════════════════════════════════


def _make_finance_agent(memory=None) -> FinanceAgent:
    mem = memory if memory is not None else _async_memory()
    return FinanceAgent(anthropic_client=AsyncMock(), memory=mem)


def _setup_finance_agent() -> FinanceAgent:
    """Return a FinanceAgent with all mixin methods pre-mocked for run() calls."""
    agent = _make_finance_agent()
    agent.memory.get_cost_breakdown = AsyncMock(
        return_value={"total": 0.5, "per_agent": {"analytics": 0.1}}
    )
    agent.memory.get_revenue_stats = AsyncMock(
        return_value={
            "total_revenue_eur": 100.0,
            "total_sales": 10,
            "active_count": 5,
            "avg_price_eur": 10.0,
        }
    )
    agent.memory.get_model_cost_breakdown = AsyncMock(return_value=[])
    agent.memory.store_insight = AsyncMock()
    agent._compute_niche_roi = AsyncMock(return_value=[])
    agent._compute_product_type_roi = AsyncMock(return_value=[])
    agent._compute_trend = AsyncMock(
        return_value={
            "revenue_7d": 10.0,
            "revenue_30d": 35.0,
            "revenue_7d_normalized_30d": 42.86,
            "revenue_delta_pct": 10.0,
        }
    )
    agent._generate_cost_insights = AsyncMock(return_value={})
    agent._read_learning_context = AsyncMock(
        return_value={
            "design_winners": [],
            "failure_rate": 0.0,
            "failure_count": 0,
            "success_count": 0,
        }
    )
    agent._generate_roi_analysis = AsyncMock(
        return_value={
            "top_niches_to_scale": [],
            "niches_to_abandon": [],
            "strategic_recommendation": "keep going",
        }
    )
    agent._check_budget_alert = AsyncMock(return_value=False)
    agent._build_report = MagicMock(return_value={"period_days": 30})
    agent._send_finance_summary = AsyncMock()
    agent._calculate_finance_confidence = MagicMock(return_value=(0.9, []))
    return agent


class TestFinanceAgentInit:
    def test_attributes_set(self):
        mem = _async_memory()
        tg = AsyncMock()
        agent = FinanceAgent(
            anthropic_client=AsyncMock(),
            memory=mem,
            telegram_broadcaster=tg,
        )
        assert agent.name == "finance"
        assert agent.memory is mem
        assert agent._telegram_broadcast is tg

    def test_no_telegram(self):
        agent = _make_finance_agent()
        assert agent._telegram_broadcast is None


class TestFinanceAgentRun:
    async def test_run_happy_path(self):
        agent = _setup_finance_agent()
        result = await agent.run(_make_task("finance", {"period_days": 30}))
        assert result.status == TaskStatus.COMPLETED
        agent._generate_roi_analysis.assert_called_once()
        agent._build_report.assert_called_once()

    async def test_run_default_period(self):
        agent = _setup_finance_agent()
        result = await agent.run(_make_task("finance"))
        assert result.status in (TaskStatus.COMPLETED, TaskStatus.PARTIAL)

    async def test_run_partial_on_low_confidence(self):
        agent = _setup_finance_agent()
        agent._calculate_finance_confidence = MagicMock(return_value=(0.3, ["no_data"]))
        result = await agent.run(_make_task("finance"))
        assert result.status == TaskStatus.PARTIAL

    async def test_run_with_niches_stores_snapshots(self):
        """niche_roi with data → store_insight called for snapshots + directive."""
        agent = _setup_finance_agent()
        agent._compute_niche_roi = AsyncMock(
            return_value=[
                {
                    "niche": "wedding",
                    "listing_count": 5,
                    "total_sales": 3,
                    "net_margin_eur": 20.0,
                    "avg_price_eur": 10.0,
                    "break_even_units": 2,
                    "cost_per_listing_eur": 0.01,
                    "roi_pct": 50.0,
                }
            ]
        )
        agent._generate_roi_analysis = AsyncMock(
            return_value={
                "top_niches_to_scale": [{"niche": "wedding"}],
                "niches_to_abandon": [{"niche": "xmas"}],
                "strategic_recommendation": "scale wedding",
            }
        )
        result = await agent.run(_make_task("finance"))
        assert result.status in (TaskStatus.COMPLETED, TaskStatus.PARTIAL)
        # niche_roi_snapshot + finance_insight + directive + report
        assert agent.memory.store_insight.call_count >= 3

    async def test_run_niche_without_name_skipped(self):
        """Niche entry with no 'niche' key is silently skipped."""
        agent = _setup_finance_agent()
        agent._compute_niche_roi = AsyncMock(
            return_value=[{"niche": "", "listing_count": 0, "total_sales": 0,
                           "net_margin_eur": 0.0, "avg_price_eur": 0.0,
                           "break_even_units": 0, "cost_per_listing_eur": 0.0, "roi_pct": 0.0}]
        )
        result = await agent.run(_make_task("finance"))
        # Empty niche skipped — no extra store_insight beyond final report
        assert result.status in (TaskStatus.COMPLETED, TaskStatus.PARTIAL)

    async def test_run_directive_only_with_scale(self):
        """niches_to_scale non-empty → directive stored even without niches_to_abandon."""
        agent = _setup_finance_agent()
        agent._generate_roi_analysis = AsyncMock(
            return_value={
                "top_niches_to_scale": [{"niche": "prints"}],
                "niches_to_abandon": [],
                "strategic_recommendation": "more prints",
            }
        )
        result = await agent.run(_make_task("finance"))
        assert result.status in (TaskStatus.COMPLETED, TaskStatus.PARTIAL)
        assert agent.memory.store_insight.call_count >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# RecallAgent — static helpers
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecallBuildTimeFilter:
    def test_both_none_returns_none(self):
        assert RecallAgent._build_time_filter(None, None) is None

    def test_only_from(self):
        assert RecallAgent._build_time_filter("2025-01-01", None) == {
            "timestamp": {"$gte": "2025-01-01"}
        }

    def test_only_to(self):
        assert RecallAgent._build_time_filter(None, "2025-12-31") == {
            "timestamp": {"$lte": "2025-12-31"}
        }

    def test_both_set_returns_and(self):
        result = RecallAgent._build_time_filter("2025-01-01", "2025-12-31")
        assert result == {
            "$and": [
                {"timestamp": {"$gte": "2025-01-01"}},
                {"timestamp": {"$lte": "2025-12-31"}},
            ]
        }


class TestRecallGroupByApp:
    def test_empty_list(self):
        assert RecallAgent._group_by_app([]) == {}

    def test_groups_by_app_name(self):
        chunks = [
            {"metadata": {"app_name": "Safari", "timestamp": "2025-01-01T10:00:00"}, "document": "a"},
            {"metadata": {"app_name": "VSCode", "timestamp": "2025-01-01T11:00:00"}, "document": "b"},
            {"metadata": {"app_name": "Safari", "timestamp": "2025-01-01T09:00:00"}, "document": "c"},
        ]
        result = RecallAgent._group_by_app(chunks)
        assert set(result.keys()) == {"Safari", "VSCode"}
        assert len(result["Safari"]) == 2
        # Sorted by timestamp ascending: 09:00 before 10:00
        assert result["Safari"][0]["document"] == "c"

    def test_missing_app_uses_sconosciuta(self):
        chunk = {"metadata": {}, "document": "x"}
        result = RecallAgent._group_by_app([chunk])
        assert "Sconosciuta" in result


class TestRecallBuildContext:
    def test_empty_dict(self):
        assert RecallAgent._build_context({}) == ""

    def test_single_app(self):
        grouped = {
            "Safari": [
                {"metadata": {"timestamp": "2025-01-01T10:00:00"}, "document": "web content"},
            ]
        }
        ctx = RecallAgent._build_context(grouped)
        assert "Safari" in ctx
        assert "web content" in ctx

    def test_invalid_timestamp_handled(self):
        grouped = {"App": [{"metadata": {"timestamp": "not-a-date"}, "document": "text"}]}
        ctx = RecallAgent._build_context(grouped)
        assert "App" in ctx
        assert "text" in ctx

    def test_no_timestamp(self):
        grouped = {"App": [{"metadata": {}, "document": "content"}]}
        ctx = RecallAgent._build_context(grouped)
        assert "content" in ctx

    def test_multiple_apps_separator(self):
        grouped = {
            "A": [{"metadata": {}, "document": "doc_a"}],
            "B": [{"metadata": {}, "document": "doc_b"}],
        }
        ctx = RecallAgent._build_context(grouped)
        assert "doc_a" in ctx
        assert "doc_b" in ctx
        assert "---" in ctx

    def test_chunks_capped_at_five(self):
        """Only first 5 chunks per app are included."""
        chunks = [{"metadata": {}, "document": f"doc{i}"} for i in range(10)]
        grouped = {"App": chunks}
        ctx = RecallAgent._build_context(grouped)
        assert "doc4" in ctx
        assert "doc5" not in ctx


# ─────────────────────────────────────────────────────────────────────────────
# RecallAgent — async methods
# ─────────────────────────────────────────────────────────────────────────────


def _make_recall_agent(memory=None) -> RecallAgent:
    mem = memory if memory is not None else _async_memory()
    return RecallAgent(anthropic_client=AsyncMock(), memory=mem)


class TestRecallMultiSearch:
    async def test_aggregates_all_sources(self):
        agent = _make_recall_agent()
        agent.memory.search_screen_memory = AsyncMock(
            return_value=[{"metadata": {}, "document": "screen doc"}]
        )
        agent.memory.query_insights = AsyncMock(
            return_value=[{"metadata": {}, "document": "insight doc"}]
        )
        agent.memory.query_personal_memory = AsyncMock(return_value=[])
        results = await agent._multi_search("query", None)
        assert len(results) == 2

    async def test_screen_exception_skipped(self):
        agent = _make_recall_agent()
        agent.memory.search_screen_memory = AsyncMock(side_effect=Exception("DB error"))
        agent.memory.query_insights = AsyncMock(
            return_value=[{"metadata": {}, "document": "insight"}]
        )
        agent.memory.query_personal_memory = AsyncMock(return_value=[])
        results = await agent._multi_search("q", None)
        assert len(results) == 1

    async def test_all_sources_exception_returns_empty(self):
        agent = _make_recall_agent()
        agent.memory.search_screen_memory = AsyncMock(side_effect=Exception("err"))
        agent.memory.query_insights = AsyncMock(side_effect=Exception("err"))
        agent.memory.query_personal_memory = AsyncMock(side_effect=Exception("err"))
        results = await agent._multi_search("q", None)
        assert results == []

    async def test_source_type_tags_added(self):
        agent = _make_recall_agent()
        agent.memory.search_screen_memory = AsyncMock(
            return_value=[{"metadata": {}, "document": "s"}]
        )
        agent.memory.query_insights = AsyncMock(
            return_value=[{"metadata": {}, "document": "i"}]
        )
        agent.memory.query_personal_memory = AsyncMock(
            return_value=[{"metadata": {}, "document": "p"}]
        )
        results = await agent._multi_search("q", None)
        types = {r["metadata"]["source_type"] for r in results}
        assert "screen" in types
        assert "notes" in types
        assert "recall_synthesis" in types


class TestRecallGradeChunks:
    async def test_empty_chunks_returns_empty(self):
        agent = _make_recall_agent()
        assert await agent._grade_chunks("q", []) == []

    async def test_relevant_chunk_kept(self):
        agent = _make_recall_agent()
        agent._call_llm_ollama = AsyncMock(return_value="RELEVANT")
        result = await agent._grade_chunks("q", [{"document": "important content"}])
        assert len(result) == 1

    async def test_irrelevant_chunk_discarded(self):
        agent = _make_recall_agent()
        agent._call_llm_ollama = AsyncMock(return_value="IRRELEVANT")
        result = await agent._grade_chunks("q", [{"document": "noise"}])
        assert result == []

    async def test_empty_document_skipped(self):
        agent = _make_recall_agent()
        result = await agent._grade_chunks("q", [{"document": "   "}])
        assert result == []

    async def test_llm_exception_includes_chunk_as_fallback(self):
        """On LLM error the chunk is kept (better too much than too little)."""
        agent = _make_recall_agent()
        agent._call_llm_ollama = AsyncMock(side_effect=Exception("LLM error"))
        result = await agent._grade_chunks("q", [{"document": "some content"}])
        assert len(result) == 1

    async def test_mixed_relevance(self):
        agent = _make_recall_agent()
        agent._call_llm_ollama = AsyncMock(side_effect=["RELEVANT", "IRRELEVANT", "RELEVANT"])
        chunks = [{"document": f"d{i}"} for i in range(3)]
        result = await agent._grade_chunks("q", chunks)
        assert len(result) == 2


class TestRecallRewriteQuery:
    async def test_returns_stripped_result(self):
        agent = _make_recall_agent()
        agent._call_llm_ollama = AsyncMock(return_value="  new query  ")
        assert await agent._rewrite_query("old") == "new query"

    async def test_returns_original_on_exception(self):
        agent = _make_recall_agent()
        agent._call_llm_ollama = AsyncMock(side_effect=Exception("fail"))
        assert await agent._rewrite_query("original") == "original"

    async def test_hint_appended_to_prompt(self):
        agent = _make_recall_agent()
        agent._call_llm_ollama = AsyncMock(return_value="query with hint")
        result = await agent._rewrite_query("original", hint="last_app=Safari")
        assert result == "query with hint"


class TestRecallRun:
    async def test_empty_query_returns_failed(self):
        agent = _make_recall_agent()
        result = await agent.run(_make_task("recall", {"query": ""}))
        assert result.status == TaskStatus.FAILED
        assert "query" in result.output_data.get("error", "").lower()

    async def test_missing_query_returns_failed(self):
        agent = _make_recall_agent()
        result = await agent.run(_make_task("recall", {}))
        assert result.status == TaskStatus.FAILED

    async def test_happy_path(self):
        agent = _make_recall_agent()
        chunk = {
            "document": "found it",
            "metadata": {"app_name": "Safari", "timestamp": "2025-01-01T10:00:00",
                         "source_type": "screen"},
        }
        # Return >= 3 chunks to avoid rewrite trigger
        agent._multi_search = AsyncMock(return_value=[chunk] * 3)
        agent._grade_chunks = AsyncMock(return_value=[chunk] * 3)
        agent._synthesize = AsyncMock(return_value="Synthesis result.")
        agent._check_stop = AsyncMock(return_value=True)
        agent._store_recall_insight = AsyncMock()

        result = await agent.run(_make_task("recall", {"query": "what did I do?"}))
        assert result.status == TaskStatus.COMPLETED
        assert "Synthesis result." in result.output_data.get("response", "")
        assert result.output_data["results_found"] == 3

    async def test_no_relevant_after_retry_returns_empty_response(self):
        agent = _make_recall_agent()
        agent._multi_search = AsyncMock(return_value=[])
        agent._grade_chunks = AsyncMock(return_value=[])
        agent._rewrite_query = AsyncMock(return_value="same query")  # same → no extra search

        result = await agent.run(_make_task("recall", {"query": "unknown topic"}))
        assert result.status == TaskStatus.COMPLETED
        assert "Non ho trovato" in result.output_data.get("response", "")

    async def test_query_rewrite_triggered_when_few_relevant(self):
        """< _MIN_RELEVANT (3) relevant chunks → rewrite + retry."""
        agent = _make_recall_agent()
        _make_chunk = lambda doc: {
            "document": doc,
            "metadata": {"app_name": "App", "timestamp": "", "source_type": "screen"},
        }
        few = [_make_chunk("chunk1"), _make_chunk("chunk2")]  # 2 < 3
        extra = [_make_chunk("extra")]

        call_count = {"n": 0}

        async def _multi_mock(q, f, n_screen=15, n_pepe=5, n_personal=3):
            call_count["n"] += 1
            return few if call_count["n"] == 1 else extra

        agent._multi_search = _multi_mock
        agent._grade_chunks = AsyncMock(side_effect=[few, extra])
        agent._rewrite_query = AsyncMock(return_value="rewritten query")
        agent._synthesize = AsyncMock(return_value="Combined synthesis.")
        agent._check_stop = AsyncMock(return_value=True)
        agent._store_recall_insight = AsyncMock()

        result = await agent.run(_make_task("recall", {"query": "some query"}))
        assert result.status == TaskStatus.COMPLETED
        agent._rewrite_query.assert_called_once()

    async def test_rewrite_same_query_no_extra_search(self):
        """If rewrite returns the same query, no extra _multi_search call."""
        agent = _make_recall_agent()
        _make_chunk = lambda doc: {
            "document": doc,
            "metadata": {"app_name": "App", "timestamp": "", "source_type": "screen"},
        }
        few = [_make_chunk("x")]  # 1 < 3

        agent._multi_search = AsyncMock(return_value=few)
        agent._grade_chunks = AsyncMock(return_value=few)
        agent._rewrite_query = AsyncMock(return_value="some query")  # identical
        agent._synthesize = AsyncMock(return_value="Partial.")
        agent._check_stop = AsyncMock(return_value=True)
        agent._store_recall_insight = AsyncMock()

        await agent.run(_make_task("recall", {"query": "some query"}))
        # _multi_search called only once (initial); no extra call since query unchanged
        assert agent._multi_search.call_count == 1

    async def test_stop_false_triggers_supplement(self):
        """_check_stop returns False → supplemental search + _synthesize_integrated."""
        agent = _make_recall_agent()
        _make_chunk = lambda doc: {
            "document": doc,
            "metadata": {"app_name": "App", "timestamp": "", "source_type": "screen"},
        }
        initial = [_make_chunk(f"c{i}") for i in range(4)]
        supp = [_make_chunk("extra")]

        call_count = {"n": 0}

        async def _multi_mock(q, f, n_screen=15, n_pepe=5, n_personal=3):
            call_count["n"] += 1
            return initial if call_count["n"] == 1 else supp

        agent._multi_search = _multi_mock
        agent._grade_chunks = AsyncMock(side_effect=[initial, supp])
        agent._synthesize = AsyncMock(return_value="Partial answer.")
        agent._synthesize_integrated = AsyncMock(return_value="Full answer.")
        agent._check_stop = AsyncMock(return_value=False)
        agent._store_recall_insight = AsyncMock()

        result = await agent.run(_make_task("recall", {"query": "complex question"}))
        assert result.status == TaskStatus.COMPLETED
        agent._synthesize_integrated.assert_called_once()
        assert "Full answer." in result.output_data.get("response", "")

    async def test_stop_false_no_supplement_chunks(self):
        """_check_stop returns False but supplement search returns empty → no integrate."""
        agent = _make_recall_agent()
        chunk = {"document": "d", "metadata": {"app_name": "A", "timestamp": "", "source_type": "screen"}}
        initial = [chunk] * 4

        agent._multi_search = AsyncMock(side_effect=[initial, []])
        agent._grade_chunks = AsyncMock(side_effect=[initial, []])
        agent._synthesize = AsyncMock(return_value="Answer.")
        agent._synthesize_integrated = AsyncMock(return_value="Should not be called.")
        agent._check_stop = AsyncMock(return_value=False)
        agent._store_recall_insight = AsyncMock()

        result = await agent.run(_make_task("recall", {"query": "q"}))
        assert result.status == TaskStatus.COMPLETED
        agent._synthesize_integrated.assert_not_called()

    async def test_time_filter_passed_to_multi_search(self):
        """time_from/time_to in input → time_filter built and forwarded."""
        agent = _make_recall_agent()
        chunk = {"document": "d", "metadata": {"app_name": "A", "timestamp": "2025-01-01T10:00:00", "source_type": "screen"}}
        agent._multi_search = AsyncMock(return_value=[chunk] * 3)
        agent._grade_chunks = AsyncMock(return_value=[chunk] * 3)
        agent._synthesize = AsyncMock(return_value="ok")
        agent._check_stop = AsyncMock(return_value=True)
        agent._store_recall_insight = AsyncMock()

        await agent.run(_make_task("recall", {
            "query": "what?",
            "time_from": "2025-01-01T00:00:00",
            "time_to": "2025-01-31T23:59:59",
        }))
        # Verify the time_filter was built correctly (not None)
        call_args = agent._multi_search.call_args
        passed_filter = call_args[0][1]  # second positional arg
        assert passed_filter is not None
        assert "$and" in passed_filter


# ═══════════════════════════════════════════════════════════════════════════════
# RemindAgent
# ═══════════════════════════════════════════════════════════════════════════════


def _make_remind_agent(memory=None) -> RemindAgent:
    mem = memory if memory is not None else _async_memory()
    agent = RemindAgent(anthropic_client=AsyncMock(), memory=mem)
    agent._log_step = AsyncMock()
    agent._ensure_notion = AsyncMock()
    return agent


class TestRemindFail:
    def test_fail_returns_failed_status(self):
        agent = _make_remind_agent()
        result = agent._fail("bad input")
        assert result.status == TaskStatus.FAILED
        assert result.output_data["error"] == "bad input"
        assert result.agent_name == "remind"

    def test_fail_uses_task_id(self):
        agent = _make_remind_agent()
        agent._task_id = "tid-123"
        result = agent._fail("oops")
        assert result.task_id == "tid-123"


class TestExtractReminderJson:
    async def test_valid_json_returned(self):
        agent = _make_remind_agent()
        agent._call_llm = AsyncMock(
            return_value='{"text": "call Mario", "recurring": null}'
        )
        result = await agent._extract_reminder_json("call Mario tomorrow")
        assert result is not None
        assert result["text"] == "call Mario"

    async def test_json_with_preamble_extracted(self):
        agent = _make_remind_agent()
        agent._call_llm = AsyncMock(
            return_value='Sure! Here is: {"text": "go to gym", "recurring": "daily"}'
        )
        result = await agent._extract_reminder_json("gym every day")
        assert result is not None
        assert result["recurring"] == "daily"

    async def test_no_json_in_response_returns_none(self):
        agent = _make_remind_agent()
        agent._call_llm = AsyncMock(return_value="not json at all")
        assert await agent._extract_reminder_json("reminder text") is None

    async def test_invalid_json_returns_none(self):
        agent = _make_remind_agent()
        agent._call_llm = AsyncMock(return_value="{broken json")
        assert await agent._extract_reminder_json("text") is None

    async def test_exception_returns_none(self):
        agent = _make_remind_agent()
        agent._call_llm = AsyncMock(side_effect=Exception("LLM error"))
        assert await agent._extract_reminder_json("text") is None

    async def test_json_without_required_keys_returns_none(self):
        agent = _make_remind_agent()
        agent._call_llm = AsyncMock(return_value='{"foo": "bar"}')
        assert await agent._extract_reminder_json("text") is None


class TestCheckDuplicate:
    async def test_no_pending_returns_none(self):
        agent = _make_remind_agent()
        agent.memory.get_pending_reminders = AsyncMock(return_value=[])
        trigger = datetime.now() + timedelta(hours=2)
        assert await agent._check_duplicate("call Mario", trigger) is None

    async def test_duplicate_within_window_returned(self):
        agent = _make_remind_agent()
        trigger = datetime.now() + timedelta(hours=2)
        existing = {
            "id": 1,
            "text": "call Mario back",
            "trigger_at": (trigger + timedelta(minutes=30)).isoformat(),
            "status": "pending",
        }
        agent.memory.get_pending_reminders = AsyncMock(return_value=[existing])
        result = await agent._check_duplicate("call Mario", trigger)
        assert result is not None
        assert result["id"] == 1

    async def test_no_duplicate_outside_window(self):
        agent = _make_remind_agent()
        trigger = datetime.now() + timedelta(hours=2)
        existing = {
            "id": 1,
            "text": "call Mario",
            "trigger_at": (trigger + timedelta(hours=3)).isoformat(),  # > +1h
        }
        agent.memory.get_pending_reminders = AsyncMock(return_value=[existing])
        assert await agent._check_duplicate("call Mario", trigger) is None

    async def test_no_duplicate_different_text(self):
        agent = _make_remind_agent()
        trigger = datetime.now() + timedelta(hours=2)
        existing = {
            "id": 1,
            "text": "buy groceries",
            "trigger_at": (trigger + timedelta(minutes=10)).isoformat(),
        }
        agent.memory.get_pending_reminders = AsyncMock(return_value=[existing])
        assert await agent._check_duplicate("call Mario", trigger) is None

    async def test_exception_returns_none(self):
        agent = _make_remind_agent()
        agent.memory.get_pending_reminders = AsyncMock(side_effect=Exception("DB fail"))
        trigger = datetime.now() + timedelta(hours=1)
        assert await agent._check_duplicate("text", trigger) is None

    async def test_invalid_trigger_at_in_pending_skipped(self):
        agent = _make_remind_agent()
        trigger = datetime.now() + timedelta(hours=2)
        existing = {"id": 1, "text": "call Mario", "trigger_at": "invalid-date"}
        agent.memory.get_pending_reminders = AsyncMock(return_value=[existing])
        assert await agent._check_duplicate("call Mario", trigger) is None


class TestRemindRun:
    async def test_action_list_empty(self):
        agent = _make_remind_agent()
        agent.memory.get_pending_reminders = AsyncMock(return_value=[])
        agent.memory.get_sent_unacknowledged = AsyncMock(return_value=[])
        result = await agent.run(_make_task("remind", {"action": "list"}))
        assert result.status == TaskStatus.COMPLETED
        assert "Nessun reminder" in result.output_data.get("reply", "")

    async def test_action_list_with_items(self):
        agent = _make_remind_agent()
        future = (datetime.now() + timedelta(hours=2)).isoformat()
        reminders = [{"id": 1, "text": "call Mario", "trigger_at": future, "status": "pending"}]
        agent.memory.get_pending_reminders = AsyncMock(return_value=reminders)
        agent.memory.get_sent_unacknowledged = AsyncMock(return_value=[])
        result = await agent.run(_make_task("remind", {"action": "list"}))
        assert result.status == TaskStatus.COMPLETED
        assert len(result.output_data.get("reminders", [])) == 1

    async def test_action_list_with_sent_unacked(self):
        agent = _make_remind_agent()
        future = (datetime.now() + timedelta(hours=2)).isoformat()
        sent = [{"id": 2, "text": "take pills", "trigger_at": future, "status": "sent",
                 "recurring_rule": "daily"}]
        agent.memory.get_pending_reminders = AsyncMock(return_value=[])
        agent.memory.get_sent_unacknowledged = AsyncMock(return_value=sent)
        result = await agent.run(_make_task("remind", {"action": "list"}))
        assert result.status == TaskStatus.COMPLETED
        assert len(result.output_data.get("reminders", [])) == 1

    async def test_action_cancel_missing_id(self):
        agent = _make_remind_agent()
        result = await agent.run(_make_task("remind", {"action": "cancel"}))
        assert result.status == TaskStatus.FAILED

    async def test_action_cancel_happy_path(self):
        agent = _make_remind_agent()
        agent.memory.cancel_reminder = AsyncMock()
        agent.memory.get_reminder_notion_id_by_id = AsyncMock(return_value=None)
        result = await agent.run(_make_task("remind", {"action": "cancel", "reminder_id": 5}))
        assert result.status == TaskStatus.COMPLETED
        assert result.output_data["reminder_id"] == 5

    async def test_action_cancel_with_notion(self):
        """If _notion is set and notion_id found → update_status called."""
        agent = _make_remind_agent()
        notion_mock = AsyncMock()
        agent._notion = notion_mock
        agent.memory.cancel_reminder = AsyncMock()
        agent.memory.get_reminder_notion_id_by_id = AsyncMock(return_value="notion-page-1")
        result = await agent.run(_make_task("remind", {"action": "cancel", "reminder_id": 3}))
        assert result.status == TaskStatus.COMPLETED
        notion_mock.update_status.assert_called_once_with("notion-page-1", "Cancelled")

    async def test_action_ack_missing_msg_id(self):
        agent = _make_remind_agent()
        result = await agent.run(_make_task("remind", {"action": "ack"}))
        assert result.status == TaskStatus.FAILED

    async def test_action_ack_happy_path(self):
        agent = _make_remind_agent()
        agent.memory.acknowledge_reminder = AsyncMock(return_value=True)
        agent.memory.get_reminder_notion_id = AsyncMock(return_value=None)
        result = await agent.run(_make_task("remind", {"action": "ack", "telegram_msg_id": 77}))
        assert result.status == TaskStatus.COMPLETED

    async def test_action_ack_not_found(self):
        agent = _make_remind_agent()
        agent.memory.acknowledge_reminder = AsyncMock(return_value=None)
        result = await agent.run(_make_task("remind", {"action": "ack", "telegram_msg_id": 77}))
        assert result.status == TaskStatus.FAILED

    async def test_action_ack_with_notion(self):
        agent = _make_remind_agent()
        notion_mock = AsyncMock()
        agent._notion = notion_mock
        agent.memory.acknowledge_reminder = AsyncMock(return_value=True)
        agent.memory.get_reminder_notion_id = AsyncMock(return_value="notion-page-2")
        result = await agent.run(_make_task("remind", {"action": "ack", "telegram_msg_id": 88}))
        assert result.status == TaskStatus.COMPLETED
        notion_mock.update_status.assert_called_once_with("notion-page-2", "Done")

    async def test_unknown_action_returns_failed(self):
        agent = _make_remind_agent()
        result = await agent.run(_make_task("remind", {"action": "fly"}))
        assert result.status == TaskStatus.FAILED
        assert "fly" in result.output_data.get("error", "")

    async def test_action_create_happy_path(self):
        agent = _make_remind_agent()
        future = datetime.now() + timedelta(hours=2)
        with patch("apps.backend.agents.remind.dateparser.parse", return_value=future):
            agent._extract_reminder_json = AsyncMock(
                return_value={"text": "call Mario", "recurring": None}
            )
            agent._check_duplicate = AsyncMock(return_value=None)
            agent.memory.add_reminder = AsyncMock(return_value=42)
            agent.memory.upsert_learning = AsyncMock()
            agent._notify_telegram = AsyncMock()
            result = await agent.run(_make_task("remind", {
                "action": "create",
                "text": "call Mario",
                "when": "in 2 hours",
            }))
        assert result.status == TaskStatus.COMPLETED
        assert result.output_data["reminder_id"] == 42
        assert result.output_data["text"] == "call Mario"

    async def test_action_create_missing_text(self):
        agent = _make_remind_agent()
        result = await agent.run(_make_task("remind", {"action": "create"}))
        assert result.status == TaskStatus.FAILED

    async def test_action_create_past_date(self):
        agent = _make_remind_agent()
        past = datetime.now() - timedelta(hours=3)
        with patch("apps.backend.agents.remind.dateparser.parse", return_value=past):
            agent._extract_reminder_json = AsyncMock(
                return_value={"text": "call Mario", "recurring": None}
            )
            result = await agent.run(_make_task("remind", {
                "action": "create",
                "text": "call Mario",
                "when": "3 hours ago",
            }))
        assert result.status == TaskStatus.FAILED
        assert "passata" in result.output_data.get("error", "")

    async def test_action_create_soon_warning(self):
        """Trigger in next 5 minutes → succeeds but reply contains warning."""
        agent = _make_remind_agent()
        soon = datetime.now() + timedelta(minutes=2)
        with patch("apps.backend.agents.remind.dateparser.parse", return_value=soon):
            agent._extract_reminder_json = AsyncMock(
                return_value={"text": "send email", "recurring": None}
            )
            agent._check_duplicate = AsyncMock(return_value=None)
            agent.memory.add_reminder = AsyncMock(return_value=7)
            agent.memory.upsert_learning = AsyncMock()
            agent._notify_telegram = AsyncMock()
            result = await agent.run(_make_task("remind", {
                "action": "create",
                "text": "send email",
                "when": "in 2 minutes",
            }))
        assert result.status == TaskStatus.COMPLETED
        assert "5 minuti" in result.output_data.get("reply", "")

    async def test_action_create_duplicate_found(self):
        agent = _make_remind_agent()
        future = datetime.now() + timedelta(hours=2)
        duplicate = {"id": 1, "text": "call Mario", "trigger_at": future.isoformat()}
        with patch("apps.backend.agents.remind.dateparser.parse", return_value=future):
            agent._extract_reminder_json = AsyncMock(
                return_value={"text": "call Mario", "recurring": None}
            )
            agent._check_duplicate = AsyncMock(return_value=duplicate)
            result = await agent.run(_make_task("remind", {
                "action": "create",
                "text": "call Mario",
                "when": "in 2 hours",
            }))
        assert result.status == TaskStatus.FAILED
        assert "simile" in result.output_data.get("error", "")

    async def test_action_create_no_time_no_recurring(self):
        agent = _make_remind_agent()
        with patch("apps.backend.agents.remind.dateparser.parse", return_value=None):
            agent._extract_reminder_json = AsyncMock(
                return_value={"text": "call Mario", "recurring": None}
            )
            result = await agent.run(_make_task("remind", {
                "action": "create",
                "text": "call Mario",
            }))
        assert result.status == TaskStatus.FAILED

    async def test_action_create_recurring_no_trigger(self):
        """recurring set + no trigger_at → saves without time, returns COMPLETED."""
        agent = _make_remind_agent()
        with patch("apps.backend.agents.remind.dateparser.parse", return_value=None):
            agent._extract_reminder_json = AsyncMock(
                return_value={"text": "gym session", "recurring": "daily"}
            )
            agent._check_duplicate = AsyncMock(return_value=None)
            agent.memory.add_reminder = AsyncMock(return_value=10)
            agent.memory.upsert_learning = AsyncMock()
            agent._notify_telegram = AsyncMock()
            result = await agent.run(_make_task("remind", {
                "action": "create",
                "text": "gym session every day",
            }))
        assert result.status == TaskStatus.COMPLETED
        assert result.output_data["recurring"] == "daily"

    async def test_action_create_db_error(self):
        agent = _make_remind_agent()
        future = datetime.now() + timedelta(hours=2)
        with patch("apps.backend.agents.remind.dateparser.parse", return_value=future):
            agent._extract_reminder_json = AsyncMock(
                return_value={"text": "call Mario", "recurring": None}
            )
            agent._check_duplicate = AsyncMock(return_value=None)
            agent.memory.add_reminder = AsyncMock(return_value=None)
            result = await agent.run(_make_task("remind", {
                "action": "create",
                "text": "call Mario",
                "when": "in 2 hours",
            }))
        assert result.status == TaskStatus.FAILED
        assert "salvataggio" in result.output_data.get("error", "").lower()

    async def test_action_create_uses_user_message_fallback(self):
        """When 'when' not present, falls back to '_user_message' for dateparser."""
        agent = _make_remind_agent()
        future = datetime.now() + timedelta(hours=1)
        with patch("apps.backend.agents.remind.dateparser.parse", return_value=future) as mock_parse:
            agent._extract_reminder_json = AsyncMock(
                return_value={"text": "buy milk", "recurring": None}
            )
            agent._check_duplicate = AsyncMock(return_value=None)
            agent.memory.add_reminder = AsyncMock(return_value=5)
            agent.memory.upsert_learning = AsyncMock()
            agent._notify_telegram = AsyncMock()
            result = await agent.run(_make_task("remind", {
                "action": "create",
                "text": "buy milk",
                "_user_message": "buy milk in 1 hour",
            }))
        assert result.status == TaskStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════════════════
# SummarizeAgent
# ═══════════════════════════════════════════════════════════════════════════════


def _make_summarize_agent(memory=None, text_extractor=None) -> SummarizeAgent:
    mem = memory if memory is not None else _async_memory()
    if text_extractor is None:
        extractor = MagicMock()
        extractor.from_url = AsyncMock(return_value="extracted url text")
        extractor.from_telegram_file = AsyncMock(return_value=("extracted pdf text", "pdf"))
        extractor.chunk_text = MagicMock(return_value=["chunk_a", "chunk_b"])
    else:
        extractor = text_extractor
    agent = SummarizeAgent(
        anthropic_client=AsyncMock(),
        memory=mem,
        text_extractor=extractor,
    )
    agent._log_step = AsyncMock()
    return agent


class TestSummarizeFail:
    def test_fail_returns_failed_status(self):
        agent = _make_summarize_agent()
        result = agent._fail("error occurred")
        assert result.status == TaskStatus.FAILED
        assert result.output_data["error"] == "error occurred"
        assert result.agent_name == "summarize"

    def test_fail_uses_task_id(self):
        agent = _make_summarize_agent()
        agent._task_id = "t-99"
        result = agent._fail("oops")
        assert result.task_id == "t-99"


class TestSummarizeDirectSummary:
    async def test_returns_llm_string(self):
        agent = _make_summarize_agent()
        agent._call_llm = AsyncMock(return_value="Nice summary.")
        result = await agent._direct_summary("some text", "normal")
        assert result == "Nice summary."

    async def test_fallback_to_ollama_on_exception(self):
        agent = _make_summarize_agent()
        agent._call_llm = AsyncMock(side_effect=Exception("LLM down"))
        agent._call_llm_ollama = AsyncMock(return_value="Ollama summary.")
        result = await agent._direct_summary("some text", "brief")
        assert result == "Ollama summary."

    async def test_brief_length_used(self):
        agent = _make_summarize_agent()
        agent._call_llm = AsyncMock(return_value="Brief.")
        result = await agent._direct_summary("text", "brief", "personal")
        assert result == "Brief."

    async def test_detailed_length_used(self):
        agent = _make_summarize_agent()
        agent._call_llm = AsyncMock(return_value="Detailed.")
        result = await agent._direct_summary("text", "detailed")
        assert result == "Detailed."


class TestSummarizeSummarizeChunk:
    async def test_returns_ollama_string(self):
        agent = _make_summarize_agent()
        agent._call_llm_ollama = AsyncMock(return_value="Chunk summary.")
        result = await agent._summarize_chunk("chunk text", idx=0)
        assert result == "Chunk summary."

    async def test_returns_empty_on_exception(self):
        agent = _make_summarize_agent()
        agent._call_llm_ollama = AsyncMock(side_effect=Exception("Ollama down"))
        result = await agent._summarize_chunk("chunk text", idx=2)
        assert result == ""


class TestSummarizeDetectActionItems:
    async def test_no_items_returns_empty(self):
        agent = _make_summarize_agent()
        agent._call_llm_ollama = AsyncMock(return_value="NO")
        assert await agent._detect_action_items("nothing here") == []

    async def test_empty_response_returns_empty(self):
        agent = _make_summarize_agent()
        agent._call_llm_ollama = AsyncMock(return_value="")
        assert await agent._detect_action_items("text") == []

    async def test_si_with_items_parsed(self):
        agent = _make_summarize_agent()
        agent._call_llm_ollama = AsyncMock(return_value="SI: [call Mario] | [send email]")
        result = await agent._detect_action_items("call Mario, send email")
        assert len(result) == 2
        assert "call Mario" in result

    async def test_si_single_item(self):
        agent = _make_summarize_agent()
        agent._call_llm_ollama = AsyncMock(return_value="SI: [review PR]")
        result = await agent._detect_action_items("review PR today")
        assert result == ["review PR"]

    async def test_exception_returns_empty(self):
        agent = _make_summarize_agent()
        agent._call_llm_ollama = AsyncMock(side_effect=Exception("fail"))
        assert await agent._detect_action_items("text") == []


class TestSummarizeRun:
    async def test_missing_content_returns_failed(self):
        agent = _make_summarize_agent()
        result = await agent.run(_make_task("summarize", {"content": ""}))
        assert result.status == TaskStatus.FAILED
        assert "content" in result.output_data.get("error", "").lower()

    async def test_short_text_direct_path(self):
        """Text < _CHUNK_THRESHOLD → _direct_summary (single chunk)."""
        agent = _make_summarize_agent()
        agent._call_llm = AsyncMock(return_value="Short summary.")
        agent._call_llm_ollama = AsyncMock(return_value="NO")
        agent.memory.store_personal_insight = AsyncMock()
        agent._notify_telegram = AsyncMock()
        result = await agent.run(_make_task("summarize", {
            "content": "This is a short text.",
            "source_type": "text",
            "save": True,
        }))
        assert result.status == TaskStatus.COMPLETED
        assert result.output_data["summary"] == "Short summary."
        agent.memory.store_personal_insight.assert_called_once()

    async def test_long_text_map_reduce_path(self):
        """Text > _CHUNK_THRESHOLD → map-reduce path."""
        agent = _make_summarize_agent()
        long_text = "A" * 3100
        agent._extractor.chunk_text = MagicMock(return_value=["chunk1", "chunk2"])
        # _summarize_chunk called twice, then _detect_action_items (NO), then merge
        agent._call_llm_ollama = AsyncMock(
            side_effect=["Summary1.", "Summary2.", "NO"]
        )
        agent._call_llm = AsyncMock(return_value="Merged summary.")
        agent.memory.store_personal_insight = AsyncMock()
        agent._notify_telegram = AsyncMock()
        result = await agent.run(_make_task("summarize", {
            "content": long_text,
            "source_type": "text",
        }))
        assert result.status == TaskStatus.COMPLETED
        assert result.output_data["summary"] == "Merged summary."

    async def test_empty_summary_returns_failed(self):
        agent = _make_summarize_agent()
        agent._call_llm = AsyncMock(return_value="")
        result = await agent.run(_make_task("summarize", {"content": "some text"}))
        assert result.status == TaskStatus.FAILED

    async def test_save_false_no_store(self):
        agent = _make_summarize_agent()
        agent._call_llm = AsyncMock(return_value="Summary.")
        agent._call_llm_ollama = AsyncMock(return_value="NO")
        agent._notify_telegram = AsyncMock()
        result = await agent.run(_make_task("summarize", {
            "content": "short text",
            "save": False,
        }))
        assert result.status == TaskStatus.COMPLETED
        agent.memory.store_personal_insight.assert_not_called()

    async def test_save_etsy_domain_uses_store_insight(self):
        agent = _make_summarize_agent()
        agent._call_llm = AsyncMock(return_value="Etsy summary.")
        agent._call_llm_ollama = AsyncMock(return_value="NO")
        agent.memory.store_insight = AsyncMock()
        agent._notify_telegram = AsyncMock()
        result = await agent.run(_make_task("summarize", {
            "content": "etsy content",
            "save": True,
            "domain_name": "etsy",
        }))
        assert result.status == TaskStatus.COMPLETED
        agent.memory.store_insight.assert_called_once()
        agent.memory.store_personal_insight.assert_not_called()

    async def test_invalid_length_falls_back_to_normal(self):
        agent = _make_summarize_agent()
        agent._call_llm = AsyncMock(return_value="Normal summary.")
        agent._call_llm_ollama = AsyncMock(return_value="NO")
        agent._notify_telegram = AsyncMock()
        result = await agent.run(_make_task("summarize", {
            "content": "text",
            "length": "turbo",
        }))
        assert result.status == TaskStatus.COMPLETED

    async def test_with_action_items_reply_contains_hint(self):
        agent = _make_summarize_agent()
        agent._call_llm = AsyncMock(return_value="Do X by Friday.")
        agent._call_llm_ollama = AsyncMock(return_value="SI: [do X by Friday]")
        agent.memory.store_personal_insight = AsyncMock()
        agent._notify_telegram = AsyncMock()
        result = await agent.run(_make_task("summarize", {
            "content": "text with deadline",
            "save": False,
        }))
        assert result.status == TaskStatus.COMPLETED
        assert len(result.output_data.get("action_items", [])) > 0
        assert "reminder" in result.output_data.get("reply", "").lower()

    async def test_url_source_extraction(self):
        agent = _make_summarize_agent()
        agent._extractor.from_url = AsyncMock(return_value="Page content from URL.")
        agent._call_llm = AsyncMock(return_value="URL summary.")
        agent._call_llm_ollama = AsyncMock(return_value="NO")
        agent.memory.store_personal_insight = AsyncMock()
        agent._notify_telegram = AsyncMock()
        result = await agent.run(_make_task("summarize", {
            "content": "https://example.com",
            "source_type": "url",
        }))
        assert result.status == TaskStatus.COMPLETED

    async def test_url_not_accessible_returns_failed(self):
        agent = _make_summarize_agent()
        agent._extractor.from_url = AsyncMock(return_value=None)
        result = await agent.run(_make_task("summarize", {
            "content": "https://example.com",
            "source_type": "url",
        }))
        assert result.status == TaskStatus.FAILED

    async def test_url_quality_fail_returns_failed(self):
        agent = _make_summarize_agent()
        agent._extractor.from_url = AsyncMock(
            return_value="enable javascript to view this page" + "x" * 500
        )
        result = await agent.run(_make_task("summarize", {
            "content": "https://example.com",
            "source_type": "url",
        }))
        assert result.status == TaskStatus.FAILED

    async def test_file_no_token_returns_failed(self):
        agent = _make_summarize_agent()
        with patch("apps.backend.agents.summarize.settings",
                   new=MagicMock(TELEGRAM_BOT_TOKEN="")):
            result = await agent.run(_make_task("summarize", {
                "content": "file_id_123",
                "source_type": "file",
            }))
        assert result.status == TaskStatus.FAILED

    async def test_file_unsupported_format_returns_failed(self):
        agent = _make_summarize_agent()
        agent._extractor.from_telegram_file = AsyncMock(return_value=(None, None))
        with patch("apps.backend.agents.summarize.settings",
                   new=MagicMock(TELEGRAM_BOT_TOKEN="real-token")):
            result = await agent.run(_make_task("summarize", {
                "content": "file_id",
                "source_type": "file",
            }))
        assert result.status == TaskStatus.FAILED

    async def test_file_happy_path(self):
        agent = _make_summarize_agent()
        agent._extractor.from_telegram_file = AsyncMock(
            return_value=("PDF content text.", "pdf")
        )
        agent._call_llm = AsyncMock(return_value="PDF summary.")
        agent._call_llm_ollama = AsyncMock(return_value="NO")
        agent.memory.store_personal_insight = AsyncMock()
        agent._notify_telegram = AsyncMock()
        with patch("apps.backend.agents.summarize.settings",
                   new=MagicMock(TELEGRAM_BOT_TOKEN="tok")):
            result = await agent.run(_make_task("summarize", {
                "content": "file_id",
                "source_type": "file",
            }))
        assert result.status == TaskStatus.COMPLETED
        assert "PDF summary." == result.output_data["summary"]

    async def test_store_exception_is_silenced(self):
        """store_personal_insight raising → run() completes anyway (fail-safe)."""
        agent = _make_summarize_agent()
        agent._call_llm = AsyncMock(return_value="Summary.")
        agent._call_llm_ollama = AsyncMock(return_value="NO")
        agent.memory.store_personal_insight = AsyncMock(side_effect=Exception("DB error"))
        agent._notify_telegram = AsyncMock()
        result = await agent.run(_make_task("summarize", {
            "content": "text",
            "save": True,
        }))
        assert result.status == TaskStatus.COMPLETED

    async def test_single_chunk_summary_skips_reduce(self):
        """_map_reduce_summary_from_chunks with 1 result → returns without merge call."""
        agent = _make_summarize_agent()
        agent._call_llm_ollama = AsyncMock(return_value="Only summary.")
        agent._call_llm = AsyncMock(return_value="Should not be reached")
        result = await agent._map_reduce_summary_from_chunks(["single chunk"], "normal")
        assert result == "Only summary."
        agent._call_llm.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Additional targeted tests to close remaining coverage gaps
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnalyticsRunCasesBAndA:
    """Cover analytics.py lines 195-199 (Case B) and 204-208 (Case A)."""

    async def test_run_cases_b_and_a_with_unique_listings(self):
        """no_conversion + no_views lists with IDs not in no_views_no_sales → analyzed."""
        agent = _make_analytics_agent()
        listing = {"listing_id": 42, "status": "active", "sales": 0}
        agent.memory.get_etsy_listings = AsyncMock(return_value=[listing])
        agent.memory.get_listings_no_views_no_sales = AsyncMock(
            return_value=[{"listing_id": 1}]
        )
        agent.memory.get_listings_no_conversion = AsyncMock(
            return_value=[{"listing_id": 99}]  # new ID → Case B covered
        )
        agent.memory.get_listings_no_views = AsyncMock(
            return_value=[{"listing_id": 101}]  # new ID → Case A covered
        )
        agent.memory.update_etsy_listing_stats = AsyncMock()
        agent.memory.store_insight = AsyncMock()
        agent._call_tool = AsyncMock(
            return_value={
                "views": 10,
                "num_favorers": 5,
                "state": "active",
                "price": {"amount": 1000},
                "shop_id": "S1",
            }
        )
        agent._get_listing_sales = AsyncMock(return_value=3)
        agent._find_bestsellers = AsyncMock(return_value=[])
        agent._build_report = AsyncMock(return_value={"synced": 1})
        agent._send_daily_summary = AsyncMock()
        agent._calculate_analytics_confidence = MagicMock(return_value=(0.9, []))
        agent._analyze_no_views_no_sales = AsyncMock()
        agent._analyze_no_conversion = AsyncMock()
        agent._analyze_no_views = AsyncMock()

        result = await agent.run(_make_task("analytics"))

        assert result.status == TaskStatus.COMPLETED
        agent._analyze_no_conversion.assert_called_once()
        agent._analyze_no_views.assert_called_once()

    async def test_run_cases_b_skipped_when_in_case_c(self):
        """Listing already in Case C → Case B skips it (if branch not taken)."""
        agent = _make_analytics_agent()
        agent.memory.get_etsy_listings = AsyncMock(return_value=[])
        agent.memory.get_listings_no_views_no_sales = AsyncMock(
            return_value=[{"listing_id": 5}]
        )
        agent.memory.get_listings_no_conversion = AsyncMock(
            return_value=[{"listing_id": 5}]  # same ID → already_analyzed → skip
        )
        agent.memory.get_listings_no_views = AsyncMock(return_value=[])
        agent.memory.store_insight = AsyncMock()
        agent._find_bestsellers = AsyncMock(return_value=[])
        agent._build_report = AsyncMock(return_value={})
        agent._send_daily_summary = AsyncMock()
        agent._calculate_analytics_confidence = MagicMock(return_value=(0.9, []))
        agent._analyze_no_views_no_sales = AsyncMock()
        agent._analyze_no_conversion = AsyncMock()
        agent._analyze_no_views = AsyncMock()

        result = await agent.run(_make_task("analytics"))

        assert result.status == TaskStatus.COMPLETED
        agent._analyze_no_conversion.assert_not_called()


class TestRecallInternalMethods:
    """Cover recall.py lines 326-336, 344-361, 371-389, 412-430."""

    async def test_check_stop_yes(self):
        agent = _make_recall_agent()
        agent._call_llm_ollama = AsyncMock(return_value="YES I am done")
        assert await agent._check_stop("query", "answer") is True

    async def test_check_stop_no(self):
        agent = _make_recall_agent()
        agent._call_llm_ollama = AsyncMock(return_value="NO more data needed")
        assert await agent._check_stop("query", "answer") is False

    async def test_check_stop_exception_returns_true(self):
        agent = _make_recall_agent()
        agent._call_llm_ollama = AsyncMock(side_effect=Exception("LLM fail"))
        assert await agent._check_stop("query", "answer") is True

    async def test_synthesize_happy_path(self):
        agent = _make_recall_agent()
        agent._call_llm = AsyncMock(return_value="Synthesis result.")
        result = await agent._synthesize("what is X?", "context data")
        assert result == "Synthesis result."

    async def test_synthesize_exception_returns_fallback(self):
        agent = _make_recall_agent()
        agent._call_llm = AsyncMock(side_effect=Exception("LLM down"))
        result = await agent._synthesize("query", "[App: A] data [App: B] more")
        assert "sorgenti" in result or "sintesi" in result

    async def test_synthesize_integrated_happy_path(self):
        agent = _make_recall_agent()
        agent._call_llm = AsyncMock(return_value="Integrated result.")
        result = await agent._synthesize_integrated("q", "ctx_primary", "ctx_supp", "draft")
        assert result == "Integrated result."

    async def test_synthesize_integrated_exception_returns_draft(self):
        agent = _make_recall_agent()
        agent._call_llm = AsyncMock(side_effect=Exception("LLM fail"))
        result = await agent._synthesize_integrated("q", "ctx_p", "ctx_s", "My draft answer")
        assert result == "My draft answer"

    async def test_store_recall_insight_happy_path(self):
        agent = _make_recall_agent()
        agent.memory.store_personal_insight = AsyncMock()
        await agent._store_recall_insight("query", "synthesis text", [{"a": 1}], 0.8)
        agent.memory.store_personal_insight.assert_called_once()

    async def test_store_recall_insight_empty_synthesis_skipped(self):
        agent = _make_recall_agent()
        agent.memory.store_personal_insight = AsyncMock()
        await agent._store_recall_insight("query", "", [], 0.5)
        agent.memory.store_personal_insight.assert_not_called()

    async def test_store_recall_insight_whitespace_synthesis_skipped(self):
        agent = _make_recall_agent()
        agent.memory.store_personal_insight = AsyncMock()
        await agent._store_recall_insight("query", "   ", [], 0.5)
        agent.memory.store_personal_insight.assert_not_called()

    async def test_store_recall_insight_exception_silenced(self):
        agent = _make_recall_agent()
        agent.memory.store_personal_insight = AsyncMock(side_effect=Exception("DB fail"))
        # Must not raise
        await agent._store_recall_insight("q", "some synthesis", [], 0.9)


class TestRemindNotifyTelegram:
    """Cover remind.py lines 114-118."""

    async def test_notify_telegram_calls_broadcaster(self):
        broadcaster = AsyncMock()
        agent = RemindAgent(
            anthropic_client=AsyncMock(),
            memory=_async_memory(),
            telegram_broadcaster=broadcaster,
        )
        agent._log_step = AsyncMock()
        await agent._notify_telegram("Hello!")
        broadcaster.assert_called_once_with("Hello!")

    async def test_notify_telegram_exception_silenced(self):
        broadcaster = AsyncMock(side_effect=Exception("Telegram down"))
        agent = RemindAgent(
            anthropic_client=AsyncMock(),
            memory=_async_memory(),
            telegram_broadcaster=broadcaster,
        )
        agent._log_step = AsyncMock()
        # Should not raise
        await agent._notify_telegram("Test message")

    async def test_notify_telegram_no_broadcaster_noop(self):
        agent = _make_remind_agent()
        # _telegram_broadcast is None → method is a no-op
        await agent._notify_telegram("should be ignored")


class TestRemindEnsureNotion:
    """Cover remind.py lines 125-131."""

    async def test_ensure_notion_with_token_initializes(self):
        agent = RemindAgent(anthropic_client=AsyncMock(), memory=_async_memory())
        agent._log_step = AsyncMock()
        with patch("apps.backend.agents.remind.NotionCalendar") as mock_nc, \
             patch("apps.backend.agents.remind.settings") as ms:
            ms.NOTION_API_TOKEN = "test-token"
            nc_instance = AsyncMock()
            mock_nc.return_value = nc_instance
            await agent._ensure_notion()
        assert agent._notion_ready is True
        mock_nc.assert_called_once_with(token="test-token")
        nc_instance.ensure_database.assert_called_once()

    async def test_ensure_notion_without_token_sets_ready(self):
        agent = RemindAgent(anthropic_client=AsyncMock(), memory=_async_memory())
        agent._log_step = AsyncMock()
        with patch("apps.backend.agents.remind.NotionCalendar") as mock_nc, \
             patch("apps.backend.agents.remind.settings") as ms:
            ms.NOTION_API_TOKEN = ""
            await agent._ensure_notion()
        assert agent._notion_ready is True
        mock_nc.assert_not_called()

    async def test_ensure_notion_already_ready_is_noop(self):
        agent = RemindAgent(anthropic_client=AsyncMock(), memory=_async_memory())
        agent._log_step = AsyncMock()
        agent._notion_ready = True
        with patch("apps.backend.agents.remind.NotionCalendar") as mock_nc:
            await agent._ensure_notion()
        mock_nc.assert_not_called()


class TestRemindCreateAdditionalBranches:
    """Cover remind.py lines 200, 245-246, 266-276, 301-302."""

    async def test_whitespace_when_triggers_continue(self):
        """when='   ' → empty after strip → continue fires on line 200."""
        agent = _make_remind_agent()
        future = datetime.now() + timedelta(hours=2)
        with patch("apps.backend.agents.remind.dateparser.parse", return_value=future):
            agent._extract_reminder_json = AsyncMock(
                return_value={"text": "call Mario", "recurring": None}
            )
            agent._check_duplicate = AsyncMock(return_value=None)
            agent.memory.add_reminder = AsyncMock(return_value=99)
            agent.memory.upsert_learning = AsyncMock()
            agent._notify_telegram = AsyncMock()
            result = await agent.run(_make_task("remind", {
                "action": "create",
                "when": "   ",  # whitespace → "" after strip → continue
                "text": "call Mario in 2 hours",
            }))
        assert result.status == TaskStatus.COMPLETED

    async def test_duplicate_with_invalid_trigger_at_string(self):
        """Duplicate has non-ISO trigger_at → ValueError caught, raw string used."""
        agent = _make_remind_agent()
        future = datetime.now() + timedelta(hours=2)
        duplicate = {"id": 1, "text": "call Mario", "trigger_at": "not-a-valid-date"}
        with patch("apps.backend.agents.remind.dateparser.parse", return_value=future):
            agent._extract_reminder_json = AsyncMock(
                return_value={"text": "call Mario", "recurring": None}
            )
            agent._check_duplicate = AsyncMock(return_value=duplicate)
            result = await agent.run(_make_task("remind", {
                "action": "create",
                "when": "in 2 hours",
                "text": "call Mario",
            }))
        assert result.status == TaskStatus.FAILED
        assert "not-a-valid-date" in result.output_data.get("error", "")

    async def test_create_notion_saves_page_id(self):
        """_notion set + trigger_at + token → notion.create_reminder called."""
        agent = _make_remind_agent()
        future = datetime.now() + timedelta(hours=2)
        notion_mock = AsyncMock()
        notion_mock.create_reminder = AsyncMock(return_value="notion-page-99")
        agent._notion = notion_mock
        with patch("apps.backend.agents.remind.dateparser.parse", return_value=future), \
             patch("apps.backend.agents.remind.settings") as ms:
            ms.NOTION_API_TOKEN = "tok"
            agent._extract_reminder_json = AsyncMock(
                return_value={"text": "call Mario", "recurring": None}
            )
            agent._check_duplicate = AsyncMock(return_value=None)
            agent.memory.add_reminder = AsyncMock(return_value=42)
            agent.memory.update_reminder_notion_id = AsyncMock()
            agent.memory.upsert_learning = AsyncMock()
            agent._notify_telegram = AsyncMock()
            result = await agent.run(_make_task("remind", {
                "action": "create",
                "when": "in 2 hours",
                "text": "call Mario",
            }))
        assert result.status == TaskStatus.COMPLETED
        assert result.output_data["notion_page_id"] == "notion-page-99"
        agent.memory.update_reminder_notion_id.assert_called_once_with(42, "notion-page-99")

    async def test_create_notion_exception_silenced(self):
        """notion.create_reminder raises → fail-safe: reminder saved without notion_id."""
        agent = _make_remind_agent()
        future = datetime.now() + timedelta(hours=2)
        notion_mock = AsyncMock()
        notion_mock.create_reminder = AsyncMock(side_effect=Exception("Notion down"))
        agent._notion = notion_mock
        with patch("apps.backend.agents.remind.dateparser.parse", return_value=future), \
             patch("apps.backend.agents.remind.settings") as ms:
            ms.NOTION_API_TOKEN = "tok"
            agent._extract_reminder_json = AsyncMock(
                return_value={"text": "call Mario", "recurring": None}
            )
            agent._check_duplicate = AsyncMock(return_value=None)
            agent.memory.add_reminder = AsyncMock(return_value=42)
            agent.memory.upsert_learning = AsyncMock()
            agent._notify_telegram = AsyncMock()
            result = await agent.run(_make_task("remind", {
                "action": "create",
                "when": "in 2 hours",
                "text": "call Mario",
            }))
        assert result.status == TaskStatus.COMPLETED
        assert result.output_data["notion_page_id"] is None

    async def test_create_upsert_learning_exception_silenced(self):
        """upsert_learning raises → exception silenced, result is COMPLETED."""
        agent = _make_remind_agent()
        future = datetime.now() + timedelta(hours=2)
        with patch("apps.backend.agents.remind.dateparser.parse", return_value=future):
            agent._extract_reminder_json = AsyncMock(
                return_value={"text": "call Mario", "recurring": None}
            )
            agent._check_duplicate = AsyncMock(return_value=None)
            agent.memory.add_reminder = AsyncMock(return_value=42)
            agent.memory.upsert_learning = AsyncMock(side_effect=Exception("DB fail"))
            agent._notify_telegram = AsyncMock()
            result = await agent.run(_make_task("remind", {
                "action": "create",
                "when": "in 2 hours",
                "text": "call Mario",
            }))
        assert result.status == TaskStatus.COMPLETED


class TestRemindListAdditionalBranches:
    """Cover remind.py lines 349-350 (ValueError in _list date formatting)."""

    async def test_list_non_iso_trigger_at_kept_as_string(self):
        """Reminder with non-ISO trigger_at → ValueError caught, raw string in reply."""
        agent = _make_remind_agent()
        reminders = [
            {"id": 5, "text": "call", "trigger_at": "not-a-date", "status": "pending"}
        ]
        agent.memory.get_pending_reminders = AsyncMock(return_value=reminders)
        agent.memory.get_sent_unacknowledged = AsyncMock(return_value=[])
        result = await agent.run(_make_task("remind", {"action": "list"}))
        assert result.status == TaskStatus.COMPLETED
        assert "not-a-date" in result.output_data.get("reply", "")


class TestSummarizeNotifyTelegram:
    """Cover summarize.py lines 114-118."""

    async def test_run_with_broadcaster_calls_notify(self):
        broadcaster = AsyncMock()
        extractor = MagicMock()
        extractor.chunk_text = MagicMock(return_value=["a"])
        agent = SummarizeAgent(
            anthropic_client=AsyncMock(),
            memory=_async_memory(),
            text_extractor=extractor,
            telegram_broadcaster=broadcaster,
        )
        agent._log_step = AsyncMock()
        agent._call_llm = AsyncMock(return_value="Summary text.")
        result = await agent.run(_make_task("summarize", {
            "content": "short text",
            "save": False,
        }))
        assert result.status == TaskStatus.COMPLETED
        broadcaster.assert_called_once()

    async def test_run_broadcaster_exception_silenced(self):
        broadcaster = AsyncMock(side_effect=Exception("Telegram down"))
        extractor = MagicMock()
        extractor.chunk_text = MagicMock(return_value=["a"])
        agent = SummarizeAgent(
            anthropic_client=AsyncMock(),
            memory=_async_memory(),
            text_extractor=extractor,
            telegram_broadcaster=broadcaster,
        )
        agent._log_step = AsyncMock()
        agent._call_llm = AsyncMock(return_value="Summary text.")
        result = await agent.run(_make_task("summarize", {
            "content": "short text",
            "save": False,
        }))
        assert result.status == TaskStatus.COMPLETED


class TestSummarizeMapReduceAdditionalPaths:
    """Cover summarize.py lines 179-180, 294-295, 298, 316-318."""

    async def test_long_text_truncated_to_max_chunks(self):
        """More than MAX_CHUNKS=5 chunks → truncated, log includes 'troncato a 5'."""
        agent = _make_summarize_agent()
        six_chunks = [f"chunk_{i}" for i in range(6)]
        agent._extractor.chunk_text = MagicMock(return_value=six_chunks)
        agent._call_llm_ollama = AsyncMock(return_value="Chunk summary.")
        agent._call_llm = AsyncMock(return_value="Final merged summary.")
        long_text = "A" * 3100  # > _CHUNK_THRESHOLD=3000
        result = await agent.run(_make_task("summarize", {
            "content": long_text,
            "save": False,
        }))
        assert result.status == TaskStatus.COMPLETED
        # Verify truncation log was emitted
        log_args = [str(c) for c in agent._log_step.call_args_list]
        assert any("troncato" in s for s in log_args)

    async def test_map_reduce_exception_chunk_logged(self):
        """_summarize_chunk raises → Exception captured by gather, logged, other chunks used."""
        agent = _make_summarize_agent()
        call_count = {"n": 0}

        async def chunk_mock(chunk, idx=0):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Unexpected crash")
            return "Good summary."

        agent._summarize_chunk = chunk_mock
        agent._call_llm = AsyncMock(return_value="Should not be called for merge")
        result = await agent._map_reduce_summary_from_chunks(["chunk1", "chunk2"], "normal")
        assert result == "Good summary."

    async def test_map_reduce_all_chunks_fail_returns_empty_string(self):
        """All _summarize_chunk calls raise → empty chunk_summaries → return ''."""
        agent = _make_summarize_agent()

        async def chunk_mock_fail(chunk, idx=0):
            raise RuntimeError("All fail")

        agent._summarize_chunk = chunk_mock_fail
        result = await agent._map_reduce_summary_from_chunks(["chunk1", "chunk2"], "normal")
        assert result == ""

    async def test_map_reduce_merge_exception_fallback_to_ollama(self):
        """_call_llm raises during REDUCE merge → fallback to _call_llm_ollama."""
        agent = _make_summarize_agent()

        async def good_chunk(chunk, idx=0):
            return f"summary of {chunk}"

        agent._summarize_chunk = good_chunk
        agent._call_llm = AsyncMock(side_effect=Exception("Claude unavailable"))
        agent._call_llm_ollama = AsyncMock(return_value="Ollama merged summary.")
        result = await agent._map_reduce_summary_from_chunks(["c1", "c2"], "normal")
        assert result == "Ollama merged summary."
