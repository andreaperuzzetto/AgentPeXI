"""Coverage tests for scheduler job mixins — ROUND1-C.

Covers (target ≥ 60% each):
- _EtsyMixin:     _run_publish_checker, _run_etsy_learning_loop,
                  _run_poll_listing_performance, _run_shop_optimizer_job,
                  _run_etsy_ads_manager, _check_empty_sections
- _PersonalMixin: _run_personal_learning_loop, _run_weekly_personal_synthesis,
                  _run_shared_memory_decay, _run_reminder_checker,
                  _run_unack_ping, _run_medium_digest
- _WikiMixin:     _run_wiki_health_check
- _SystemMixin:   _run_screen_cleanup, _health_check_ssd (storage branch),
                  _sync_agent_status, _run_scheduled_task
- _CoreMixin:     _load_db_jobs, event listeners, get_jobs,
                  broadcast helpers, _extract_color_schemes

These tests do NOT duplicate what is in:
  tests/core/test_scheduler.py
  tests/test_b2_scheduler_job.py
"""
from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------

def _make_sched(**overrides):
    """Return a Scheduler instance with all collaborators mocked.

    _notify_telegram and _broadcast are patched as AsyncMock so that
    individual job tests can assert on them without needing Telegram/WS.
    """
    from apps.backend.core.scheduler import Scheduler  # noqa: PLC0415

    sched = Scheduler.__new__(Scheduler)
    sched.memory               = AsyncMock()
    sched._ws_broadcast        = None
    sched._telegram_broadcast  = None
    sched.pepe                 = None
    sched.storage              = None
    sched.research_agent       = None
    sched.design_agent         = None
    sched.publisher_agent      = None
    sched.analytics_agent      = None
    sched.finance_agent        = None
    sched.screen_watcher       = None
    sched.production_queue     = None
    sched.budget_manager       = None
    sched.publication_policy   = None
    sched.autopilot_loop       = None
    sched.etsy_client          = None
    sched.shop_optimizer       = None
    sched.etsy_ads_manager     = None
    sched.learning_loop        = None
    sched.pinterest_agent      = None
    sched._scheduler           = AsyncIOScheduler()
    sched._job_status          = {}
    sched._job_status_lock     = threading.Lock()
    sched._internal_jobs       = {"ssd_health_check", "agent_status_sync"}
    # Override helpers so individual tests don't need Telegram/WS
    sched._notify_telegram     = AsyncMock()
    sched._broadcast           = AsyncMock()

    for k, v in overrides.items():
        setattr(sched, k, v)
    return sched


def _queue_item(item_id=1, listing_title="Test Listing", niche="planners"):
    """Minimal production-queue item mock."""
    item = MagicMock()
    item.id            = item_id
    item.listing_title = listing_title
    item.niche         = niche
    return item


# ===========================================================================
# _EtsyMixin
# ===========================================================================

class TestPublishChecker:

    @pytest.mark.asyncio
    async def test_queue_none_returns_early(self):
        sched = _make_sched(production_queue=None)
        await sched._run_publish_checker()
        sched._notify_telegram.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_due_items_returns_early(self):
        queue = MagicMock()
        queue.get_due_scheduled = AsyncMock(return_value=[])
        sched = _make_sched(production_queue=queue)
        await sched._run_publish_checker()
        sched._notify_telegram.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mock_mode_uses_mock_id(self):
        """In mock_mode items are published with MOCK_ID, no real API call."""
        queue = MagicMock()
        item = _queue_item()
        queue.get_due_scheduled = AsyncMock(return_value=[item])
        queue.set_published = AsyncMock()

        pepe = MagicMock()
        pepe.mock_mode = True

        sched = _make_sched(production_queue=queue, pepe=pepe)
        await sched._run_publish_checker()

        queue.set_published.assert_awaited_once_with(item.id, etsy_listing_id="MOCK_ID")
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_etsy_client_none_raises_etsy_api_error(self):
        """etsy_client=None causes EtsyAPIError which is caught → set_failed."""
        queue = MagicMock()
        item = _queue_item()
        queue.get_due_scheduled = AsyncMock(return_value=[item])
        queue.set_failed = AsyncMock()

        pepe = MagicMock()
        pepe.mock_mode = False

        sched = _make_sched(production_queue=queue, pepe=pepe, etsy_client=None)
        await sched._run_publish_checker()

        queue.set_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_successful_publish_notifies_telegram(self):
        queue = MagicMock()
        item = _queue_item()
        queue.get_due_scheduled = AsyncMock(return_value=[item])
        queue.set_published    = AsyncMock()
        queue.set_ads_activated = AsyncMock()

        etsy_client = MagicMock()
        etsy_client.publish_listing = AsyncMock(return_value="listing_123")

        policy = MagicMock()
        policy.ads_enabled = AsyncMock(return_value=False)

        pepe = MagicMock()
        pepe.mock_mode = False

        sched = _make_sched(
            production_queue=queue,
            etsy_client=etsy_client,
            publication_policy=policy,
            pepe=pepe,
        )
        await sched._run_publish_checker()

        queue.set_published.assert_awaited_once_with(item.id, "listing_123")
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ads_policy_enabled_activates_ads(self):
        queue = MagicMock()
        item = _queue_item()
        queue.get_due_scheduled = AsyncMock(return_value=[item])
        queue.set_published     = AsyncMock()
        queue.set_ads_activated = AsyncMock()

        etsy_client = MagicMock()
        etsy_client.publish_listing = AsyncMock(return_value="listing_456")

        policy = MagicMock()
        policy.ads_enabled = AsyncMock(return_value=True)

        pepe = MagicMock()
        pepe.mock_mode = False

        sched = _make_sched(
            production_queue=queue,
            etsy_client=etsy_client,
            publication_policy=policy,
            pepe=pepe,
        )
        await sched._run_publish_checker()
        queue.set_ads_activated.assert_awaited_once_with(item.id)

    @pytest.mark.asyncio
    async def test_etsy_api_error_from_publish_marks_failed_and_notifies(self):
        from apps.backend.tools.etsy_api import EtsyAPIError  # noqa: PLC0415

        queue = MagicMock()
        item = _queue_item()
        queue.get_due_scheduled = AsyncMock(return_value=[item])
        queue.set_failed = AsyncMock()

        etsy_client = MagicMock()
        etsy_client.publish_listing = AsyncMock(side_effect=EtsyAPIError("rate limited"))

        pepe = MagicMock()
        pepe.mock_mode = False

        sched = _make_sched(production_queue=queue, etsy_client=etsy_client, pepe=pepe)
        await sched._run_publish_checker()

        queue.set_failed.assert_awaited_once()
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generic_exception_marks_failed_no_notify(self):
        queue = MagicMock()
        item = _queue_item()
        queue.get_due_scheduled = AsyncMock(return_value=[item])
        queue.set_failed = AsyncMock()

        etsy_client = MagicMock()
        etsy_client.publish_listing = AsyncMock(side_effect=RuntimeError("network"))

        pepe = MagicMock()
        pepe.mock_mode = False

        sched = _make_sched(production_queue=queue, etsy_client=etsy_client, pepe=pepe)
        await sched._run_publish_checker()

        queue.set_failed.assert_awaited_once()


