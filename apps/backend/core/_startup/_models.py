"""Return-value dataclasses shared across startup phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class _PepeBundle:
    pepe: Any
    research_agent: Any
    design_agent: Any


@dataclass
class _AutonomyBundle:
    db: Any
    production_queue: Any
    budget_manager: Any
    publication_policy: Any


@dataclass
class AgentBundle:
    publisher_agent: Any
    analytics_agent: Any
    finance_agent: Any
    recall_agent: Any
    remind_agent: Any
    summarize_agent: Any
    research_personal_agent: Any
    learning_loop: Any
    bundle_strategy: Any
    shop_optimizer: Any
    etsy_ads_manager: Any
    finance_tracker: Any
    pinterest_agent: Any
