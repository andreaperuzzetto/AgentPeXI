"""Agent init functions: pepe, wiki, etsy, autonomy services, all agents."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from apps.backend.core._startup._models import AgentBundle, _AutonomyBundle, _PepeBundle

logger = logging.getLogger("agentpexi.startup")


async def init_pepe(
    memory,
    storage,
    ws_broadcast: Callable,
    telegram_broadcast: Callable,
) -> _PepeBundle:
    """Init Pepe, register research + design agents, start Pepe."""
    from apps.backend.core.pepe import Pepe
    from apps.backend.agents.research import ResearchAgent
    from apps.backend.agents.design import DesignAgent

    pepe = Pepe(memory=memory, ws_broadcaster=ws_broadcast)

    research_agent = ResearchAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_broadcast,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("research", research_agent)

    design_agent = DesignAgent(
        anthropic_client=pepe.client,
        memory=memory,
        storage=storage,
        ws_broadcaster=ws_broadcast,
        get_mock_mode=pepe.get_mock_mode,
    )
    pepe.register_agent("design", design_agent)

    await pepe.start()
    logger.info("Pepe avviato")
    return _PepeBundle(pepe=pepe, research_agent=research_agent, design_agent=design_agent)


async def init_wiki(pepe, settings) -> None:
    """Init WikiManager and attach to pepe.wiki (fail-safe)."""
    from apps.backend.core.wiki import WikiManager

    wiki_base_raw = settings.WIKI_BASE_PATH
    # _agents.py lives in core/_startup/; project root is parents[4]
    wiki_base = (
        Path(wiki_base_raw)
        if Path(wiki_base_raw).is_absolute()
        else Path(__file__).resolve().parents[4] / wiki_base_raw
    )
    try:
        wiki_manager = WikiManager(wiki_base)
        await wiki_manager.init()
        pepe.wiki = wiki_manager
        logger.info("WikiManager inizializzato — base: %s", wiki_base)
    except Exception as exc:
        logger.warning("WikiManager non avviato (fail-safe): %s", exc)
        pepe.wiki = None


async def init_etsy(memory, pepe) -> Any:
    """Init EtsyAPI."""
    from apps.backend.tools.etsy_api import EtsyAPI

    etsy_api = EtsyAPI(memory=memory, pepe=pepe)
    logger.info("EtsyAPI inizializzato")
    return etsy_api


async def init_autonomy_services(memory) -> _AutonomyBundle:
    """Init ProductionQueueService, BudgetManager, PublicationPolicy."""
    from apps.backend.core.production_queue import ProductionQueueService
    from apps.backend.core.budget_manager import BudgetManager
    from apps.backend.core.publication_policy import PublicationPolicy

    db = await memory.get_db()
    production_queue = ProductionQueueService(db)
    budget_manager = BudgetManager(db)
    publication_policy = PublicationPolicy(db)
    await budget_manager.ensure_defaults()
    await publication_policy.ensure_defaults()
    logger.info(
        "Autonomy Layer (B2): ProductionQueueService, BudgetManager, PublicationPolicy inizializzati"
    )
    return _AutonomyBundle(
        db=db,
        production_queue=production_queue,
        budget_manager=budget_manager,
        publication_policy=publication_policy,
    )


async def init_all_agents(
    pepe,
    memory,
    storage,
    etsy_api,
    ws_broadcast: Callable,
    telegram_broadcast: Callable,
    notion_calendar,
    web_search,
    text_extractor,
    production_queue,
    publication_policy,
) -> AgentBundle:
    """Register publisher/analytics/finance/personal agents; init learning & growth layer."""
    from apps.backend.agents.publisher import PublisherAgent
    from apps.backend.agents.analytics import AnalyticsAgent
    from apps.backend.agents.finance import FinanceAgent
    from apps.backend.agents.recall import RecallAgent
    from apps.backend.agents.remind import RemindAgent
    from apps.backend.agents.summarize import SummarizeAgent
    from apps.backend.agents.research_personal import ResearchPersonalAgent
    from apps.backend.agents.pinterest import PinterestAgent
    from apps.backend.core.learning_loop import LearningLoop
    from apps.backend.core.bundle_strategy import BundleStrategy
    from apps.backend.core.etsy_ads import EtsyAdsManager
    from apps.backend.core.finance_tracker import FinanceTracker
    from apps.backend.core.shop_optimizer import ShopProfileOptimizer

    pinterest_agent = PinterestAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_broadcast,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("pinterest", pinterest_agent)
    logger.info("PinterestAgent istanziato (B-08)")

    publisher_agent = PublisherAgent(
        anthropic_client=pepe.client,
        memory=memory,
        storage=storage,
        etsy_api=etsy_api,
        ws_broadcaster=ws_broadcast,
        telegram_broadcaster=telegram_broadcast,
        pinterest_agent=pinterest_agent,
    )
    pepe.register_agent("publisher", publisher_agent)

    learning_loop = LearningLoop(memory=memory)
    logger.info("LearningLoop istanziato")

    bundle_strategy = BundleStrategy(memory=memory, learning_loop=learning_loop)
    logger.info("BundleStrategy istanziato")

    mock_mode = getattr(pepe, "mock_mode", False)

    shop_optimizer = ShopProfileOptimizer(
        memory=memory,
        etsy_client=etsy_api,
        learning_loop=learning_loop,
        mock_mode=mock_mode,
    )
    logger.info("ShopProfileOptimizer istanziato (mock=%s)", mock_mode)

    etsy_ads_manager = EtsyAdsManager(
        etsy_client=etsy_api,
        production_queue=production_queue,
        publication_policy=publication_policy,
        telegram_broadcaster=telegram_broadcast,
        mock_mode=mock_mode,
    )
    logger.info("EtsyAdsManager istanziato (mock=%s)", mock_mode)

    analytics_agent = AnalyticsAgent(
        anthropic_client=pepe.client,
        memory=memory,
        etsy_api=etsy_api,
        ws_broadcaster=ws_broadcast,
        telegram_broadcaster=telegram_broadcast,
        production_queue=production_queue,
        learning_loop=learning_loop,
    )
    pepe.register_agent("analytics", analytics_agent)

    finance_agent = FinanceAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_broadcast,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("finance", finance_agent)

    finance_tracker = FinanceTracker(
        memory=memory,
        telegram_broadcaster=telegram_broadcast,
    )
    logger.info("FinanceTracker istanziato (B5/5.4)")

    recall_agent = RecallAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_broadcast,
    )
    pepe.register_agent("recall", recall_agent)

    remind_agent = RemindAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_broadcast,
        notion_calendar=notion_calendar,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("remind", remind_agent)

    summarize_agent = SummarizeAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_broadcast,
        text_extractor=text_extractor,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("summarize", summarize_agent)

    research_personal_agent = ResearchPersonalAgent(
        anthropic_client=pepe.client,
        memory=memory,
        ws_broadcaster=ws_broadcast,
        web_search=web_search,
        telegram_broadcaster=telegram_broadcast,
    )
    pepe.register_agent("research_personal", research_personal_agent)

    return AgentBundle(
        publisher_agent=publisher_agent,
        analytics_agent=analytics_agent,
        finance_agent=finance_agent,
        recall_agent=recall_agent,
        remind_agent=remind_agent,
        summarize_agent=summarize_agent,
        research_personal_agent=research_personal_agent,
        learning_loop=learning_loop,
        bundle_strategy=bundle_strategy,
        shop_optimizer=shop_optimizer,
        etsy_ads_manager=etsy_ads_manager,
        finance_tracker=finance_tracker,
        pinterest_agent=pinterest_agent,
    )