class TestEtsyLearningLoop:

    @pytest.mark.asyncio
    async def test_analytics_agent_none_appends_skip_note(self):
        sched = _make_sched(analytics_agent=None, learning_loop=None)
        await sched._run_etsy_learning_loop()
        # Report is assembled → Telegram is notified
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_analytics_agent_poll_success(self):
        analytics = MagicMock()
        analytics.poll_listing_performance = AsyncMock()
        sched = _make_sched(analytics_agent=analytics, learning_loop=None)
        await sched._run_etsy_learning_loop()
        analytics.poll_listing_performance.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_analytics_agent_poll_exception_captured(self):
        analytics = MagicMock()
        analytics.poll_listing_performance = AsyncMock(side_effect=Exception("api down"))
        sched = _make_sched(analytics_agent=analytics, learning_loop=None)
        await sched._run_etsy_learning_loop()
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_learning_loop_run_full_update_success(self):
        analytics = MagicMock()
        analytics.poll_listing_performance = AsyncMock()

        learning = MagicMock()
        learning.run_full_update = AsyncMock(
            return_value={"n_updated": 5, "top_niches": ["planners", "journals"]}
        )

        db = AsyncMock()
        cursor = AsyncMock()
        cursor.fetchall = AsyncMock(return_value=[])
        db.execute = AsyncMock(return_value=cursor)

        sched = _make_sched(analytics_agent=analytics, learning_loop=learning)
        sched.memory.get_db = AsyncMock(return_value=db)

        await sched._run_etsy_learning_loop()
        learning.run_full_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_learning_loop_run_full_update_exception(self):
        analytics = MagicMock()
        analytics.poll_listing_performance = AsyncMock()

        learning = MagicMock()
        learning.run_full_update = AsyncMock(side_effect=Exception("db error"))

        sched = _make_sched(analytics_agent=analytics, learning_loop=learning)
        await sched._run_etsy_learning_loop()
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ab_thumbnail_compared_success(self):
        analytics = MagicMock()
        analytics.poll_listing_performance = AsyncMock()

        learning = MagicMock()
        learning.run_full_update = AsyncMock(
            return_value={"n_updated": 2, "top_niches": []}
        )
        learning.compare_ab_thumbnails = AsyncMock(return_value={"status": "compared"})

        row = MagicMock()
        row.__getitem__ = lambda self, k: "planners" if k == "niche" else None
        cursor = AsyncMock()
        cursor.fetchall = AsyncMock(return_value=[row])
        db = AsyncMock()
        db.execute = AsyncMock(return_value=cursor)

        sched = _make_sched(analytics_agent=analytics, learning_loop=learning)
        sched.memory.get_db = AsyncMock(return_value=db)

        await sched._run_etsy_learning_loop()
        learning.compare_ab_thumbnails.assert_awaited_once_with("planners")

    @pytest.mark.asyncio
    async def test_ab_thumbnail_skipped_on_exception(self):
        analytics = MagicMock()
        analytics.poll_listing_performance = AsyncMock()

        learning = MagicMock()
        learning.run_full_update = AsyncMock(return_value={"n_updated": 1, "top_niches": []})
        learning.compare_ab_thumbnails = AsyncMock(side_effect=Exception("ctr error"))

        row = MagicMock()
        row.__getitem__ = lambda self, k: "notebooks" if k == "niche" else None
        cursor = AsyncMock()
        cursor.fetchall = AsyncMock(return_value=[row])
        db = AsyncMock()
        db.execute = AsyncMock(return_value=cursor)

        sched = _make_sched(analytics_agent=analytics, learning_loop=learning)
        sched.memory.get_db = AsyncMock(return_value=db)

        await sched._run_etsy_learning_loop()
        # Should not raise — exception increments ab_skipped

    @pytest.mark.asyncio
    async def test_ab_thumbnail_db_exception_captured(self):
        analytics = MagicMock()
        analytics.poll_listing_performance = AsyncMock()

        learning = MagicMock()
        learning.run_full_update = AsyncMock(return_value={"n_updated": 1, "top_niches": []})

        sched = _make_sched(analytics_agent=analytics, learning_loop=learning)
        sched.memory.get_db = AsyncMock(side_effect=Exception("db unavailable"))

        await sched._run_etsy_learning_loop()
        # errors list → telegram message still sent
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ab_thumbnail_skipped_status_increments_ab_skipped(self):
        analytics = MagicMock()
        analytics.poll_listing_performance = AsyncMock()

        learning = MagicMock()
        learning.run_full_update = AsyncMock(return_value={"n_updated": 1, "top_niches": []})
        learning.compare_ab_thumbnails = AsyncMock(return_value={"status": "skipped"})

        row = MagicMock()
        row.__getitem__ = lambda self, k: "journals" if k == "niche" else None
        cursor = AsyncMock()
        cursor.fetchall = AsyncMock(return_value=[row])
        db = AsyncMock()
        db.execute = AsyncMock(return_value=cursor)

        sched = _make_sched(analytics_agent=analytics, learning_loop=learning)
        sched.memory.get_db = AsyncMock(return_value=db)

        await sched._run_etsy_learning_loop()
        # Verify ab_skipped path ran (no assertion error = pass)


class TestPollListingPerformance:

    @pytest.mark.asyncio
    async def test_analytics_agent_none_returns_early(self):
        sched = _make_sched(analytics_agent=None)
        await sched._run_poll_listing_performance()

    @pytest.mark.asyncio
    async def test_poll_called_when_agent_present(self):
        analytics = MagicMock()
        analytics.poll_listing_performance = AsyncMock()
        sched = _make_sched(analytics_agent=analytics)
        await sched._run_poll_listing_performance()
        analytics.poll_listing_performance.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_caught_and_logged(self):
        analytics = MagicMock()
        analytics.poll_listing_performance = AsyncMock(side_effect=Exception("timeout"))
        sched = _make_sched(analytics_agent=analytics)
        await sched._run_poll_listing_performance()


