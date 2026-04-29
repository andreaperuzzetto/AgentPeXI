"""Dependency injection container per il bot Telegram.

Invece di passare ogni servizio come parametro singolo al costruttore
di TelegramBot, si costruisce un'unica istanza di BotDependencies e la
si passa. Questo rende facile:

  - aggiungere nuovi servizi senza toccare la firma di TelegramBot
  - iniettare mock in test (sostituire solo i campi rilevanti)
  - dividere handler in moduli separati (ogni handler riceve deps)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.backend.agents.analytics import AnalyticsAgent
    from apps.backend.core.autopilot_loop import AutopilotLoop
    from apps.backend.core.budget_manager import BudgetManager
    from apps.backend.core.bundle_strategy import BundleStrategy
    from apps.backend.core.etsy_ads import EtsyAdsManager
    from apps.backend.core.finance_tracker import FinanceTracker
    from apps.backend.core.learning_loop import LearningLoop
    from apps.backend.core.pepe import Pepe
    from apps.backend.core.production_queue import ProductionQueueService
    from apps.backend.core.publication_policy import PublicationPolicy
    from apps.backend.core.scheduler import Scheduler
    from apps.backend.core.shop_optimizer import ShopProfileOptimizer
    from apps.backend.screen.watcher import ScreenWatcher
    from apps.backend.tools.etsy_api import EtsyAPI


@dataclass
class BotDependencies:
    """Raccoglie tutte le dipendenze iniettate nel TelegramBot.

    Obbligatorio:
        pepe — orchestratore principale, sempre presente.

    Opzionali — ``None`` se il servizio non è stato avviato:
        scheduler          — APScheduler wrapper
        screen_watcher     — ScreenWatcher macOS
        autopilot_loop     — AutopilotLoop B2
        production_queue   — ProductionQueueService B2
        budget_manager     — BudgetManager B2
        publication_policy — PublicationPolicy B2
        etsy_api           — EtsyAPI client B3/step 3.6
        analytics_agent    — AnalyticsAgent B4/4.3 (per /ladder diretto)
        learning_loop      — LearningLoop B4/4.5
        bundle_strategy    — BundleStrategy B4/4.6
        shop_optimizer     — ShopProfileOptimizer B5/5.1
        etsy_ads_manager   — EtsyAdsManager B5/5.2
        finance_tracker    — FinanceTracker B5/5.4
    """

    # ── Obbligatorio ──────────────────────────────────────────────────
    pepe: "Pepe"

    # ── Opzionali ─────────────────────────────────────────────────────
    scheduler: "Scheduler | None" = None
    screen_watcher: "ScreenWatcher | None" = None
    autopilot_loop: "AutopilotLoop | None" = None
    production_queue: "ProductionQueueService | None" = None
    budget_manager: "BudgetManager | None" = None
    publication_policy: "PublicationPolicy | None" = None
    etsy_api: "EtsyAPI | None" = None
    analytics_agent: "AnalyticsAgent | None" = None
    learning_loop: "LearningLoop | None" = None
    bundle_strategy: "BundleStrategy | None" = None
    shop_optimizer: "ShopProfileOptimizer | None" = None
    etsy_ads_manager: "EtsyAdsManager | None" = None
    finance_tracker: "FinanceTracker | None" = None
