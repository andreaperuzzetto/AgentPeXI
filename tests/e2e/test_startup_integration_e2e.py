"""tests/e2e/test_startup_integration_e2e.py — Integration tests for startup modules.

ST1 — build_autopilot_callables (sync) + init_autopilot_loop (async) build
      a valid AutopilotLoop with all injected components.
ST2 — init_all_agents registers 8 agents and returns a complete AgentBundle;
      init_autonomy_services works against a real :memory: SQLite.
ST3 — init_memory + init_storage + init_tools + init_screen_watcher (happy
      path and fail-safe path) complete without exceptions.
ST4 — init_scheduler builds a Scheduler without starting it;
      init_telegram_bot constructs BotDependencies + TelegramBot and calls start().
ST5 — Calling init_autopilot_loop twice produces two independent AutopilotLoop
      objects with no shared mutable state.

All external IO (LLM, Telegram, Notion, Etsy, screen capture) is mocked.
init_autonomy_services alone uses a real MemoryManager (tmp_path SQLite).
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.e2e.conftest import _make_memory_manager


# ─────────────────────────────────────────────────────────────────────────────
# ST1 — _autopilot_builder.py
# ─────────────────────────────────────────────────────────────────────────────

async def test_st1_build_autopilot_callables_and_loop() -> None:
    """build_autopilot_callables returns 3 callables; init_autopilot_loop
    constructs a valid AutopilotLoop with every injected component set."""
    from apps.backend.core._startup._autopilot_builder import (
        build_autopilot_callables,
        init_autopilot_loop,
    )
    from apps.backend.core.autopilot_loop import AutopilotLoop

    memory = AsyncMock()
    pepe = AsyncMock()
    production_queue = AsyncMock()
    bundle_strategy = AsyncMock()
    learning_loop = AsyncMock()

    # build_autopilot_callables is synchronous — no wait_for needed
    design_pipeline, niche_picker, bundle_checker = build_autopilot_callables(
        memory=memory,
        pepe=pepe,
        production_queue=production_queue,
        bundle_strategy=bundle_strategy,
        learning_loop=learning_loop,
    )

    assert callable(design_pipeline)
    assert callable(niche_picker)
    assert callable(bundle_checker)

    db = AsyncMock()
    budget_manager = AsyncMock()
    publication_policy = AsyncMock()
    bot_send = AsyncMock()
    bot_send_markup = AsyncMock()

    loop = await asyncio.wait_for(
        init_autopilot_loop(
            db=db,
            production_queue=production_queue,
            budget_manager=budget_manager,
            publication_policy=publication_policy,
            bot_send=bot_send,
            bot_send_markup=bot_send_markup,
            design_pipeline=design_pipeline,
            niche_picker=niche_picker,
            bundle_checker=bundle_checker,
        ),
        timeout=5.0,
    )

    assert isinstance(loop, AutopilotLoop)
    assert loop._db is db
    assert loop.queue is production_queue
    assert loop.budget is budget_manager
    assert loop.policy is publication_policy
    assert loop._design_pipeline is design_pipeline
    assert loop._niche_picker is niche_picker
    assert loop._bundle_checker is bundle_checker
    assert loop._running is False
    assert loop._approval_events == {}
    assert loop._approval_results == {}


# ─────────────────────────────────────────────────────────────────────────────
# ST2 — _agents.py
# ─────────────────────────────────────────────────────────────────────────────

async def test_st2_agents_init_all_and_autonomy(tmp_path) -> None:
    """init_autonomy_services creates the three autonomy objects from a real DB.
    init_all_agents registers exactly 8 agents with pepe and returns a
    complete 13-field AgentBundle."""
    from apps.backend.core._startup._agents import init_all_agents, init_autonomy_services
    from apps.backend.core._startup._models import AgentBundle, _AutonomyBundle

    # ── init_autonomy_services: real SQLite ───────────────────────────────────
    mm = _make_memory_manager(tmp_path)
    await mm.init()

    autonomy = await asyncio.wait_for(init_autonomy_services(mm), timeout=5.0)

    assert isinstance(autonomy, _AutonomyBundle)
    assert autonomy.db is not None
    assert autonomy.production_queue is not None
    assert autonomy.budget_manager is not None
    assert autonomy.publication_policy is not None

    # ── init_all_agents: all agent/service classes mocked ────────────────────
    pepe = MagicMock()
    pepe.client = MagicMock()
    pepe.mock_mode = False
    pepe.register_agent = MagicMock()

    _PATCHES = [
        patch("apps.backend.agents.pinterest.PinterestAgent"),
        patch("apps.backend.agents.publisher.PublisherAgent"),
        patch("apps.backend.agents.analytics.AnalyticsAgent"),
        patch("apps.backend.agents.finance.FinanceAgent"),
        patch("apps.backend.agents.recall.RecallAgent"),
        patch("apps.backend.agents.remind.RemindAgent"),
        patch("apps.backend.agents.summarize.SummarizeAgent"),
        patch("apps.backend.agents.research_personal.ResearchPersonalAgent"),
        patch("apps.backend.core.learning_loop.LearningLoop"),
        patch("apps.backend.core.bundle_strategy.BundleStrategy"),
        patch("apps.backend.core.etsy_ads.EtsyAdsManager"),
        patch("apps.backend.core.finance_tracker.FinanceTracker"),
        patch("apps.backend.core.shop_optimizer.ShopProfileOptimizer"),
    ]

    with ExitStack() as stack:
        for p in _PATCHES:
            stack.enter_context(p)

        bundle = await asyncio.wait_for(
            init_all_agents(
                pepe=pepe,
                memory=mm,
                storage=MagicMock(),
                etsy_api=MagicMock(),
                ws_broadcast=AsyncMock(),
                telegram_broadcast=AsyncMock(),
                notion_calendar=MagicMock(),
                web_search=MagicMock(),
                text_extractor=MagicMock(),
                production_queue=autonomy.production_queue,
                publication_policy=autonomy.publication_policy,
            ),
            timeout=5.0,
        )

    assert isinstance(bundle, AgentBundle)

    # Exactly 8 agents registered: pinterest, publisher, analytics, finance,
    # recall, remind, summarize, research_personal
    assert pepe.register_agent.call_count == 8
    registered_names = {c.args[0] for c in pepe.register_agent.call_args_list}
    assert registered_names == {
        "pinterest", "publisher", "analytics", "finance",
        "recall", "remind", "summarize", "research_personal",
    }

    # All 13 AgentBundle fields are populated
    for field in (
        "publisher_agent", "analytics_agent", "finance_agent", "recall_agent",
        "remind_agent", "summarize_agent", "research_personal_agent",
        "learning_loop", "bundle_strategy", "shop_optimizer",
        "etsy_ads_manager", "finance_tracker", "pinterest_agent",
    ):
        assert getattr(bundle, field) is not None, f"bundle.{field} is None"


# ─────────────────────────────────────────────────────────────────────────────
# ST3 — _infra.py
# ─────────────────────────────────────────────────────────────────────────────

async def test_st3_infra_all_functions() -> None:
    """All four _infra init functions complete without exceptions and return
    the expected objects; fail-safe path of init_screen_watcher returns
    (None, error_string) instead of raising."""
    from apps.backend.core._startup._infra import (
        init_memory,
        init_storage,
        init_tools,
        init_screen_watcher,
    )

    ws_broadcast = AsyncMock()
    settings_mock = MagicMock()
    settings_mock.NOTION_API_TOKEN = "test_token"
    settings_mock.SUMMARIZE_MAX_CHARS = 4096

    # ── init_memory ───────────────────────────────────────────────────────────
    mock_memory_instance = MagicMock()
    mock_memory_instance.init = AsyncMock()
    mock_memory_instance.set_ws_broadcaster = MagicMock()
    mock_memory_instance.set_bridge_callback = MagicMock()
    mock_memory_cls = MagicMock(return_value=mock_memory_instance)

    mock_bridge_instance = MagicMock()
    mock_bridge_instance.set_ws_broadcaster = MagicMock()
    mock_bridge_cls = MagicMock(return_value=mock_bridge_instance)

    with (
        patch("apps.backend.core.memory.MemoryManager", mock_memory_cls),
        patch("apps.backend.core.knowledge_bridge.KnowledgeBridge", mock_bridge_cls),
    ):
        memory = await asyncio.wait_for(
            init_memory(settings_mock, ws_broadcast), timeout=5.0
        )

    assert memory is mock_memory_instance
    mock_memory_instance.init.assert_called_once()
    mock_memory_instance.set_ws_broadcaster.assert_called_once_with(ws_broadcast)
    mock_bridge_instance.set_ws_broadcaster.assert_called_once_with(ws_broadcast)

    # ── init_storage ──────────────────────────────────────────────────────────
    mock_storage_instance = MagicMock()
    mock_storage_cls = MagicMock(return_value=mock_storage_instance)

    with patch("apps.backend.core.storage.StorageManager", mock_storage_cls):
        storage = await asyncio.wait_for(init_storage(), timeout=5.0)

    assert storage is mock_storage_instance
    mock_storage_instance.ensure_dirs.assert_called_once()

    # ── init_tools ────────────────────────────────────────────────────────────
    mock_notion_instance = MagicMock()
    mock_notion_instance.ensure_database = AsyncMock()
    mock_notion_cls = MagicMock(return_value=mock_notion_instance)
    mock_web_cls = MagicMock()
    mock_text_cls = MagicMock()

    with (
        patch("apps.backend.tools.notion_calendar.NotionCalendar", mock_notion_cls),
        patch("apps.backend.tools.web_search.WebSearchTool", mock_web_cls),
        patch("apps.backend.tools.text_extract.TextExtractor", mock_text_cls),
    ):
        notion_cal, web_search, text_extractor = await asyncio.wait_for(
            init_tools(settings_mock), timeout=5.0
        )

    assert notion_cal is mock_notion_instance
    assert web_search is mock_web_cls.return_value
    assert text_extractor is mock_text_cls.return_value
    mock_notion_instance.ensure_database.assert_called_once()

    # ── init_screen_watcher — happy path ──────────────────────────────────────
    mock_watcher_instance = MagicMock()
    mock_watcher_instance.start = AsyncMock()
    mock_watcher_cls = MagicMock(return_value=mock_watcher_instance)

    with patch("apps.backend.screen.watcher.ScreenWatcher", mock_watcher_cls):
        watcher, err = await asyncio.wait_for(
            init_screen_watcher(mock_memory_instance, ws_broadcast), timeout=5.0
        )

    assert watcher is mock_watcher_instance
    assert err is None

    # ── init_screen_watcher — fail-safe path ──────────────────────────────────
    mock_watcher_fail = MagicMock()
    mock_watcher_fail.start = AsyncMock(side_effect=RuntimeError("screen unavailable"))
    mock_watcher_fail_cls = MagicMock(return_value=mock_watcher_fail)

    with patch("apps.backend.screen.watcher.ScreenWatcher", mock_watcher_fail_cls):
        watcher_none, err_str = await asyncio.wait_for(
            init_screen_watcher(mock_memory_instance, ws_broadcast), timeout=5.0
        )

    assert watcher_none is None
    assert "screen unavailable" in (err_str or "")


# ─────────────────────────────────────────────────────────────────────────────
# ST4 — _services.py
# ─────────────────────────────────────────────────────────────────────────────

async def test_st4_services_init_scheduler_and_telegram_bot() -> None:
    """init_scheduler builds a Scheduler without calling start().
    init_telegram_bot constructs BotDependencies + TelegramBot and awaits start()."""
    from apps.backend.core._startup._services import init_scheduler, init_telegram_bot

    # Shared mock dependencies (used by both scheduler and bot)
    deps = {k: MagicMock() for k in (
        "memory", "pepe", "storage", "research_agent", "design_agent",
        "publisher_agent", "analytics_agent", "finance_agent",
        "screen_watcher", "production_queue", "budget_manager",
        "publication_policy", "autopilot_loop", "etsy_api",
        "shop_optimizer", "etsy_ads_manager", "learning_loop",
        "bundle_strategy", "finance_tracker",
    )}
    ws_broadcast = AsyncMock()
    telegram_broadcast = AsyncMock()

    # ── init_scheduler ────────────────────────────────────────────────────────
    mock_scheduler_instance = MagicMock()
    mock_scheduler_cls = MagicMock(return_value=mock_scheduler_instance)

    with patch("apps.backend.core.scheduler.Scheduler", mock_scheduler_cls):
        scheduler = await asyncio.wait_for(
            init_scheduler(
                memory=deps["memory"],
                ws_broadcast=ws_broadcast,
                pepe=deps["pepe"],
                storage=deps["storage"],
                research_agent=deps["research_agent"],
                design_agent=deps["design_agent"],
                publisher_agent=deps["publisher_agent"],
                analytics_agent=deps["analytics_agent"],
                finance_agent=deps["finance_agent"],
                telegram_broadcast=telegram_broadcast,
                screen_watcher=deps["screen_watcher"],
                production_queue=deps["production_queue"],
                budget_manager=deps["budget_manager"],
                publication_policy=deps["publication_policy"],
                autopilot_loop=deps["autopilot_loop"],
                etsy_api=deps["etsy_api"],
                shop_optimizer=deps["shop_optimizer"],
                etsy_ads_manager=deps["etsy_ads_manager"],
                learning_loop=deps["learning_loop"],
            ),
            timeout=5.0,
        )

    assert scheduler is mock_scheduler_instance
    mock_scheduler_cls.assert_called_once()

    # ── init_telegram_bot ─────────────────────────────────────────────────────
    mock_bot_instance = MagicMock()
    mock_bot_instance.start = AsyncMock()
    mock_bot_cls = MagicMock(return_value=mock_bot_instance)
    mock_deps_cls = MagicMock()

    with (
        patch("apps.backend.telegram.bot.TelegramBot", mock_bot_cls),
        patch("apps.backend.telegram.dependencies.BotDependencies", mock_deps_cls),
    ):
        bot = await asyncio.wait_for(
            init_telegram_bot(
                pepe=deps["pepe"],
                scheduler=mock_scheduler_instance,
                screen_watcher=deps["screen_watcher"],
                autopilot_loop=deps["autopilot_loop"],
                production_queue=deps["production_queue"],
                budget_manager=deps["budget_manager"],
                publication_policy=deps["publication_policy"],
                etsy_api=deps["etsy_api"],
                research_agent=deps["research_agent"],
                analytics_agent=deps["analytics_agent"],
                learning_loop=deps["learning_loop"],
                bundle_strategy=deps["bundle_strategy"],
                shop_optimizer=deps["shop_optimizer"],
                etsy_ads_manager=deps["etsy_ads_manager"],
                finance_tracker=deps["finance_tracker"],
            ),
            timeout=5.0,
        )

    assert bot is mock_bot_instance
    mock_bot_instance.start.assert_called_once()
    mock_deps_cls.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# ST5 — Double-init: two AutopilotLoop instances are independent
# ─────────────────────────────────────────────────────────────────────────────

async def test_st5_double_init_autopilot_loop_independent() -> None:
    """Calling init_autopilot_loop twice never raises and produces two fully
    independent AutopilotLoop objects — separate DB references, separate
    approval dicts, both in the initial non-running state."""
    from apps.backend.core._startup._autopilot_builder import (
        build_autopilot_callables,
        init_autopilot_loop,
    )
    from apps.backend.core.autopilot_loop import AutopilotLoop

    memory = AsyncMock()
    pepe = AsyncMock()
    production_queue = AsyncMock()
    bundle_strategy = AsyncMock()
    learning_loop = AsyncMock()

    design_pipeline, niche_picker, bundle_checker = build_autopilot_callables(
        memory=memory,
        pepe=pepe,
        production_queue=production_queue,
        bundle_strategy=bundle_strategy,
        learning_loop=learning_loop,
    )

    db1, db2 = AsyncMock(), AsyncMock()
    shared_kwargs = dict(
        production_queue=production_queue,
        budget_manager=AsyncMock(),
        publication_policy=AsyncMock(),
        bot_send=AsyncMock(),
        bot_send_markup=AsyncMock(),
        design_pipeline=design_pipeline,
        niche_picker=niche_picker,
        bundle_checker=bundle_checker,
    )

    loop1 = await asyncio.wait_for(
        init_autopilot_loop(db=db1, **shared_kwargs), timeout=5.0
    )
    loop2 = await asyncio.wait_for(
        init_autopilot_loop(db=db2, **shared_kwargs), timeout=5.0
    )

    assert loop1 is not loop2
    assert isinstance(loop1, AutopilotLoop)
    assert isinstance(loop2, AutopilotLoop)

    # Each loop holds its own DB reference
    assert loop1._db is db1
    assert loop2._db is db2

    # Mutable approval state is not shared
    assert loop1._approval_events is not loop2._approval_events
    assert loop1._approval_results is not loop2._approval_results

    # Both start in the non-running state
    assert loop1._running is False
    assert loop2._running is False