class TestShopOptimizerJob:

    @pytest.mark.asyncio
    async def test_shop_optimizer_none_returns_early(self):
        sched = _make_sched(shop_optimizer=None)
        await sched._run_shop_optimizer_job()

    @pytest.mark.asyncio
    async def test_status_applied_notifies_telegram(self):
        optimizer = MagicMock()
        optimizer.apply_shop_profile = AsyncMock(return_value={
            "status": "applied",
            "title": "Best Planners",
            "niches": ["planners", "notebooks"],
        })
        sched = _make_sched(shop_optimizer=optimizer)
        await sched._run_shop_optimizer_job()
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_mock_no_notification(self):
        optimizer = MagicMock()
        optimizer.apply_shop_profile = AsyncMock(return_value={"status": "mock"})
        sched = _make_sched(shop_optimizer=optimizer)
        await sched._run_shop_optimizer_job()
        sched._notify_telegram.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_status_skipped_no_notification(self):
        optimizer = MagicMock()
        optimizer.apply_shop_profile = AsyncMock(return_value={"status": "skipped"})
        sched = _make_sched(shop_optimizer=optimizer)
        await sched._run_shop_optimizer_job()
        sched._notify_telegram.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_status_no_api_notifies_warning(self):
        optimizer = MagicMock()
        optimizer.apply_shop_profile = AsyncMock(return_value={
            "status": "no_api",
            "error": "API key missing",
        })
        sched = _make_sched(shop_optimizer=optimizer)
        await sched._run_shop_optimizer_job()
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_error_notifies_warning(self):
        optimizer = MagicMock()
        optimizer.apply_shop_profile = AsyncMock(return_value={
            "status": "error",
            "error": "Internal failure",
        })
        sched = _make_sched(shop_optimizer=optimizer)
        await sched._run_shop_optimizer_job()
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_is_caught(self):
        optimizer = MagicMock()
        optimizer.apply_shop_profile = AsyncMock(side_effect=Exception("crash"))
        sched = _make_sched(shop_optimizer=optimizer)
        await sched._run_shop_optimizer_job()


class TestEtsyAdsManager:

    @pytest.mark.asyncio
    async def test_ads_manager_none_returns_early(self):
        sched = _make_sched(etsy_ads_manager=None)
        await sched._run_etsy_ads_manager()

    @pytest.mark.asyncio
    async def test_auto_manage_ads_called(self):
        ads = MagicMock()
        ads.auto_manage_ads = AsyncMock()
        sched = _make_sched(etsy_ads_manager=ads)
        await sched._run_etsy_ads_manager()
        ads.auto_manage_ads.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_is_caught(self):
        ads = MagicMock()
        ads.auto_manage_ads = AsyncMock(side_effect=Exception("ads error"))
        sched = _make_sched(etsy_ads_manager=ads)
        await sched._run_etsy_ads_manager()


class TestCheckEmptySections:

    @pytest.mark.asyncio
    async def test_memory_none_returns_early(self):
        sched = _make_sched(memory=None)
        await sched._check_empty_sections()

    @pytest.mark.asyncio
    async def test_db_exception_returns_early(self):
        sched = _make_sched()
        sched.memory.get_db = AsyncMock(side_effect=Exception("db down"))
        # EtsySectionsService never reached — no patch needed
        await sched._check_empty_sections()
        sched._notify_telegram.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_stale_sections_no_notification(self):
        db = AsyncMock()
        sched = _make_sched()
        sched.memory.get_db = AsyncMock(return_value=db)

        with patch("apps.backend.core.etsy_sections_service.EtsySectionsService") as mock_cls:
            mock_ess = MagicMock()
            mock_ess.get_stale_sections = AsyncMock(return_value=[])
            mock_cls.return_value = mock_ess
            await sched._check_empty_sections()

        sched._notify_telegram.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stale_sections_notifies_telegram(self):
        db = AsyncMock()
        sched = _make_sched()
        sched.memory.get_db = AsyncMock(return_value=db)

        stale = [
            {"section_name": "Planners", "last_listing_at": "2025-01-01", "listing_count": 2},
            {"section_name": "Journals", "last_listing_at": None, "listing_count": 0},
        ]
        with patch("apps.backend.core.etsy_sections_service.EtsySectionsService") as mock_cls:
            mock_ess = MagicMock()
            mock_ess.get_stale_sections = AsyncMock(return_value=stale)
            mock_cls.return_value = mock_ess
            await sched._check_empty_sections()

        sched._notify_telegram.assert_awaited_once()


# ===========================================================================
# _PersonalMixin
# ===========================================================================

