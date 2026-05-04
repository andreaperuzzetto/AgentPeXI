"""MemoryManager — thin assembler that composes all _memory mixins.

Schema: 15 tabelle SQLite (conversations, agent_logs, agent_steps, llm_calls,
tool_calls, etsy_listings, scheduled_tasks, error_log, production_queue,
config, autopilot_state, market_signals, listing_performance,
niche_intelligence, revenue_events).
ChromaDB collection `pepe_memory` con Voyage AI voyage-3-lite embeddings.
"""

from __future__ import annotations

from apps.backend.core._memory._base import MemoryBase
from apps.backend.core._memory._conversations import ConversationsMixin
from apps.backend.core._memory._agent_logs import AgentLogsMixin
from apps.backend.core._memory._analytics import AnalyticsMixin
from apps.backend.core._memory._revenue import RevenueMixin
from apps.backend.core._memory._queue import QueueMixin
from apps.backend.core._memory._etsy_listings import EtsyListingsMixin
from apps.backend.core._memory._pending import PendingMixin
from apps.backend.core._memory._oauth import OAuthMixin
from apps.backend.core._memory._chromadb import ChromaDbMixin
from apps.backend.core._memory._reminders import RemindersMixin
from apps.backend.core._memory._learning import LearningMixin


class MemoryManager(
    ConversationsMixin,
    AgentLogsMixin,
    AnalyticsMixin,
    RevenueMixin,
    QueueMixin,
    EtsyListingsMixin,
    PendingMixin,
    OAuthMixin,
    ChromaDbMixin,
    RemindersMixin,
    LearningMixin,
    MemoryBase,
):
    """Full MemoryManager assembled from mixin classes."""


memory_manager = MemoryManager()
