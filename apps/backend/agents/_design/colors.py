"""DesignAgent — color helpers and print specs."""
from __future__ import annotations

from reportlab.lib.units import mm

from apps.backend.tools.file_gen import ColorScheme

from apps.backend.agents._design.presets import SAFE_ZONE_MM, BLEED_MM


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Converte hex (#RRGGBB) in tupla RGB (0-255)."""
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _colors_to_scheme(name: str, colors: dict[str, str]) -> ColorScheme:
    """Bridge: converte dict colori hex → ColorScheme per PDFGenerator."""
    return ColorScheme(
        name=name,
        primary=_hex_to_rgb(colors.get("primary", "#4A4A4A")),
        secondary=_hex_to_rgb(colors.get("secondary", "#F5F5F5")),
        accent=_hex_to_rgb(colors.get("text", "#1A1A1A")),
        background=_hex_to_rgb(colors.get("bg", "#FFFFFF")),
    )


def get_print_specs(page_width: float, page_height: float, has_colored_bg: bool) -> dict:
    """Ritorna specifiche print-ready per il documento."""
    safe_zone = SAFE_ZONE_MM * mm
    bleed = BLEED_MM * mm if has_colored_bg else 0

    return {
        "safe_left": safe_zone,
        "safe_right": page_width - safe_zone,
        "safe_top": page_height - safe_zone,
        "safe_bottom": safe_zone,
        "content_width": page_width - (2 * safe_zone),
        "content_height": page_height - (2 * safe_zone),
        "bleed_left": -bleed,
        "bleed_right": page_width + bleed,
        "bleed_top": page_height + bleed,
        "bleed_bottom": -bleed,
        "has_bleed": has_colored_bg,
    }