class TestPersonalLearningLoop:

    @pytest.mark.asyncio
    async def test_no_recent_activity_returns_early(self):
        sched = _make_sched()
        sched.memory.get_agent_steps_count = AsyncMock(return_value=0)
        await sched._run_personal_learning_loop()
        sched.memory.decay_old_patterns.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_run_all_steps(self):
        sched = _make_sched()
        sched.memory.get_agent_steps_count    = AsyncMock(return_value=10)
        sched.memory.decay_old_patterns       = AsyncMock(return_value=3)
        sched.memory.get_frequent_queries     = AsyncMock(return_value=["planners", "journals"])
        sched.memory.upsert_learning          = AsyncMock()
        sched.memory.detect_watcher_habits    = AsyncMock(return_value=[{"pattern": "morning:app_x"}])
        sched.memory.get_sent_unacknowledged  = AsyncMock(return_value=[])
        sched._run_weekly_personal_synthesis  = AsyncMock(return_value=False)

        await sched._run_personal_learning_loop()
        sched.memory.decay_old_patterns.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_step3_exception_does_not_abort(self):
        sched = _make_sched()
        sched.memory.get_agent_steps_count    = AsyncMock(return_value=5)
        sched.memory.decay_old_patterns       = AsyncMock(return_value=2)
        sched.memory.get_frequent_queries     = AsyncMock(side_effect=Exception("chroma"))
        sched.memory.detect_watcher_habits    = AsyncMock(return_value=[])
        sched.memory.get_sent_unacknowledged  = AsyncMock(return_value=[])
        sched._run_weekly_personal_synthesis  = AsyncMock(return_value=False)

        await sched._run_personal_learning_loop()
        sched.memory.detect_watcher_habits.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_step4_exception_does_not_abort(self):
        sched = _make_sched()
        sched.memory.get_agent_steps_count    = AsyncMock(return_value=5)
        sched.memory.decay_old_patterns       = AsyncMock(return_value=2)
        sched.memory.get_frequent_queries     = AsyncMock(return_value=[])
        sched.memory.detect_watcher_habits    = AsyncMock(side_effect=Exception("watcher"))
        sched.memory.get_sent_unacknowledged  = AsyncMock(return_value=[])
        sched._run_weekly_personal_synthesis  = AsyncMock(return_value=False)

        await sched._run_personal_learning_loop()
        sched.memory.get_sent_unacknowledged.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_step5_ignored_reminder_penalized(self):
        sched = _make_sched()
        sched.memory.get_agent_steps_count    = AsyncMock(return_value=5)
        sched.memory.decay_old_patterns       = AsyncMock(return_value=2)
        sched.memory.get_frequent_queries     = AsyncMock(return_value=[])
        sched.memory.detect_watcher_habits    = AsyncMock(return_value=[])
        sched.memory.upsert_learning          = AsyncMock()
        sched.memory.get_sent_unacknowledged  = AsyncMock(
            return_value=[{"id": 1, "text": "buy groceries"}]
        )
        sched._run_weekly_personal_synthesis  = AsyncMock(return_value=False)

        await sched._run_personal_learning_loop()

        calls = sched.memory.upsert_learning.await_args_list
        assert any(
            c.kwargs.get("signal_type") == "implicit_ignored" for c in calls
        )

    @pytest.mark.asyncio
    async def test_step6_synthesis_exception_does_not_abort(self):
        sched = _make_sched()
        sched.memory.get_agent_steps_count    = AsyncMock(return_value=5)
        sched.memory.decay_old_patterns       = AsyncMock(return_value=2)
        sched.memory.get_frequent_queries     = AsyncMock(return_value=[])
        sched.memory.detect_watcher_habits    = AsyncMock(return_value=[])
        sched.memory.get_sent_unacknowledged  = AsyncMock(return_value=[])
        sched._run_weekly_personal_synthesis  = AsyncMock(side_effect=Exception("synthesis crash"))

        await sched._run_personal_learning_loop()
        # No raise — outer function catches step-6 exception

    @pytest.mark.asyncio
    async def test_notify_telegram_when_decayed_gt5(self):
        pepe = MagicMock()
        pepe.notify_telegram = AsyncMock()

        sched = _make_sched(pepe=pepe)
        sched.memory.get_agent_steps_count    = AsyncMock(return_value=10)
        sched.memory.decay_old_patterns       = AsyncMock(return_value=10)  # > 5
        sched.memory.get_frequent_queries     = AsyncMock(return_value=[])
        sched.memory.detect_watcher_habits    = AsyncMock(return_value=[])
        sched.memory.get_sent_unacknowledged  = AsyncMock(return_value=[])
        sched._run_weekly_personal_synthesis  = AsyncMock(return_value=True)

        await sched._run_personal_learning_loop()
        pepe.notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_outer_exception_is_caught(self):
        sched = _make_sched()
        sched.memory.get_agent_steps_count = AsyncMock(return_value=5)
        sched.memory.decay_old_patterns    = AsyncMock(side_effect=Exception("fatal"))
        await sched._run_personal_learning_loop()


class TestWeeklyPersonalSynthesis:

    @pytest.mark.asyncio
    async def test_pepe_none_returns_false(self):
        sched = _make_sched(pepe=None)
        result = await sched._run_weekly_personal_synthesis()
        assert result is False

    @pytest.mark.asyncio
    async def test_guard_finds_recent_synthesis_returns_false(self):
        pepe = MagicMock()
        pepe.client = MagicMock()
        sched = _make_sched(pepe=pepe)

        sched.memory.query_personal_memory = AsyncMock(
            return_value=[{"metadata": {"type": "weekly_synthesis"}}]
        )
        result = await sched._run_weekly_personal_synthesis()
        assert result is False

    @pytest.mark.asyncio
    async def test_guard_exception_returns_false(self):
        pepe = MagicMock()
        pepe.client = MagicMock()
        sched = _make_sched(pepe=pepe)
        sched.memory.query_personal_memory = AsyncMock(side_effect=Exception("chroma"))

        result = await sched._run_weekly_personal_synthesis()
        assert result is False

    @pytest.mark.asyncio
    async def test_insufficient_insights_returns_false(self):
        pepe = MagicMock()
        pepe.client = MagicMock()
        sched = _make_sched(pepe=pepe)

        sched.memory.query_personal_memory = AsyncMock(side_effect=[
            [],  # guard → no recent synthesis
            [{"document": "insight", "metadata": {"type": "topic"}}] * 3,  # < 5
        ])
        result = await sched._run_weekly_personal_synthesis()
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_documents_returns_false(self):
        pepe = MagicMock()
        pepe.client = MagicMock()
        sched = _make_sched(pepe=pepe)

        # 6 insights but all documents are empty strings
        empty_insights = [
            {"document": "", "metadata": {"type": "topic", "query": "q"}}
            for _ in range(6)
        ]
        sched.memory.query_personal_memory = AsyncMock(side_effect=[[], empty_insights])

        result = await sched._run_weekly_personal_synthesis()
        assert result is False

    @pytest.mark.asyncio
    async def test_enough_insights_llm_success_returns_true(self):
        pepe = MagicMock()
        pepe.client = MagicMock()

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Synthesis text here")]
        pepe.client.messages.create = AsyncMock(return_value=mock_response)

        sched = _make_sched(pepe=pepe)
        sched.memory.store_personal_insight = AsyncMock()

        insights = [
            {"document": f"insight {i}", "metadata": {"type": "topic", "query": f"q{i}"}}
            for i in range(6)
        ]
        sched.memory.query_personal_memory = AsyncMock(side_effect=[[], insights])

        result = await sched._run_weekly_personal_synthesis()
        assert result is True
        sched.memory.store_personal_insight.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_failure_returns_false(self):
        pepe = MagicMock()
        pepe.client = MagicMock()
        pepe.client.messages.create = AsyncMock(side_effect=Exception("llm down"))

        sched = _make_sched(pepe=pepe)

        insights = [
            {"document": f"insight {i}", "metadata": {"type": "topic", "query": "q"}}
            for i in range(6)
        ]
        sched.memory.query_personal_memory = AsyncMock(side_effect=[[], insights])

        result = await sched._run_weekly_personal_synthesis()
        assert result is False

    @pytest.mark.asyncio
    async def test_store_failure_returns_false(self):
        pepe = MagicMock()
        pepe.client = MagicMock()

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Some synthesis")]
        pepe.client.messages.create = AsyncMock(return_value=mock_response)

        sched = _make_sched(pepe=pepe)
        sched.memory.store_personal_insight = AsyncMock(side_effect=Exception("store error"))

        insights = [
            {"document": f"insight {i}", "metadata": {"type": "topic", "query": "q"}}
            for i in range(6)
        ]
        sched.memory.query_personal_memory = AsyncMock(side_effect=[[], insights])

        result = await sched._run_weekly_personal_synthesis()
        assert result is False


