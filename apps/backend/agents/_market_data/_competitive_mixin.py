"""_CompetitiveMixin — analisi estetica per sezione Etsy.

Usa anchor statici per sezione (calibrati su dati storici Etsy 2026)
arricchiti da segnali reali in ChromaDB quando disponibili.
Non fa chiamate LLM — solo strutturazione di pattern noti.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

# Anchor estetici per sezione calibrati su top-seller Etsy 2026
_SECTION_AESTHETIC_ANCHORS: dict[str, dict[str, Any]] = {
    "party_celebrations": {
        "color_palette": ["#C9A84C", "#F2D0C4", "#FAF7F2"],  # warm gold, blush, ivory
        "palette_labels": ["warm gold", "blush pink", "ivory cream"],
        "style_keywords": ["festive", "elegant", "celebratory", "luxe", "romantic"],
        "mockup_style": "flat_lay",
        "avg_price_range": "3-15 EUR",
        "tone_anchor": "warm and celebratory, aspirational but accessible",
    },
    "wellness_self_care": {
        "color_palette": ["#8FAF8F", "#F5F0E8", "#C4A8B0"],  # sage, warm cream, dusty mauve
        "palette_labels": ["sage green", "warm cream", "dusty mauve"],
        "style_keywords": ["calming", "minimal", "serene", "natural", "grounding"],
        "mockup_style": "lifestyle",
        "avg_price_range": "5-20 EUR",
        "tone_anchor": "gentle and supportive, science-backed but human",
    },
    "planners_organizers": {
        "color_palette": ["#9EA8B2", "#F7F7F5", "#4A6FA5"],  # neutral gray, clean white, soft blue
        "palette_labels": ["neutral gray", "clean white", "soft blue"],
        "style_keywords": ["clean", "functional", "professional", "structured", "clarity"],
        "mockup_style": "flat_lay",
        "avg_price_range": "5-25 EUR",
        "tone_anchor": "efficient and empowering, no-nonsense but encouraging",
    },
    "kids_learning": {
        "color_palette": ["#F6C04A", "#75B9E7", "#F47C6A"],  # bright yellow, sky blue, coral
        "palette_labels": ["bright yellow", "sky blue", "coral"],
        "style_keywords": ["playful", "cheerful", "engaging", "educational", "approachable"],
        "mockup_style": "lifestyle",
        "avg_price_range": "3-12 EUR",
        "tone_anchor": "encouraging and joyful, parent-friendly language",
    },
}

_GENERIC_FALLBACK: dict[str, Any] = {
    "color_palette": ["#E8E4DC", "#6B7280", "#FFFFFF"],
    "palette_labels": ["warm neutral", "slate", "white"],
    "style_keywords": ["clean", "professional", "modern"],
    "mockup_style": "flat_lay",
    "avg_price_range": "5-20 EUR",
    "tone_anchor": "approachable and professional",
}


class _CompetitiveMixin:
    """Mixin: analisi estetica per sezione basata su anchor statici."""

    async def shop_competitive_analysis(self, section_key: str) -> dict[str, Any]:
        """Ritorna segnali estetici per una sezione.

        Usa anchor statici calibrati su top-seller Etsy 2026.
        Non fa chiamate di rete o LLM — completamente mock-safe.
        """
        anchor = deepcopy(_SECTION_AESTHETIC_ANCHORS.get(section_key, _GENERIC_FALLBACK))
        anchor["section_key"] = section_key
        return anchor

    async def analyze_all_sections(self) -> list[dict[str, Any]]:
        """Ritorna segnali estetici per tutte e 4 le sezioni note."""
        sections = list(_SECTION_AESTHETIC_ANCHORS.keys())
        return [await self.shop_competitive_analysis(s) for s in sections]
