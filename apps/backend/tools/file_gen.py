"""PDFGenerator — genera Printable PDF con ReportLab + Pillow per AgentPeXI."""

from __future__ import annotations

import asyncio
from pathlib import Path

from apps.backend.tools._file_gen._pdf_helpers import (
    ColorScheme,
    DEFAULT_SCHEMES,
    SCHEME_BY_NAME,
    MARGIN,
    SIZES,
    FONTS,
    _rgb,
    _fill_page,
    _draw_lines,
    _draw_instructions_page,
    _draw_cover,
)
from apps.backend.tools._file_gen._planner_mixin import _PlannerMixin
from apps.backend.tools._file_gen._habit_mixin import _HabitMixin
from apps.backend.tools._file_gen._budget_mixin import _BudgetMixin
from apps.backend.tools._file_gen._journal_mixin import _JournalMixin

__all__ = ["PDFGenerator", "ColorScheme"]


# ---------------------------------------------------------------------------
# PDFGenerator
# ---------------------------------------------------------------------------


class PDFGenerator(_JournalMixin, _BudgetMixin, _HabitMixin, _PlannerMixin, object):
    """Genera Printable PDF con ReportLab + Pillow."""

    async def generate(
        self,
        template: str,
        scheme: ColorScheme,
        size: str,
        output_path: Path,
        **kwargs,
    ) -> Path:
        """Dispatcher async. Chiama il metodo giusto, ritorna Path al file generato."""
        generators = {
            "weekly_planner": self._generate_weekly_planner,
            "habit_tracker": self._generate_habit_tracker,
            "budget_sheet": self._generate_budget_sheet,
            "daily_journal": self._generate_daily_journal,
        }
        fn = generators.get(template)
        if fn is None:
            raise ValueError(f"Template sconosciuto: {template!r}. Disponibili: {list(generators)}")

        # ReportLab è sync — eseguiamo in thread pool
        return await asyncio.to_thread(fn, scheme, size, output_path, **kwargs)