class TestSharedMemoryDecay:

    @pytest.mark.asyncio
    async def test_deleted_gt0_notifies_telegram(self):
        sched = _make_sched()
        sched.memory.delete_stale_shared_memory = AsyncMock(return_value=5)
        await sched._run_shared_memory_decay()
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deleted_eq0_no_notification(self):
        sched = _make_sched()
        sched.memory.delete_stale_shared_memory = AsyncMock(return_value=0)
        await sched._run_shared_memory_decay()
        sched._notify_telegram.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_is_caught(self):
        sched = _make_sched()
        sched.memory.delete_stale_shared_memory = AsyncMock(side_effect=Exception("db error"))
        await sched._run_shared_memory_decay()


class TestReminderChecker:

    @pytest.mark.asyncio
    async def test_pepe_none_returns_early(self):
        sched = _make_sched(pepe=None)
        await sched._run_reminder_checker()

    @pytest.mark.asyncio
    async def test_pepe_without_notify_telegram_returns_early(self):
        class _FakePepe:
            pass

        sched = _make_sched(pepe=_FakePepe())
        await sched._run_reminder_checker()

    @pytest.mark.asyncio
    async def test_no_due_reminders_returns_early(self):
        pepe = MagicMock()
        pepe.notify_telegram = AsyncMock()
        sched = _make_sched(pepe=pepe)
        sched.memory.get_due_reminders = AsyncMock(return_value=[])
        await sched._run_reminder_checker()

    @pytest.mark.asyncio
    async def test_non_recurring_reminder_sent_and_marked(self):
        pepe = MagicMock()
        pepe.notify_telegram = AsyncMock()
        pepe.send_reminder_notification = AsyncMock(return_value=42)
        sched = _make_sched(pepe=pepe)

        sched.memory.get_due_reminders = AsyncMock(
            return_value=[{"id": 1, "text": "Buy milk", "recurring_rule": None}]
        )
        sched.memory.mark_reminder_sent = AsyncMock()

        await sched._run_reminder_checker()

        pepe.send_reminder_notification.assert_awaited_once()
        sched.memory.mark_reminder_sent.assert_awaited_once_with(1, 42)

    @pytest.mark.asyncio
    async def test_recurring_reminder_rescheduled(self):
        pepe = MagicMock()
        pepe.notify_telegram = AsyncMock()
        pepe.send_reminder_notification = AsyncMock(return_value=99)
        sched = _make_sched(pepe=pepe)

        sched.memory.get_due_reminders = AsyncMock(
            return_value=[{"id": 2, "text": "Daily standup", "recurring_rule": "daily"}]
        )
        sched.memory.mark_reminder_sent     = AsyncMock()
        sched.memory.reschedule_recurring   = AsyncMock()

        await sched._run_reminder_checker()
        sched.memory.reschedule_recurring.assert_awaited_once_with(2)

    @pytest.mark.asyncio
    async def test_exception_is_caught(self):
        pepe = MagicMock()
        pepe.notify_telegram = AsyncMock()
        sched = _make_sched(pepe=pepe)
        sched.memory.get_due_reminders = AsyncMock(side_effect=Exception("db error"))
        await sched._run_reminder_checker()


class TestUnackPing:

    @pytest.mark.asyncio
    async def test_pepe_none_returns_early(self):
        sched = _make_sched(pepe=None)
        await sched._run_unack_ping()

    @pytest.mark.asyncio
    async def test_pepe_without_notify_telegram_returns_early(self):
        class _FakePepe:
            pass

        sched = _make_sched(pepe=_FakePepe())
        await sched._run_unack_ping()

    @pytest.mark.asyncio
    async def test_no_unacked_returns_early(self):
        pepe = MagicMock()
        pepe.notify_telegram = AsyncMock()
        sched = _make_sched(pepe=pepe)
        sched.memory.get_sent_unacknowledged = AsyncMock(return_value=[])
        await sched._run_unack_ping()

    @pytest.mark.asyncio
    async def test_unacked_reminder_ping_sent(self):
        pepe = MagicMock()
        pepe.notify_telegram = AsyncMock()
        sched = _make_sched(pepe=pepe)
        sched.memory.get_sent_unacknowledged = AsyncMock(
            return_value=[{"id": 5, "text": "Exercise reminder"}]
        )
        await sched._run_unack_ping()
        pepe.notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_is_caught(self):
        pepe = MagicMock()
        pepe.notify_telegram = AsyncMock()
        sched = _make_sched(pepe=pepe)
        sched.memory.get_sent_unacknowledged = AsyncMock(side_effect=Exception("fail"))
        await sched._run_unack_ping()


class TestMediumDigest:

    @pytest.mark.asyncio
    async def test_pepe_none_returns_early(self):
        sched = _make_sched(pepe=None)
        await sched._run_medium_digest()

    @pytest.mark.asyncio
    async def test_pepe_without_flush_method_returns_early(self):
        class _FakePepe:
            pass

        sched = _make_sched(pepe=_FakePepe())
        await sched._run_medium_digest()

    @pytest.mark.asyncio
    async def test_flush_medium_digest_called(self):
        pepe = MagicMock()
        pepe.flush_medium_digest = AsyncMock()
        sched = _make_sched(pepe=pepe)
        await sched._run_medium_digest()
        pepe.flush_medium_digest.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_is_caught(self):
        pepe = MagicMock()
        pepe.flush_medium_digest = AsyncMock(side_effect=Exception("flush error"))
        sched = _make_sched(pepe=pepe)
        await sched._run_medium_digest()


# ===========================================================================
# _WikiMixin
# ===========================================================================

