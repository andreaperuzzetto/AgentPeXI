"""Service init functions: scheduler, telegram bot."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("agentpexi.startup")


async def init_scheduler(
    memory,
    ws_broadcast: Callable,
    pepe,
    storage,
    research_agent,
    design_agent,
    publisher_agent,
    analytics_agent,
    finance_agent,
    telegram_broadcast: Callable,
    screen_watcher,
    production_queue,
    budget_manager,
    publication_policy,
    autopilot_loop,
    etsy_api,
    shop_optimizer,
    etsy_ads_manager,
    learning_loop,
) -> Any:
    """Init Scheduler (does NOT start it — caller calls await scheduler.start())."""
    from apps.backend.core.scheduler import Scheduler

    scheduler = Scheduler(
        memory=memory,
        ws_broadcaster=ws_broadcast,
        pepe=pepe,
        storage=storage,
        research_agent=research_agent,
        design_agent=design_agent,
        publisher_agent=publisher_agent,
        analytics_agent=analytics_agent,
        finance_agent=finance_agent,
        telegram_broadcaster=telegram_broadcast,
        screen_watcher=screen_watcher,
        production_queue=production_queue,
        budget_manager=budget_manager,
        publication_policy=publication_policy,
        autopilot_loop=autopilot_loop,
        etsy_client=etsy_api,
        shop_optimizer=shop_optimizer,
        etsy_ads_manager=etsy_ads_manager,
        learning_loop=learning_loop,
    )
    return scheduler


async def init_telegram_bot(
    pepe,
    scheduler,
    screen_watcher,
    autopilot_loop,
    production_queue,
    budget_manager,
    publication_policy,
    etsy_api,
    analytics_agent,
    learning_loop,
    bundle_strategy,
    shop_optimizer,
    etsy_ads_manager,
    finance_tracker,
) -> Any:
    """Init and start TelegramBot."""
    from apps.backend.telegram.bot import TelegramBot
    from apps.backend.telegram.dependencies import BotDependencies

    deps = BotDependencies(
        pepe=pepe,
        scheduler=scheduler,
        screen_watcher=screen_watcher,
        autopilot_loop=autopilot_loop,
        production_queue=production_queue,
        budget_manager=budget_manager,
        publication_policy=publication_policy,
        etsy_api=etsy_api,
        analytics_agent=analytics_agent,
        learning_loop=learning_loop,
        bundle_strategy=bundle_strategy,
        shop_optimizer=shop_optimizer,
        etsy_ads_manager=etsy_ads_manager,
        finance_tracker=finance_tracker,
    )
    bot = TelegramBot(deps)
    await bot.start()
    return bot
