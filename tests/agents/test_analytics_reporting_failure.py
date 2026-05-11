"""Coverage tests for _analytics/reporting_mixin.py and _analytics/failure_mixin.py."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.backend.agents._analytics.failure_mixin import _AnalyticsFailureMixin
from apps.backend.agents._analytics.reporting_mixin import _AnalyticsReportingMixin

# ─────────────────────────────────────────────────────────────────────────────
# Shared constants
# ─────────────────────────────────────────────────────────────────────────────

_VALID_ANALYSIS = {
    "cause": "Keyword troppo generiche",
    "recommendations": ["Usa keyword specifiche", "Ottimizza titolo", "Migliora tag"],
    "avoid_in_future": "Evita keyword generiche",
}
_VALID_JSON_STR = json.dumps(_VALID_ANALYSIS)

_SAMPLE_LISTING = {
    "listing_id": "L-001",
    "title": "Printable Watercolor Art",
    "niche": "wall_art",
    "tags": ["watercolor", "printable"],
    "price_eur": 5.99,
    "size": "A4",
    "template": "watercolor",
    "views": 50,
    "favorites": 3,
    "ab_price_variant": "A",
}

_SAMPLE_REPORT = {
    "total_views": 500,
    "total_favorites": 30,
    "total_sales": 10,
    "total_revenue_eur": 150.00,
    "delta_views_vs_yesterday": 25,
    "total_listings_active": 20,
    "drafts": 2,
    "failures": {"no_views": 3, "no_conversion": 2},
    "bestsellers": [{"title": "Printable Wall Art Watercolor", "sales": 5}],
    "ab_performance": {"winner": "A", "winner_confidence": "medium"},
}


# ─────────────────────────────────────────────────────────────────────────────
# Agent factories
# ─────────────────────────────────────────────────────────────────────────────

def _make_reporting_agent(telegram_broadcast=None):
    class _Agent(_AnalyticsReportingMixin):
        pass

    agent = _Agent()
    agent.memory = MagicMock()
    agent.memory.get_etsy_listings = AsyncMock(return_value=[])
    agent.memory.get_listing_prev_views = AsyncMock(return_value=None)
    agent._telegram_broadcast = telegram_broadcast
    return agent


def _make_failure_agent():
    class _Agent(_AnalyticsFailureMixin):
        pass

    agent = _Agent()
    agent.memory = MagicMock()
    agent.memory.flag_no_views = AsyncMock()
    agent.memory.flag_no_conversion = AsyncMock()
    agent.memory.flag_no_views_no_sales = AsyncMock()
    agent.memory.save_listing_analysis = AsyncMock()
    agent.memory.query_chromadb_recent = AsyncMock(return_value=[])
    agent.memory.store_insight = AsyncMock(return_value="chroma-id-001")
    agent._call_llm = AsyncMock(return_value=_VALID_JSON_STR)
    agent._notify_telegram = AsyncMock()
    return agent


# ─────────────────────────────────────────────────────────────────────────────
# TestReportingMixin
# ─────────────────────────────────────────────────────────────────────────────

class TestReportingMixin:

    # ── _build_report: basic aggregation ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_build_report_basic_sums(self):
        agent = _make_reporting_agent()
        synced = [
            {"listing_id": "L1", "views": 10, "favorites": 2, "sales": 1, "revenue_eur": 5.0},
            {"listing_id": "L2", "views": 20, "favorites": 4, "sales": 2, "revenue_eur": 10.0},
        ]

        result = await asyncio.wait_for(
            agent._build_report(
                listings=[],
                synced=synced,
                failure_counts={"no_views": 1},
                bestsellers=[],
                today_str="2024-01-15",
            ),
            timeout=5,
        )

        assert result["total_views"] == 30
        assert result["total_favorites"] == 6
        assert result["total_sales"] == 3
        assert result["total_revenue_eur"] == 15.0
        assert result["date"] == "2024-01-15"
        assert result["failures"] == {"no_views": 1}

    @pytest.mark.asyncio
    async def test_build_report_ab_accumulates_per_variant(self):
        agent = _make_reporting_agent()
        all_listings = [
            {"ab_price_variant": "A", "views": 100, "sales": 5, "revenue_eur": 50.0, "status": "active"},
            {"ab_price_variant": "A", "views": 80, "sales": 3, "revenue_eur": 30.0, "status": "active"},
            {"ab_price_variant": "B", "views": 60, "sales": 2, "revenue_eur": 20.0, "status": "active"},
            {"ab_price_variant": "B", "views": 40, "sales": 1, "revenue_eur": 10.0, "status": "active"},
            {"ab_price_variant": "X", "views": 10, "sales": 0, "revenue_eur": 0.0, "status": "draft"},
        ]
        agent.memory.get_etsy_listings = AsyncMock(return_value=all_listings)

        result = await asyncio.wait_for(
            agent._build_report([], [], {}, [], "2024-01-15"),
            timeout=5,
        )

        ab = result["ab_performance"]
        assert ab["A"]["count"] == 2
        assert ab["A"]["views"] == 180
        assert ab["A"]["avg_views"] == 90.0
        assert ab["B"]["count"] == 2
        assert ab["B"]["avg_views"] == 50.0
        assert result["total_listings_active"] == 4
        assert result["drafts"] == 1

    @pytest.mark.asyncio
    async def test_build_report_ab_winner_a_low_confidence(self):
        agent = _make_reporting_agent()
        # 3 A + 3 B = 6 total < 10 → low confidence; A conversion >> B
        all_listings = (
            [{"ab_price_variant": "A", "views": 100, "sales": 5, "revenue_eur": 50.0, "status": "active"}] * 3
            + [{"ab_price_variant": "B", "views": 100, "sales": 0, "revenue_eur": 0.0, "status": "active"}] * 3
        )
        agent.memory.get_etsy_listings = AsyncMock(return_value=all_listings)

        result = await asyncio.wait_for(
            agent._build_report([], [], {}, [], "2024-01-15"),
            timeout=5,
        )

        ab = result["ab_performance"]
        assert ab["winner"] == "A"
        assert ab["winner_confidence"] == "low"

    @pytest.mark.asyncio
    async def test_build_report_ab_winner_b_medium_confidence(self):
        agent = _make_reporting_agent()
        # 5 A + 5 B = 10 → medium confidence; B conversion >> A
        all_listings = (
            [{"ab_price_variant": "A", "views": 100, "sales": 0, "revenue_eur": 0.0, "status": "active"}] * 5
            + [{"ab_price_variant": "B", "views": 100, "sales": 5, "revenue_eur": 50.0, "status": "active"}] * 5
        )
        agent.memory.get_etsy_listings = AsyncMock(return_value=all_listings)

        result = await asyncio.wait_for(
            agent._build_report([], [], {}, [], "2024-01-15"),
            timeout=5,
        )

        ab = result["ab_performance"]
        assert ab["winner"] == "B"
        assert ab["winner_confidence"] == "medium"

    @pytest.mark.asyncio
    async def test_build_report_ab_inconclusive(self):
        agent = _make_reporting_agent()
        # Same conversion rate → inconclusive
        all_listings = (
            [{"ab_price_variant": "A", "views": 100, "sales": 5, "revenue_eur": 50.0, "status": "active"}] * 3
            + [{"ab_price_variant": "B", "views": 100, "sales": 5, "revenue_eur": 50.0, "status": "active"}] * 3
        )
        agent.memory.get_etsy_listings = AsyncMock(return_value=all_listings)

        result = await asyncio.wait_for(
            agent._build_report([], [], {}, [], "2024-01-15"),
            timeout=5,
        )

        ab = result["ab_performance"]
        assert ab["winner"] == "inconclusive"
        assert ab["winner_confidence"] == "medium"

    @pytest.mark.asyncio
    async def test_build_report_ab_insufficient_data(self):
        agent = _make_reporting_agent()
        # Only 2 of each → winner remains None
        all_listings = (
            [{"ab_price_variant": "A", "views": 100, "sales": 5, "revenue_eur": 50.0, "status": "active"}] * 2
            + [{"ab_price_variant": "B", "views": 100, "sales": 1, "revenue_eur": 10.0, "status": "active"}] * 2
        )
        agent.memory.get_etsy_listings = AsyncMock(return_value=all_listings)

        result = await asyncio.wait_for(
            agent._build_report([], [], {}, [], "2024-01-15"),
            timeout=5,
        )

        ab = result["ab_performance"]
        assert ab["winner"] is None
        assert ab["winner_confidence"] == "insufficient_data"

    @pytest.mark.asyncio
    async def test_build_report_delta_views_with_prev(self):
        agent = _make_reporting_agent()
        agent.memory.get_listing_prev_views = AsyncMock(return_value=10)
        synced = [{"listing_id": "L1", "views": 15}]

        result = await asyncio.wait_for(
            agent._build_report([], synced, {}, [], "2024-01-15"),
            timeout=5,
        )

        assert result["delta_views_vs_yesterday"] == 5

    @pytest.mark.asyncio
    async def test_build_report_delta_views_prev_none(self):
        agent = _make_reporting_agent()
        agent.memory.get_listing_prev_views = AsyncMock(return_value=None)
        synced = [{"listing_id": "L1", "views": 15}]

        result = await asyncio.wait_for(
            agent._build_report([], synced, {}, [], "2024-01-15"),
            timeout=5,
        )

        assert result["delta_views_vs_yesterday"] == 0

    @pytest.mark.asyncio
    async def test_build_report_delta_views_clamps_negative(self):
        agent = _make_reporting_agent()
        # prev > current → clamped to 0
        agent.memory.get_listing_prev_views = AsyncMock(return_value=100)
        synced = [{"listing_id": "L1", "views": 5}]

        result = await asyncio.wait_for(
            agent._build_report([], synced, {}, [], "2024-01-15"),
            timeout=5,
        )

        assert result["delta_views_vs_yesterday"] == 0

    @pytest.mark.asyncio
    async def test_build_report_delta_views_exception_returns_zero(self):
        agent = _make_reporting_agent()
        agent.memory.get_listing_prev_views = AsyncMock(side_effect=RuntimeError("DB error"))
        synced = [{"listing_id": "L1", "views": 15}]

        result = await asyncio.wait_for(
            agent._build_report([], synced, {}, [], "2024-01-15"),
            timeout=5,
        )

        assert result["delta_views_vs_yesterday"] == 0

    # ── _send_daily_summary ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_send_daily_summary_with_bestseller_and_winner(self):
        broadcast = AsyncMock()
        agent = _make_reporting_agent(telegram_broadcast=broadcast)

        await asyncio.wait_for(
            agent._send_daily_summary(_SAMPLE_REPORT, "2024-01-15"),
            timeout=5,
        )

        broadcast.assert_awaited_once()
        msg = broadcast.call_args[0][0]
        assert "Etsy — 2024-01-15" in msg
        assert "+25" in msg
        assert "A/B: variante A vince" in msg
        assert "Printable Wall Art Watercolor" in msg
        assert "Da ottimizzare" in msg
        assert "senza views >7gg" in msg
        assert "senza conversioni >45gg" in msg

    @pytest.mark.asyncio
    async def test_send_daily_summary_no_bestseller(self):
        broadcast = AsyncMock()
        agent = _make_reporting_agent(telegram_broadcast=broadcast)
        report = {**_SAMPLE_REPORT, "bestsellers": [], "failures": {}, "ab_performance": {"winner": None}}

        await asyncio.wait_for(
            agent._send_daily_summary(report, "2024-01-15"),
            timeout=5,
        )

        msg = broadcast.call_args[0][0]
        assert "nessuno" in msg

    @pytest.mark.asyncio
    async def test_send_daily_summary_ab_inconclusive(self):
        broadcast = AsyncMock()
        agent = _make_reporting_agent(telegram_broadcast=broadcast)
        report = {
            **_SAMPLE_REPORT,
            "delta_views_vs_yesterday": -10,
            "bestsellers": [],
            "failures": {},
            "ab_performance": {"winner": "inconclusive"},
        }

        await asyncio.wait_for(
            agent._send_daily_summary(report, "2024-01-15"),
            timeout=5,
        )

        msg = broadcast.call_args[0][0]
        assert "A/B: dati insufficienti" in msg
        assert "-10" in msg

    @pytest.mark.asyncio
    async def test_send_daily_summary_only_no_views_failures(self):
        broadcast = AsyncMock()
        agent = _make_reporting_agent(telegram_broadcast=broadcast)
        report = {
            **_SAMPLE_REPORT,
            "failures": {"no_views": 2},
            "bestsellers": [],
            "ab_performance": {"winner": None},
        }

        await asyncio.wait_for(
            agent._send_daily_summary(report, "2024-01-15"),
            timeout=5,
        )

        msg = broadcast.call_args[0][0]
        assert "senza views >7gg" in msg

    @pytest.mark.asyncio
    async def test_send_daily_summary_only_no_conversion_failures(self):
        broadcast = AsyncMock()
        agent = _make_reporting_agent(telegram_broadcast=broadcast)
        report = {
            **_SAMPLE_REPORT,
            "failures": {"no_conversion": 3},
            "bestsellers": [],
            "ab_performance": {"winner": None},
        }

        await asyncio.wait_for(
            agent._send_daily_summary(report, "2024-01-15"),
            timeout=5,
        )

        msg = broadcast.call_args[0][0]
        assert "senza conversioni >45gg" in msg

    # ── _notify_telegram ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_notify_telegram_calls_broadcast(self):
        broadcast = AsyncMock()
        agent = _make_reporting_agent(telegram_broadcast=broadcast)

        await asyncio.wait_for(agent._notify_telegram("test message"), timeout=5)

        broadcast.assert_awaited_once_with("test message")

    @pytest.mark.asyncio
    async def test_notify_telegram_no_broadcast_does_nothing(self):
        agent = _make_reporting_agent(telegram_broadcast=None)

        await asyncio.wait_for(agent._notify_telegram("test"), timeout=5)

    @pytest.mark.asyncio
    async def test_notify_telegram_broadcast_exception_swallowed(self):
        broadcast = AsyncMock(side_effect=RuntimeError("Network error"))
        agent = _make_reporting_agent(telegram_broadcast=broadcast)

        # Must not raise
        await asyncio.wait_for(agent._notify_telegram("test"), timeout=5)
        broadcast.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────
# TestFailureMixin
# ─────────────────────────────────────────────────────────────────────────────

class TestFailureMixin:

    # ── _analyze_no_views ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_analyze_no_views_full_flow(self):
        agent = _make_failure_agent()

        await asyncio.wait_for(agent._analyze_no_views(_SAMPLE_LISTING), timeout=5)

        agent.memory.flag_no_views.assert_awaited_once_with("L-001")
        agent.memory.store_insight.assert_awaited_once()
        agent.memory.save_listing_analysis.assert_awaited_once()
        agent._notify_telegram.assert_awaited_once()
        kw = agent.memory.save_listing_analysis.call_args.kwargs
        assert kw["listing_id"] == "L-001"
        assert kw["analysis_type"] == "no_views"
        assert kw["cause"] == _VALID_ANALYSIS["cause"]

    @pytest.mark.asyncio
    async def test_analyze_no_views_llm_returns_none_early_return(self):
        agent = _make_failure_agent()
        agent._call_llm = AsyncMock(return_value="not valid json {{{")

        await asyncio.wait_for(agent._analyze_no_views({"listing_id": "L-002", "niche": "art"}), timeout=5)

        agent.memory.flag_no_views.assert_awaited_once_with("L-002")
        agent.memory.save_listing_analysis.assert_not_called()
        agent._notify_telegram.assert_not_called()

    @pytest.mark.asyncio
    async def test_analyze_no_views_telegram_message_content(self):
        agent = _make_failure_agent()

        await asyncio.wait_for(agent._analyze_no_views(_SAMPLE_LISTING), timeout=5)

        msg = agent._notify_telegram.call_args[0][0]
        assert "visibilità" in msg
        assert "Printable Watercolor Art" in msg
        assert "#no_views" in msg

    # ── _no_views_prompt ──────────────────────────────────────────────────────

    def test_no_views_prompt_with_list_tags(self):
        prompt = _AnalyticsFailureMixin._no_views_prompt({
            "title": "Test Listing",
            "tags": ["art", "printable", "wall"],
            "niche": "art",
            "price_eur": 4.99,
            "size": "A4",
            "template": "minimal",
        })
        assert "art, printable, wall" in prompt
        assert "Test Listing" in prompt
        assert "Rispondi SOLO con JSON:" in prompt

    def test_no_views_prompt_with_json_string_tags(self):
        prompt = _AnalyticsFailureMixin._no_views_prompt({
            "title": "Test",
            "tags": '["tag1", "tag2"]',
            "niche": "art",
        })
        assert "tag1" in prompt
        assert "tag2" in prompt

    def test_no_views_prompt_with_plain_string_tag(self):
        prompt = _AnalyticsFailureMixin._no_views_prompt({
            "title": "Test",
            "tags": "singletag",
            "niche": "art",
        })
        assert "singletag" in prompt

    def test_no_views_prompt_with_none_tags(self):
        prompt = _AnalyticsFailureMixin._no_views_prompt({
            "title": "Test",
            "tags": None,
            "niche": "art",
        })
        assert "cause" in prompt  # JSON schema present

    # ── _analyze_no_conversion ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_analyze_no_conversion_below_threshold_skips(self):
        agent = _make_failure_agent()
        listing = {"listing_id": "L-003", "views": 10, "niche": "art"}

        await asyncio.wait_for(agent._analyze_no_conversion(listing), timeout=5)

        agent.memory.flag_no_conversion.assert_awaited_once_with("L-003")
        agent.memory.save_listing_analysis.assert_not_called()
        agent._notify_telegram.assert_not_called()

    @pytest.mark.asyncio
    async def test_analyze_no_conversion_above_threshold_full_flow(self):
        agent = _make_failure_agent()
        listing = {
            "listing_id": "L-004",
            "views": 50,
            "favorites": 5,
            "niche": "home_decor",
            "title": "Cozy Home Print",
            "tags": ["home", "cozy"],
            "price_eur": 9.99,
            "ab_price_variant": "B",
        }

        await asyncio.wait_for(agent._analyze_no_conversion(listing), timeout=5)

        agent.memory.flag_no_conversion.assert_awaited_once_with("L-004")
        agent.memory.save_listing_analysis.assert_awaited_once()
        agent._notify_telegram.assert_awaited_once()
        kw = agent.memory.save_listing_analysis.call_args.kwargs
        assert kw["analysis_type"] == "no_conversion"

    @pytest.mark.asyncio
    async def test_analyze_no_conversion_telegram_message_content(self):
        agent = _make_failure_agent()
        listing = {
            "listing_id": "L-004",
            "views": 50,
            "favorites": 5,
            "niche": "home_decor",
            "title": "Cozy Home Print",
            "tags": [],
            "price_eur": 9.99,
        }

        await asyncio.wait_for(agent._analyze_no_conversion(listing), timeout=5)

        msg = agent._notify_telegram.call_args[0][0]
        assert "conversione" in msg
        assert "Cozy Home Print" in msg
        assert "#no_conversion" in msg

    @pytest.mark.asyncio
    async def test_analyze_no_conversion_llm_returns_none(self):
        agent = _make_failure_agent()
        agent._call_llm = AsyncMock(return_value="invalid $$")
        listing = {"listing_id": "L-005", "views": 50, "niche": "art"}

        await asyncio.wait_for(agent._analyze_no_conversion(listing), timeout=5)

        agent.memory.flag_no_conversion.assert_awaited()
        agent.memory.save_listing_analysis.assert_not_called()

    # ── _no_conversion_prompt ─────────────────────────────────────────────────

    def test_no_conversion_prompt_with_list_tags(self):
        prompt = _AnalyticsFailureMixin._no_conversion_prompt({
            "title": "Art Print",
            "tags": ["art", "minimal"],
            "niche": "art",
            "price_eur": 7.99,
            "ab_price_variant": "A",
            "views": 80,
            "favorites": 3,
        })
        assert "art, minimal" in prompt
        assert "80 visualizzazioni" in prompt
        assert "3 preferiti" in prompt

    def test_no_conversion_prompt_with_json_string_tags(self):
        prompt = _AnalyticsFailureMixin._no_conversion_prompt({
            "tags": '["t1", "t2"]',
            "views": 30,
            "favorites": 1,
        })
        assert "t1" in prompt

    def test_no_conversion_prompt_with_plain_string_tag(self):
        prompt = _AnalyticsFailureMixin._no_conversion_prompt({
            "tags": "singletag",
            "views": 30,
            "favorites": 1,
        })
        assert "singletag" in prompt

    # ── _analyze_no_views_no_sales ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_analyze_no_views_no_sales_full_flow(self):
        agent = _make_failure_agent()
        listing = {
            "listing_id": "L-006",
            "title": "Stale Listing",
            "niche": "seasonal",
            "tags": [],
            "price_eur": 3.99,
        }

        await asyncio.wait_for(agent._analyze_no_views_no_sales(listing), timeout=5)

        agent.memory.flag_no_views_no_sales.assert_awaited_once_with("L-006")
        agent.memory.save_listing_analysis.assert_awaited_once()
        agent._notify_telegram.assert_awaited_once()
        kw = agent.memory.save_listing_analysis.call_args.kwargs
        assert kw["analysis_type"] == "no_views_no_sales"

    @pytest.mark.asyncio
    async def test_analyze_no_views_no_sales_telegram_content(self):
        agent = _make_failure_agent()
        listing = {
            "listing_id": "L-006",
            "title": "Stale Listing",
            "niche": "seasonal",
            "tags": [],
        }

        await asyncio.wait_for(agent._analyze_no_views_no_sales(listing), timeout=5)

        msg = agent._notify_telegram.call_args[0][0]
        assert "archiviare" in msg
        assert "Stale Listing" in msg
        assert "#no_views_no_sales" in msg

    @pytest.mark.asyncio
    async def test_analyze_no_views_no_sales_llm_returns_none(self):
        agent = _make_failure_agent()
        agent._call_llm = AsyncMock(return_value="")
        listing = {"listing_id": "L-007", "niche": "art"}

        await asyncio.wait_for(agent._analyze_no_views_no_sales(listing), timeout=5)

        agent.memory.flag_no_views_no_sales.assert_awaited_once_with("L-007")
        agent.memory.save_listing_analysis.assert_not_called()

    # ── _no_views_no_sales_prompt ─────────────────────────────────────────────

    def test_no_views_no_sales_prompt_list_tags(self):
        prompt = _AnalyticsFailureMixin._no_views_no_sales_prompt({
            "title": "Zero Listing",
            "tags": ["stale", "unused"],
            "niche": "seasonal",
            "price_eur": 2.99,
        })
        assert "stale, unused" in prompt
        assert "Zero Listing" in prompt

    def test_no_views_no_sales_prompt_json_string_tags(self):
        prompt = _AnalyticsFailureMixin._no_views_no_sales_prompt({"tags": '["t1"]'})
        assert "t1" in prompt

    def test_no_views_no_sales_prompt_plain_string_tag(self):
        prompt = _AnalyticsFailureMixin._no_views_no_sales_prompt({"tags": "onetag"})
        assert "onetag" in prompt

    # ── _fetch_similar_failures ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_fetch_similar_failures_with_matching_docs(self):
        agent = _make_failure_agent()
        agent.memory.query_chromadb_recent = AsyncMock(return_value=[
            {"document": "FAILURE no_views | cause: tag troppo generici | avoid: non ripetere"},
            {"document": "FAILURE no_views | cause: titolo debole | avoid: usa specifici"},
        ])

        result = await asyncio.wait_for(
            agent._fetch_similar_failures("wall_art", "no_views"),
            timeout=5,
        )

        assert "CONTESTO STORICO" in result
        assert "cause:" in result
        assert "avoid:" in result

    @pytest.mark.asyncio
    async def test_fetch_similar_failures_empty_results(self):
        agent = _make_failure_agent()
        agent.memory.query_chromadb_recent = AsyncMock(return_value=[])

        result = await asyncio.wait_for(
            agent._fetch_similar_failures("art", "no_views"),
            timeout=5,
        )

        assert result == ""

    @pytest.mark.asyncio
    async def test_fetch_similar_failures_docs_without_required_fields(self):
        agent = _make_failure_agent()
        agent.memory.query_chromadb_recent = AsyncMock(return_value=[
            {"document": "FAILURE no_views | incomplete doc without required fields"},
        ])

        result = await asyncio.wait_for(
            agent._fetch_similar_failures("art", "no_views"),
            timeout=5,
        )

        assert result == ""

    @pytest.mark.asyncio
    async def test_fetch_similar_failures_exception_returns_empty(self):
        agent = _make_failure_agent()
        agent.memory.query_chromadb_recent = AsyncMock(side_effect=RuntimeError("ChromaDB down"))

        result = await asyncio.wait_for(
            agent._fetch_similar_failures("art", "no_views"),
            timeout=5,
        )

        assert result == ""

    # ── _failure_llm ──────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_failure_llm_no_historical_context(self):
        agent = _make_failure_agent()
        base_prompt = "Analizza listing.\nRispondi SOLO con JSON:\n{...}"

        result = await asyncio.wait_for(
            agent._failure_llm(prompt=base_prompt),
            timeout=5,
        )

        assert result == _VALID_ANALYSIS
        agent._call_llm.assert_awaited_once()
        sent = agent._call_llm.call_args.kwargs["messages"][0]["content"]
        assert sent == base_prompt

    @pytest.mark.asyncio
    async def test_failure_llm_context_inserted_before_json_marker(self):
        agent = _make_failure_agent()
        base_prompt = "Analizza listing.\nRispondi SOLO con JSON:\n{...}"
        context = "\nSTORICO: vecchio fallimento\n"

        result = await asyncio.wait_for(
            agent._failure_llm(prompt=base_prompt, historical_context=context),
            timeout=5,
        )

        assert result == _VALID_ANALYSIS
        sent = agent._call_llm.call_args.kwargs["messages"][0]["content"]
        assert "STORICO" in sent
        assert sent.index("STORICO") < sent.index("Rispondi SOLO con JSON:")

    @pytest.mark.asyncio
    async def test_failure_llm_context_appended_when_no_marker(self):
        agent = _make_failure_agent()
        base_prompt = "Analizza listing senza marker speciale."
        context = "\nSTORICO extra\n"

        result = await asyncio.wait_for(
            agent._failure_llm(prompt=base_prompt, historical_context=context),
            timeout=5,
        )

        assert result == _VALID_ANALYSIS
        sent = agent._call_llm.call_args.kwargs["messages"][0]["content"]
        assert "STORICO extra" in sent

    @pytest.mark.asyncio
    async def test_failure_llm_invalid_json_returns_none(self):
        agent = _make_failure_agent()
        agent._call_llm = AsyncMock(return_value="not json at all @@@@")

        result = await asyncio.wait_for(
            agent._failure_llm(prompt="some prompt"),
            timeout=5,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_failure_llm_markdown_fenced_json(self):
        agent = _make_failure_agent()
        fenced = f"```json\n{_VALID_JSON_STR}\n```"
        agent._call_llm = AsyncMock(return_value=fenced)

        result = await asyncio.wait_for(
            agent._failure_llm(prompt="some prompt"),
            timeout=5,
        )

        assert result == _VALID_ANALYSIS

    # ── _save_failure_chromadb ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_save_failure_chromadb_stores_and_returns_id(self):
        agent = _make_failure_agent()
        listing = {
            "listing_id": "L-010",
            "niche": "wall_art",
            "template": "watercolor",
        }
        analysis = {
            "cause": "Keyword troppo generiche",
            "recommendations": ["Fix tag", "Fix title", "Fix price"],
            "avoid_in_future": "Non usare tag generici",
        }

        result = await asyncio.wait_for(
            agent._save_failure_chromadb(
                listing=listing,
                failure_type="no_views",
                analysis=analysis,
            ),
            timeout=5,
        )

        assert result == "chroma-id-001"
        agent.memory.store_insight.assert_awaited_once()
        kw = agent.memory.store_insight.call_args.kwargs
        assert "FAILURE no_views" in kw["text"]
        assert "wall_art" in kw["text"]
        assert "Keyword troppo generiche" in kw["text"]
        assert kw["metadata"]["failure_type"] == "no_views"
        assert kw["metadata"]["niche"] == "wall_art"
        assert kw["metadata"]["type"] == "failure_analysis"
        assert kw["metadata"]["template"] == "watercolor"

    @pytest.mark.asyncio
    async def test_save_failure_chromadb_recommendations_joined(self):
        agent = _make_failure_agent()
        listing = {"listing_id": "L-011", "niche": "art", "template": "minimal"}
        analysis = {
            "cause": "Titolo debole",
            "recommendations": ["Rec A", "Rec B", "Rec C"],
            "avoid_in_future": "Evita titoli generici",
        }

        await asyncio.wait_for(
            agent._save_failure_chromadb(listing=listing, failure_type="no_conversion", analysis=analysis),
            timeout=5,
        )

        kw = agent.memory.store_insight.call_args.kwargs
        assert "Rec A; Rec B; Rec C" in kw["text"]