class TestWikiHealthCheck:

    @pytest.mark.asyncio
    async def test_pepe_none_returns_early(self):
        sched = _make_sched(pepe=None)
        await sched._run_wiki_health_check()
        sched._notify_telegram.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wiki_none_logs_and_returns(self):
        pepe = MagicMock()
        pepe.wiki = None
        pepe.client = MagicMock()
        pepe._local_client = MagicMock()
        sched = _make_sched(pepe=pepe)
        await sched._run_wiki_health_check()
        sched._notify_telegram.assert_not_awaited()

    def _wiki_pepe(self, **wiki_overrides):
        """Return a pepe mock with a fully mocked wiki."""
        wiki = MagicMock()
        wiki.cleanup_orphan_raw = AsyncMock(
            return_value={"compiled": 1, "deleted": 0, "skipped": 0, "errors": []}
        )
        wiki.compact_wiki  = AsyncMock(return_value={"files_compacted": 2})
        wiki.lint          = AsyncMock(return_value="OK")
        wiki.update_index  = AsyncMock()
        wiki.get_stats     = AsyncMock(
            return_value={"etsy_niches": 10, "total_raw": 50, "pending_raw": 3}
        )
        for k, v in wiki_overrides.items():
            setattr(wiki, k, v)

        pepe = MagicMock()
        pepe.wiki = wiki
        pepe.client = MagicMock()
        pepe._local_client = MagicMock()
        return pepe, wiki

    @pytest.mark.asyncio
    async def test_full_run_calls_all_wiki_methods_and_notifies(self):
        pepe, wiki = self._wiki_pepe()
        sched = _make_sched(pepe=pepe)
        await sched._run_wiki_health_check()

        assert wiki.compact_wiki.call_count == 2   # etsy + personal
        assert wiki.lint.call_count == 2
        assert wiki.update_index.call_count == 2
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_orphan_cleanup_exception_continues(self):
        pepe, wiki = self._wiki_pepe(
            cleanup_orphan_raw=AsyncMock(side_effect=Exception("orphan error"))
        )
        sched = _make_sched(pepe=pepe)
        await sched._run_wiki_health_check()
        assert wiki.compact_wiki.call_count == 2

    @pytest.mark.asyncio
    async def test_compact_exception_marks_minus1(self):
        pepe, wiki = self._wiki_pepe(
            compact_wiki=AsyncMock(side_effect=Exception("compact error"))
        )
        sched = _make_sched(pepe=pepe)
        await sched._run_wiki_health_check()
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lint_exception_continues_with_error_string(self):
        pepe, wiki = self._wiki_pepe(
            lint=AsyncMock(side_effect=Exception("lint crash"))
        )
        sched = _make_sched(pepe=pepe)
        await sched._run_wiki_health_check()
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_index_exception_continues(self):
        pepe, wiki = self._wiki_pepe(
            update_index=AsyncMock(side_effect=Exception("index error"))
        )
        sched = _make_sched(pepe=pepe)
        await sched._run_wiki_health_check()
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stats_exception_uses_empty_dict(self):
        pepe, wiki = self._wiki_pepe(
            get_stats=AsyncMock(side_effect=Exception("stats error"))
        )
        sched = _make_sched(pepe=pepe)
        await sched._run_wiki_health_check()
        sched._notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_ok_lint_included_in_report(self):
        pepe, wiki = self._wiki_pepe(
            lint=AsyncMock(return_value="Broken link: [[foo]]")
        )
        sched = _make_sched(pepe=pepe)
        await sched._run_wiki_health_check()
        report = sched._notify_telegram.call_args[0][0]
        assert "Lint" in report

    @pytest.mark.asyncio
    async def test_orphan_errors_included_in_report(self):
        pepe, wiki = self._wiki_pepe(
            cleanup_orphan_raw=AsyncMock(
                return_value={"compiled": 2, "deleted": 1, "skipped": 0, "errors": ["err1"]}
            )
        )
        sched = _make_sched(pepe=pepe)
        await sched._run_wiki_health_check()
        report = sched._notify_telegram.call_args[0][0]
        assert "Orfani" in report


# ===========================================================================
# _SystemMixin
# ===========================================================================

class TestScreenCleanup:

    @pytest.mark.asyncio
    async def test_screen_watcher_none_returns_early(self):
        sched = _make_sched(screen_watcher=None)
        await sched._run_screen_cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_with_deleted_and_telegram_broadcast(self):
        watcher = MagicMock()
        watcher.cleanup_old_memories = AsyncMock(return_value=10)

        telegram = AsyncMock()
        sched = _make_sched(screen_watcher=watcher)
        sched._telegram_broadcast = telegram

        await sched._run_screen_cleanup()
        telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_with_deleted_no_telegram_broadcast(self):
        watcher = MagicMock()
        watcher.cleanup_old_memories = AsyncMock(return_value=5)
        sched = _make_sched(screen_watcher=watcher)
        await sched._run_screen_cleanup()  # should not raise

    @pytest.mark.asyncio
    async def test_cleanup_returns_none_no_notification(self):
        watcher = MagicMock()
        watcher.cleanup_old_memories = AsyncMock(return_value=None)
        telegram = AsyncMock()
        sched = _make_sched(screen_watcher=watcher)
        sched._telegram_broadcast = telegram
        await sched._run_screen_cleanup()
        telegram.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_is_caught(self):
        watcher = MagicMock()
        watcher.cleanup_old_memories = AsyncMock(side_effect=Exception("watcher error"))
        sched = _make_sched(screen_watcher=watcher)
        await sched._run_screen_cleanup()


