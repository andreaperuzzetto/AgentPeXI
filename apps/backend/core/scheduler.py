"""Scheduler — APScheduler AsyncIOScheduler integrato in FastAPI."""

from __future__ import annotations

from apps.backend.core._scheduler import (
    _CoreMixin,
    _SystemMixin,
    _WikiMixin,
    _PersonalMixin,
    _EtsyMixin,
    _extract_color_schemes,
)

__all__ = ["Scheduler", "_extract_color_schemes"]


class Scheduler(
    _CoreMixin,
    _SystemMixin,
    _WikiMixin,
    _PersonalMixin,
    _EtsyMixin,
    object,
):
    """Gestione job schedulati con APScheduler (AsyncIO)."""
