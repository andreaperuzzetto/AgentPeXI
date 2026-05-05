"""DesignAgent — genera digital products (PDF, PNG, SVG) per Etsy."""

from __future__ import annotations

import logging
from typing import Any, Callable, ClassVar, Coroutine

import anthropic

# Sub-module imports
from apps.backend.agents._design.presets import (
    FONTS_DIR,
    STYLE_PRESETS,
    PRESET_KEYWORDS,
    AVAILABLE_TEMPLATES,
    _TEMPLATE_TO_GEN,
    SAFE_ZONE_MM,
    BLEED_MM,
    _REGISTERED_FONTS,
)
from apps.backend.agents._design.colors import _hex_to_rgb, _colors_to_scheme, get_print_specs
from apps.backend.agents._design.utils import _niche_slug, _get_cover_title, _count_pdf_pages
from apps.backend.agents._design.layout import (
    draw_corner_ornaments,
    draw_ornamental_separator,
    add_instructions_page,
    set_pdf_metadata,
)
from apps.backend.agents._design.scoring import _validate_pdf, _calculate_design_confidence
from apps.backend.agents._design.selection_mixin import _DesignSelectionMixin
from apps.backend.agents._design.validation_mixin import _DesignValidationMixin
from apps.backend.agents._design.generators_mixin import _DesignGeneratorsMixin

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentCard, AgentResult, AgentTask, TaskStatus
from apps.backend.core.storage import StorageManager
from apps.backend.tools.file_gen import ColorScheme, PDFGenerator
from apps.backend.tools.image_gen import create_image_generator
from apps.backend.tools.svg_gen import SVGGenerator
from apps.backend.tools.playwright_export import generate_pdf_thumbnail

logger = logging.getLogger("agentpexi.design")

__all__ = [
    "DesignAgent",
    "STYLE_PRESETS", "PRESET_KEYWORDS", "AVAILABLE_TEMPLATES",
    "get_print_specs", "_hex_to_rgb", "_colors_to_scheme",
    "_niche_slug", "_get_cover_title",
    "draw_corner_ornaments", "draw_ornamental_separator",
    "add_instructions_page", "set_pdf_metadata",
    "_validate_pdf", "_calculate_design_confidence",
    "SAFE_ZONE_MM", "BLEED_MM",
]

# =====================================================================
# DesignAgent
# =====================================================================

class DesignAgent(
    _DesignGeneratorsMixin,
    _DesignValidationMixin,
    _DesignSelectionMixin,
    AgentBase,
):
    """Agente per generazione digital products (Printable PDF focus)."""

    card: ClassVar[AgentCard] = AgentCard(
        name="design",
        description="Genera PDF/PNG/SVG con template ottimali per la nicchia",
        input_schema={"product_type": "str", "niche": "str", "research_context": "dict"},
        layer="business",
        llm="sonnet",
        confidence_threshold=0.85,
        pipeline_position=2,
    )

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        storage: StorageManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
        telegram_broadcaster: Callable[[str], Coroutine] | None = None,
        get_mock_mode: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(
            name="design",
            model=MODEL_HAIKU,
            anthropic_client=anthropic_client,
            memory=memory,
            ws_broadcaster=ws_broadcaster,
        )
        self.storage = storage
        self._telegram_broadcast = telegram_broadcaster
        self._pdf_gen = PDFGenerator()
        self._image_gen = create_image_generator()
        self._svg_gen = SVGGenerator()
        self._get_mock_mode = get_mock_mode or (lambda: False)

    def _extra_init_kwargs(self) -> dict:
        return {"storage": self.storage, "get_mock_mode": self._get_mock_mode}