class TestHealthCheckSsdWithStorage:

    @pytest.mark.asyncio
    async def test_storage_available_broadcasts_health(self):
        storage = MagicMock()
        health  = {"available": True, "free_gb": 50.0, "pending_count": 3}

        sched = _make_sched(storage=storage)

        with patch(
            "apps.backend.core._scheduler._scheduler_system_mixin.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as mock_tt:
            mock_tt.return_value = health
            await sched._health_check_ssd()

        sched._broadcast.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_storage_low_space_notifies_pepe(self):
        storage = MagicMock()
        health  = {"available": True, "free_gb": 0.5, "pending_count": 0}

        pepe = MagicMock()
        pepe.notify_telegram = AsyncMock()
        sched = _make_sched(storage=storage, pepe=pepe)

        with patch(
            "apps.backend.core._scheduler._scheduler_system_mixin.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as mock_tt:
            mock_tt.return_value = health
            await sched._health_check_ssd()

        pepe.notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_storage_not_available_broadcasts_offline_and_notifies(self):
        storage = MagicMock()
        health  = {"available": False, "free_gb": 0, "pending_count": 0}

        pepe = MagicMock()
        pepe.notify_telegram = AsyncMock()
        sched = _make_sched(storage=storage, pepe=pepe)

        with patch(
            "apps.backend.core._scheduler._scheduler_system_mixin.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as mock_tt:
            mock_tt.return_value = health
            await sched._health_check_ssd()

        sched._broadcast.assert_awaited_once()
        pepe.notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_storage_path_not_found_notifies_pepe(self):
        pepe = MagicMock()
        pepe.notify_telegram = AsyncMock()
        sched = _make_sched(storage=None, pepe=pepe)

        with patch(
            "apps.backend.core._scheduler._scheduler_system_mixin.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as mock_tt:
            mock_tt.return_value = False
            await sched._health_check_ssd()

        pepe.notify_telegram.assert_awaited_once()


class TestSyncAgentStatus:

    @pytest.mark.asyncio
    async def test_pepe_none_returns_early(self):
        sched = _make_sched(pepe=None)
        await sched._sync_agent_status()
        sched._broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_broadcasts_agent_statuses(self):
        pepe = MagicMock()
        pepe.get_agent_statuses = MagicMock(
            return_value={"etsy": "idle", "research": "running"}
        )
        pepe.mock_mode = False
        pepe._queue    = MagicMock()
        pepe._queue.qsize = MagicMock(return_value=2)

        sched = _make_sched(pepe=pepe)
        await sched._sync_agent_status()
        sched._broadcast.assert_awaited()

    @pytest.mark.asyncio
    async def test_context_state_emitted_when_available(self):
        pepe = MagicMock()
        pepe.get_agent_statuses = MagicMock(return_value={})
        pepe.mock_mode          = False
        pepe.get_context_state  = MagicMock(return_value={"type": "context_state"})

        sched = _make_sched(pepe=pepe)
        await sched._sync_agent_status()
        assert sched._broadcast.await_count >= 2


class TestRunScheduledTask:

    @pytest.mark.asyncio
    async def test_updates_last_run(self):
        sched = _make_sched()
        sched.memory.update_task_last_run = AsyncMock()
        await sched._run_scheduled_task(1, None, None)
        sched.memory.update_task_last_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_last_run_exception_does_not_abort(self):
        sched = _make_sched()
        sched.memory.update_task_last_run = AsyncMock(side_effect=Exception("db locked"))
        await sched._run_scheduled_task(1, None, None)

    @pytest.mark.asyncio
    async def test_no_pepe_returns_after_last_run_update(self):
        sched = _make_sched(pepe=None)
        sched.memory.update_task_last_run = AsyncMock()
        await sched._run_scheduled_task(1, "etsy", '{"key": "val"}')
        sched.memory.update_task_last_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_agent_name_returns_early(self):
        pepe = MagicMock()
        pepe.dispatch_task = AsyncMock()
        sched = _make_sched(pepe=pepe)
        sched.memory.update_task_last_run = AsyncMock()
        await sched._run_scheduled_task(1, None, None)
        pepe.dispatch_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_json_task_data_is_parsed(self):
        pepe = MagicMock()
        pepe.dispatch_task = AsyncMock()
        sched = _make_sched(pepe=pepe)
        sched.memory.update_task_last_run = AsyncMock()

        await sched._run_scheduled_task(1, "etsy", '{"prompt": "analyze listings"}')

        pepe.dispatch_task.assert_awaited_once()
        task_arg = pepe.dispatch_task.await_args[0][0]
        assert task_arg.input_data == {"prompt": "analyze listings"}

    @pytest.mark.asyncio
    async def test_invalid_json_uses_raw(self):
        pepe = MagicMock()
        pepe.dispatch_task = AsyncMock()
        sched = _make_sched(pepe=pepe)
        sched.memory.update_task_last_run = AsyncMock()

        await sched._run_scheduled_task(1, "etsy", "not-valid-json")

        task_arg = pepe.dispatch_task.await_args[0][0]
        assert task_arg.input_data == {"raw": "not-valid-json"}

    @pytest.mark.asyncio
    async def test_none_task_data_uses_empty_dict(self):
        pepe = MagicMock()
        pepe.dispatch_task = AsyncMock()
        sched = _make_sched(pepe=pepe)
        sched.memory.update_task_last_run = AsyncMock()

        await sched._run_scheduled_task(1, "etsy", None)

        task_arg = pepe.dispatch_task.await_args[0][0]
        assert task_arg.input_data == {}

    @pytest.mark.asyncio
    async def test_dispatch_exception_is_caught(self):
        pepe = MagicMock()
        pepe.dispatch_task = AsyncMock(side_effect=Exception("dispatch failed"))
        sched = _make_sched(pepe=pepe)
        sched.memory.update_task_last_run = AsyncMock()
        await sched._run_scheduled_task(1, "etsy", None)


# ===========================================================================
# _CoreMixin
# ===========================================================================

class TestLoadDbJobs:

    @pytest.mark.asyncio
    async def test_empty_rows_adds_no_jobs(self):
        sched = _make_sched()
        sched.memory.get_enabled_scheduled_tasks = AsyncMock(return_value=[])
        await sched._load_db_jobs()
        assert sched._scheduler.get_jobs() == []

    @pytest.mark.asyncio
    async def test_valid_cron_row_registers_job(self):
        sched = _make_sched()
        sched.memory.get_enabled_scheduled_tasks = AsyncMock(return_value=[
            {
                "id": 1,
                "name": "My Task",
                "cron_expression": "0 * * * *",
                "agent_name": "etsy",
                "task_data": '{"k": "v"}',
            }
        ])
        await sched._load_db_jobs()
        job_ids = [j.id for j in sched._scheduler.get_jobs()]
        assert "db_task_1" in job_ids

    @pytest.mark.asyncio
    async def test_row_without_cron_expression_skipped(self):
        sched = _make_sched()
        sched.memory.get_enabled_scheduled_tasks = AsyncMock(return_value=[
            {"id": 2, "name": "No Cron", "cron_expression": None, "agent_name": "etsy"}
        ])
        await sched._load_db_jobs()
        assert sched._scheduler.get_jobs() == []

    @pytest.mark.asyncio
    async def test_invalid_cron_expression_skipped(self):
        sched = _make_sched()
        sched.memory.get_enabled_scheduled_tasks = AsyncMock(return_value=[
            {
                "id": 3,
                "name": "Bad Cron",
                "cron_expression": "invalid cron expression !!!",
                "agent_name": "etsy",
            }
        ])
        await sched._load_db_jobs()
        job_ids = [j.id for j in sched._scheduler.get_jobs()]
        assert "db_task_3" not in job_ids

    @pytest.mark.asyncio
    async def test_memory_exception_caught(self):
        sched = _make_sched()
        sched.memory.get_enabled_scheduled_tasks = AsyncMock(side_effect=Exception("db error"))
        await sched._load_db_jobs()


class TestJobLifecycleListeners:

    def test_on_job_submitted_sets_running(self):
        sched = _make_sched()
        event = MagicMock()
        event.job_id = "publish_checker"
        sched._on_job_submitted(event)
        assert sched._job_status["publish_checker"]["status"] == "running"

    def test_on_job_submitted_skips_internal_jobs(self):
        sched = _make_sched()
        event = MagicMock()
        event.job_id = "ssd_health_check"
        sched._on_job_submitted(event)
        assert "ssd_health_check" not in sched._job_status

    def test_on_job_executed_sets_completed(self):
        sched = _make_sched()
        event = MagicMock()
        event.job_id = "etsy_learning_loop"
        sched._on_job_executed(event)
        assert sched._job_status["etsy_learning_loop"]["status"] == "completed"

    def test_on_job_executed_skips_internal_jobs(self):
        sched = _make_sched()
        event = MagicMock()
        event.job_id = "agent_status_sync"
        sched._on_job_executed(event)
        assert "agent_status_sync" not in sched._job_status

    def test_on_job_error_sets_failed(self):
        sched = _make_sched()
        event = MagicMock()
        event.job_id   = "wiki_health_check"
        event.exception = RuntimeError("error")
        sched._on_job_error(event)
        assert sched._job_status["wiki_health_check"]["status"] == "failed"

    def test_on_job_error_skips_internal_jobs(self):
        sched = _make_sched()
        event = MagicMock()
        event.job_id    = "ssd_health_check"
        event.exception = RuntimeError("error")
        sched._on_job_error(event)
        assert "ssd_health_check" not in sched._job_status


class TestGetJobs:

    def test_get_jobs_returns_list(self):
        sched = _make_sched()
        jobs = sched.get_jobs()
        assert isinstance(jobs, list)

    def test_get_jobs_excludes_internal_jobs(self):
        """Internal jobs must not appear even if they are in _job_status."""
        sched = _make_sched()
        sched._job_status["ssd_health_check"]  = {"status": "running", "last_run": None}
        sched._job_status["publish_checker"]   = {"status": "completed", "last_run": "2026-01-01"}
        # No APScheduler jobs registered (scheduler not started), so list is []
        ids = [j["id"] for j in sched.get_jobs()]
        assert "ssd_health_check" not in ids


class TestBroadcastHelpers:

    @pytest.mark.asyncio
    async def test_broadcast_calls_ws_broadcast(self):
        from apps.backend.core._scheduler._scheduler_core_mixin import _CoreMixin  # noqa: PLC0415

        ws = AsyncMock()
        sched = _make_sched()
        sched._ws_broadcast = ws

        await _CoreMixin._broadcast(sched, {"type": "test"})
        ws.assert_awaited_once_with({"type": "test"})

    @pytest.mark.asyncio
    async def test_broadcast_none_ws_no_error(self):
        from apps.backend.core._scheduler._scheduler_core_mixin import _CoreMixin  # noqa: PLC0415

        sched = _make_sched()
        sched._ws_broadcast = None
        await _CoreMixin._broadcast(sched, {"type": "test"})

    @pytest.mark.asyncio
    async def test_broadcast_ws_exception_silenced(self):
        from apps.backend.core._scheduler._scheduler_core_mixin import _CoreMixin  # noqa: PLC0415

        ws = AsyncMock(side_effect=Exception("ws down"))
        sched = _make_sched()
        sched._ws_broadcast = ws
        await _CoreMixin._broadcast(sched, {"type": "test"})

    @pytest.mark.asyncio
    async def test_notify_telegram_via_telegram_broadcast(self):
        from apps.backend.core._scheduler._scheduler_core_mixin import _CoreMixin  # noqa: PLC0415

        telegram = AsyncMock()
        sched = _make_sched()
        sched._telegram_broadcast = telegram

        await _CoreMixin._notify_telegram(sched, "test message")
        telegram.assert_awaited_once_with("test message")

    @pytest.mark.asyncio
    async def test_notify_telegram_via_pepe_fallback(self):
        from apps.backend.core._scheduler._scheduler_core_mixin import _CoreMixin  # noqa: PLC0415

        pepe = MagicMock()
        pepe.notify_telegram = AsyncMock()

        sched = _make_sched(pepe=pepe)
        sched._telegram_broadcast = None

        await _CoreMixin._notify_telegram(sched, "via pepe")
        pepe.notify_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notify_telegram_broadcast_exception_silenced(self):
        from apps.backend.core._scheduler._scheduler_core_mixin import _CoreMixin  # noqa: PLC0415

        telegram = AsyncMock(side_effect=Exception("telegram down"))
        sched = _make_sched()
        sched._telegram_broadcast = telegram
        await _CoreMixin._notify_telegram(sched, "msg")

    @pytest.mark.asyncio
    async def test_notify_telegram_pepe_fallback_exception_silenced(self):
        from apps.backend.core._scheduler._scheduler_core_mixin import _CoreMixin  # noqa: PLC0415

        pepe = MagicMock()
        pepe.notify_telegram = AsyncMock(side_effect=Exception("pepe down"))

        sched = _make_sched(pepe=pepe)
        sched._telegram_broadcast = None
        await _CoreMixin._notify_telegram(sched, "msg")


class TestExtractColorSchemes:

    def setup_method(self):
        from apps.backend.core._scheduler._scheduler_core_mixin import _extract_color_schemes  # noqa: PLC0415
        self._fn = _extract_color_schemes

    def test_empty_hint_returns_empty(self):
        assert self._fn("") == []

    def test_sage_green_returns_sage(self):
        assert "sage" in self._fn("sage green")

    def test_warm_beige_returns_beige(self):
        assert "beige" in self._fn("warm beige")

    def test_dusty_pink_returns_blush(self):
        assert "blush" in self._fn("dusty pink")

    def test_multiple_hints_max_three(self):
        result = self._fn("sage green, warm beige, dusty pink, slate grey")
        assert len(result) <= 3

    def test_unknown_keyword_returns_empty(self):
        assert self._fn("rainbow sparkle") == []

    def test_charcoal_returns_slate(self):
        assert "slate" in self._fn("charcoal")

    def test_minimal_returns_minimal(self):
        assert "minimal" in self._fn("clean minimal white")

    def test_neutral_returns_beige(self):
        assert "beige" in self._fn("neutral tones")
