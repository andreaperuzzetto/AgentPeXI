"""AgentCoreProtocol — structural type shared by all AgentBase mixins.

Annotate ``self`` with ``AgentCoreProtocol`` in any mixin method that accesses
attributes defined on a sibling mixin or on ``AgentBase`` itself.  This removes
the need for ``# type: ignore[attr-defined]`` comments across the three mixin
files.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import anthropic

if TYPE_CHECKING:
    from apps.backend.core.memory import MemoryManager
    from apps.backend.core.models import AgentResult, AgentTask


@runtime_checkable
class AgentCoreProtocol(Protocol):
    """Structural interface that every ``AgentBase`` instance satisfies."""

    # ── AgentBase.__init__ attributes ────────────────────────────────────────
    memory: MemoryManager
    model: str
    name: str
    client: anthropic.AsyncAnthropic
    _task_id: str
    _step_counter: int
    _llm_call_count: int
    _tool_call_count: int
    _total_cost: float
    _total_tokens: int
    _counters_lock: asyncio.Lock
    _ws_broadcast: Callable[[dict[str, Any]], Awaitable[None]] | None

    # ── Cross-mixin methods ──────────────────────────────────────────────────

    async def _log_step(
        self,
        step_type: str,
        description: str | None,
        input_data: Any = ...,
        output_data: Any = ...,
        duration_ms: int = ...,
    ) -> int: ...

    async def _broadcast(self, event: dict[str, Any]) -> None: ...

    @staticmethod
    def _estimate_cost(
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = ...,
        cache_write: int = ...,
    ) -> float: ...

    def _extra_init_kwargs(self) -> dict[str, Any]: ...

    async def run(self, task: AgentTask) -> AgentResult: ...
