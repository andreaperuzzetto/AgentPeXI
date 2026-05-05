"""Tests for analytics reporting/diagnostics pure methods and constants."""
from __future__ import annotations

import time

import pytest

from apps.backend.agents._analytics.constants import (
    VIEWS_MIN_7DAYS,
    CTR_MIN,
    CONV_MIN,
    MIN_DAYS_LIVE,
    REMEDIATION_COOLDOWN_HOURS,
)
from apps.backend.agents._analytics.reporting_mixin import _AnalyticsReportingMixin
from apps.backend.agents._analytics.diagnostics_mixin import _AnalyticsDiagnosticsMixin

# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------

def test_views_min_positive():
    assert VIEWS_MIN_7DAYS > 0


def test_ctr_min_between_0_and_1():
    assert 0 < CTR_MIN < 1


def test_conv_min_between_0_and_1():
    assert 0 < CONV_MIN < 1


def test_min_days_live_positive():
    assert MIN_DAYS_LIVE >= 1


def test_cooldown_hours_positive():
    assert REMEDIATION_COOLDOWN_HOURS > 0


# ---------------------------------------------------------------------------
# _calculate_analytics_confidence (pure sync method on mixin)
# ---------------------------------------------------------------------------

class _FakeReporting(_AnalyticsReportingMixin):
    pass


@pytest.fixture
def reporter():
    return _FakeReporting()


def test_confidence_no_listings(reporter):
    score, missing = reporter._calculate_analytics_confidence([], [], {})
    assert score == pytest.approx(1.0, abs=0.05)  # empty listings → full sync + full missing
    assert isinstance(missing, list)


def test_confidence_all_synced_with_sales(reporter):
    listings = [{"id": 1}, {"id": 2}]
    synced = [{"id": 1, "sales": 3}, {"id": 2, "sales": 0}]  # one with real sales
    score, missing = reporter._calculate_analytics_confidence(listings, synced, {})
    assert score >= 0.8


def test_confidence_partial_sync(reporter):
    listings = [{"id": 1}, {"id": 2}]
    synced = [{"id": 1, "sales": 1}]
    score_full, _ = reporter._calculate_analytics_confidence(listings, listings, {})
    score_partial, _ = reporter._calculate_analytics_confidence(listings, synced, {})
    assert score_partial < score_full


def test_confidence_synced_no_sales(reporter):
    listings = [{"id": 1}]
    synced = [{"id": 1, "sales": 0}]
    score, missing = reporter._calculate_analytics_confidence(listings, synced, {})
    assert score < 1.0
    assert any("0" in m or "vendite" in m.lower() for m in missing)


def test_confidence_returns_tuple(reporter):
    result = reporter._calculate_analytics_confidence([], [], {})
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_confidence_score_between_0_and_1(reporter):
    listings = [{"id": i} for i in range(5)]
    synced = [{"id": i, "sales": 0} for i in range(3)]
    score, _ = reporter._calculate_analytics_confidence(listings, synced, {})
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# _AnalyticsDiagnosticsMixin — pure sync methods
# ---------------------------------------------------------------------------

class _FakeDiagnostics(_AnalyticsDiagnosticsMixin):
    def __init__(self):
        self._remediation_log: dict = {}


@pytest.fixture
def diag():
    return _FakeDiagnostics()


def test_remediation_not_recently_empty_log(diag):
    assert not diag._remediation_attempted_recently(1, "price_drop")


def test_remediation_not_recently_after_long_time(diag):
    diag._remediation_log[1] = {"price_drop": time.time() - 3600 * (REMEDIATION_COOLDOWN_HOURS + 1)}
    assert not diag._remediation_attempted_recently(1, "price_drop")


def test_remediation_recently_after_just_logged(diag):
    diag._log_remediation_attempt(1, "price_drop")
    assert diag._remediation_attempted_recently(1, "price_drop")


def test_remediation_recently_different_action(diag):
    diag._log_remediation_attempt(1, "price_drop")
    assert not diag._remediation_attempted_recently(1, "title_rewrite")


def test_log_remediation_creates_entry(diag):
    diag._log_remediation_attempt(42, "new_action")
    assert 42 in diag._remediation_log
    assert "new_action" in diag._remediation_log[42]


def test_log_remediation_updates_timestamp(diag):
    diag._log_remediation_attempt(1, "action")
    ts1 = diag._remediation_log[1]["action"]
    time.sleep(0.01)
    diag._log_remediation_attempt(1, "action")
    ts2 = diag._remediation_log[1]["action"]
    assert ts2 >= ts1


def test_log_remediation_multiple_items(diag):
    diag._log_remediation_attempt(1, "action_a")
    diag._log_remediation_attempt(2, "action_b")
    assert 1 in diag._remediation_log
    assert 2 in diag._remediation_log
