"""Startup orchestration — thin dispatcher.

Each public name is re-exported from the relevant _startup sub-module so that
all callers (api/main.py lifespan, tests, …) continue to use this module path
without any changes.
"""

from __future__ import annotations

from apps.backend.core._startup._models import AgentBundle, _AutonomyBundle, _PepeBundle
from apps.backend.core._startup._infra import (
    init_memory,
    init_tools,
    init_storage,
    init_screen_watcher,
)
from apps.backend.core._startup._agents import (
    init_pepe,
    init_wiki,
    init_etsy,
    init_autonomy_services,
    init_all_agents,
)
from apps.backend.core._startup._autopilot_builder import (
    build_autopilot_callables,
    init_autopilot_loop,
)
from apps.backend.core._startup._services import init_scheduler, init_telegram_bot

__all__ = [
    "AgentBundle",
    "_PepeBundle",
    "_AutonomyBundle",
    "init_memory",
    "init_tools",
    "init_storage",
    "init_screen_watcher",
    "init_pepe",
    "init_wiki",
    "init_etsy",
    "init_autonomy_services",
    "init_all_agents",
    "build_autopilot_callables",
    "init_autopilot_loop",
    "init_scheduler",
    "init_telegram_bot",
]
