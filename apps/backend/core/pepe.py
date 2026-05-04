"""Pepe — Orchestratore principale AgentPeXI.

Thin assembler: combina i mixin da _pepe/ in un'unica classe Pepe.
Tutti gli import esistenti ``from apps.backend.core.pepe import Pepe`` restano invariati.
"""

from __future__ import annotations

from apps.backend.core._pepe._base import PepeBase
from apps.backend.core._pepe._confidence import ConfidenceMixin
from apps.backend.core._pepe._context import ContextMixin
from apps.backend.core._pepe._dispatch import DispatchMixin
from apps.backend.core._pepe._domain import DomainMixin
from apps.backend.core._pepe._llm import LlmMixin
from apps.backend.core._pepe._notifications import NotificationsMixin
from apps.backend.core._pepe._pipeline import PipelineMixin
from apps.backend.core._pepe._watcher import WatcherMixin

__all__ = ["Pepe"]


class Pepe(
    ConfidenceMixin,
    PipelineMixin,
    ContextMixin,
    WatcherMixin,
    DispatchMixin,
    DomainMixin,
    LlmMixin,
    NotificationsMixin,
    PepeBase,
):
    """Orchestratore centrale: gestisce queue, agenti, e interazione utente."""
