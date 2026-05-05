"""Tests for pipeline pure functions and AgentBase static methods."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from apps.backend.core._pepe._pipeline import _format_analytics_summary
from apps.backend.agents.base import AgentBase
from apps.backend.core.models import AgentTask


# ---------------------------------------------------------------------------
# _format_analytics_summary — pure module-level function
# ---------------------------------------------------------------------------

def test_format_analytics_summary_basic():
    result = _format_analytics_summary({
        "total_views": 150,
        "total_favorites": 20,
        "total_sales": 5,
        "total_revenue_eur": 49.95,
        "delta_views_vs_yesterday": 10,
        "total_listings_active": 8,
        "drafts": 2,
    })
    assert "Views: 150" in result
    assert "Vendite: 5" in result
    assert "49.95" in result
    assert "+10" in result


def test_format_analytics_summary_negative_delta():
    result = _format_analytics_summary({"delta_views_vs_yesterday": -5})
    assert "-5" in result


def test_format_analytics_summary_with_bestseller():
    result = _format_analytics_summary({
        "bestsellers": [{"title": "Budget Planner 2025", "sales": 12}],
    })
    assert "Budget Planner 2025" in result
    assert "12 vendite" in result


def test_format_analytics_summary_no_bestsellers():
    result = _format_analytics_summary({})
    assert "nessuno" in result


def test_format_analytics_summary_ab_winner():
    result = _format_analytics_summary({
        "ab_performance": {"winner": "A", "winner_confidence": "high"},
    })
    assert "A/B" in result
    assert "vince" in result


def test_format_analytics_summary_ab_inconclusive():
    result = _format_analytics_summary({
        "ab_performance": {"winner": "inconclusive"},
    })
    assert "insufficienti" in result


def test_format_analytics_summary_with_failures():
    result = _format_analytics_summary({
        "failures": {"no_views": 3, "no_conversion": 2},
    })
    assert "ottimizzare" in result
    assert "3" in result


def test_format_analytics_summary_returns_string():
    result = _format_analytics_summary({})
    assert isinstance(result, str)
    assert len(result) > 5


def test_format_analytics_summary_with_date():
    result = _format_analytics_summary({"date": "2024-01-15"})
    assert "2024-01-15" in result


# ---------------------------------------------------------------------------
# AgentBase._format_rel_time — static method
# ---------------------------------------------------------------------------

def test_format_rel_time_few_seconds():
    dt = datetime.now() + timedelta(seconds=30)
    result = AgentBase._format_rel_time(dt)
    assert "pochi secondi" in result


def test_format_rel_time_one_minute():
    dt = datetime.now() + timedelta(minutes=1, seconds=5)
    result = AgentBase._format_rel_time(dt)
    assert "un minuto" in result


def test_format_rel_time_several_minutes():
    dt = datetime.now() + timedelta(minutes=15, seconds=5)
    result = AgentBase._format_rel_time(dt)
    assert "15 minuti" in result


def test_format_rel_time_one_hour():
    dt = datetime.now() + timedelta(hours=1, seconds=10)
    result = AgentBase._format_rel_time(dt)
    assert "un'ora" in result


def test_format_rel_time_several_hours():
    dt = datetime.now() + timedelta(hours=3)
    result = AgentBase._format_rel_time(dt)
    assert "ore" in result


def test_format_rel_time_hours_and_minutes():
    dt = datetime.now() + timedelta(hours=2, minutes=30)
    result = AgentBase._format_rel_time(dt)
    assert "ore" in result and "minuti" in result


def test_format_rel_time_today():
    dt = datetime.now() + timedelta(hours=18)
    if dt.date() == datetime.now().date():
        result = AgentBase._format_rel_time(dt)
        assert "oggi" in result or "ore" in result


def test_format_rel_time_tomorrow():
    dt = datetime.now() + timedelta(days=1)
    result = AgentBase._format_rel_time(dt)
    assert "domani" in result or "lunedì" in result or "martedì" in result or "ore" in result or "il" in result


def test_format_rel_time_past():
    dt = datetime.now() - timedelta(days=1)
    result = AgentBase._format_rel_time(dt)
    assert "/" in result  # fallback: "il 25/04 alle 10:00"


def test_format_rel_time_returns_string():
    dt = datetime.now() + timedelta(minutes=5)
    assert isinstance(AgentBase._format_rel_time(dt), str)


# ---------------------------------------------------------------------------
# AgentBase._task_description — static method
# ---------------------------------------------------------------------------

def _make_task(**kwargs) -> AgentTask:
    defaults = {"agent_name": "research", "input_data": {}}
    defaults.update(kwargs)
    return AgentTask(**defaults)


def test_task_description_query_field():
    task = _make_task(input_data={"query": "budget planner ideas"})
    result = AgentBase._task_description(task)
    assert "budget planner ideas" in result


def test_task_description_empty_input():
    task = _make_task(input_data={})
    result = AgentBase._task_description(task)
    assert task.task_id[:8] in result


def test_task_description_none_input():
    task = _make_task(input_data=None)
    result = AgentBase._task_description(task)
    assert isinstance(result, str)


def test_task_description_niches_list():
    task = _make_task(input_data={"niches": ["planner", "journal", "tracker"]})
    result = AgentBase._task_description(task)
    assert "planner" in result


def test_task_description_truncates_long():
    task = _make_task(input_data={"query": "A" * 200})
    result = AgentBase._task_description(task)
    assert len(result) <= 80


def test_task_description_returns_string():
    task = _make_task(input_data={"action": "publish"})
    assert isinstance(AgentBase._task_description(task), str)


# ---------------------------------------------------------------------------
# AgentBase._estimate_cost — static method
# ---------------------------------------------------------------------------

def test_estimate_cost_sonnet_basic():
    cost = AgentBase._estimate_cost("claude-sonnet-3-5", 1000, 200)
    assert isinstance(cost, float)
    assert cost >= 0


def test_estimate_cost_haiku_cheaper_than_sonnet():
    haiku = AgentBase._estimate_cost("claude-haiku-3", 1000, 200)
    sonnet = AgentBase._estimate_cost("claude-sonnet-3-5", 1000, 200)
    assert haiku <= sonnet


def test_estimate_cost_zero_tokens():
    cost = AgentBase._estimate_cost("claude-sonnet-3-5", 0, 0)
    assert cost == 0.0


def test_estimate_cost_fallback_model():
    cost = AgentBase._estimate_cost("unknown-model", 1000, 500)
    assert isinstance(cost, float)
    assert cost >= 0


def test_estimate_cost_with_cache():
    without_cache = AgentBase._estimate_cost("claude-sonnet-3-5", 1000, 200, 0, 0)
    with_cache = AgentBase._estimate_cost("claude-sonnet-3-5", 1000, 200, 500, 100)
    assert with_cache >= 0  # cache adds cost
