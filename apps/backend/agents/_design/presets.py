"""DesignAgent — font registration, style presets, and template constants."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = logging.getLogger("agentpexi.design")

# =====================================================================
# Font Registration (Intervento 1-2)
# =====================================================================

FONTS_DIR = Path(__file__).parent.parent.parent / "assets" / "fonts"


def _register_fonts() -> dict[str, bool]:
    """Registra font custom. Ritorna dict con disponibilità per preset."""
    registered: dict[str, bool] = {}
    font_map = {
        "PlayfairDisplay": ("PlayfairDisplay-Regular.ttf", "PlayfairDisplay-Bold.ttf"),
        "Lato": ("Lato-Regular.ttf", "Lato-Bold.ttf"),
        "Raleway": ("Raleway-Regular.ttf", "Raleway-Bold.ttf"),
        "JosefinSans": ("JosefinSans-Regular.ttf", "JosefinSans-Bold.ttf"),
    }
    for font_name, (regular_file, bold_file) in font_map.items():
        try:
            regular_path = FONTS_DIR / regular_file
            bold_path = FONTS_DIR / bold_file
            if regular_path.exists() and bold_path.exists():
                pdfmetrics.registerFont(TTFont(font_name, str(regular_path)))
                pdfmetrics.registerFont(TTFont(f"{font_name}-Bold", str(bold_path)))
                registered[font_name] = True
            else:
                registered[font_name] = False
        except Exception:
            registered[font_name] = False
    return registered


_REGISTERED_FONTS = _register_fonts()

# =====================================================================
# Style Presets (Intervento 2)
# =====================================================================

STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "minimal": {
        "font_primary": "Lato" if _REGISTERED_FONTS.get("Lato") else "Helvetica",
        "font_heading": "Lato" if _REGISTERED_FONTS.get("Lato") else "Helvetica",
        "font_accent": "Lato" if _REGISTERED_FONTS.get("Lato") else "Helvetica",
        "bg_color": "#FFFFFF",
        "text_color": "#1A1A1A",
        "accent_color": "#4A4A4A",
        "line_weight": 0.5,
        "decorative": False,
        "description": "Clean, professional, whitespace-focused",
    },
    "decorative": {
        "font_primary": "PlayfairDisplay" if _REGISTERED_FONTS.get("PlayfairDisplay") else "Times-Roman",
        "font_heading": "PlayfairDisplay" if _REGISTERED_FONTS.get("PlayfairDisplay") else "Times-Roman",
        "font_accent": "Raleway" if _REGISTERED_FONTS.get("Raleway") else "Helvetica",
        "bg_color": "#FDFAF6",
        "text_color": "#2C1810",
        "accent_color": "#8B6914",
        "line_weight": 1.0,
        "decorative": True,
        "description": "Elegant, ornamental, serif-forward",
    },
    "corporate": {
        "font_primary": "Raleway" if _REGISTERED_FONTS.get("Raleway") else "Helvetica",
        "font_heading": "Raleway" if _REGISTERED_FONTS.get("Raleway") else "Helvetica",
        "font_accent": "Lato" if _REGISTERED_FONTS.get("Lato") else "Helvetica",
        "bg_color": "#F8F9FA",
        "text_color": "#212529",
        "accent_color": "#0056B3",
        "line_weight": 0.75,
        "decorative": False,
        "description": "Structured, data-driven, business-ready",
    },
    "playful": {
        "font_primary": "JosefinSans" if _REGISTERED_FONTS.get("JosefinSans") else "Helvetica",
        "font_heading": "JosefinSans" if _REGISTERED_FONTS.get("JosefinSans") else "Helvetica",
        "font_accent": "Lato" if _REGISTERED_FONTS.get("Lato") else "Helvetica",
        "bg_color": "#FFFDE7",
        "text_color": "#1A237E",
        "accent_color": "#E91E63",
        "line_weight": 1.5,
        "decorative": True,
        "description": "Fun, colorful, casual and approachable",
    },
}

# =====================================================================
# Preset Keywords (Intervento 3)
# =====================================================================

PRESET_KEYWORDS: dict[str, list[str]] = {
    "minimal": [
        "minimal", "clean", "simple", "modern", "planner", "tracker",
        "budget", "finance", "habit", "productivity", "journal", "log",
        "checklist", "organizer", "schedule", "calendar",
    ],
    "decorative": [
        "wedding", "bridal", "floral", "botanical", "vintage", "elegant",
        "luxury", "boho", "feminine", "aesthetic", "invitation", "birth",
        "anniversary", "watercolor", "hand-lettered", "script",
    ],
    "corporate": [
        "business", "professional", "corporate", "report", "invoice",
        "proposal", "pitch", "strategy", "marketing", "analytics",
        "template", "presentation", "office", "work", "career", "resume",
    ],
    "playful": [
        "kids", "children", "baby", "fun", "colorful", "cute", "activity",
        "game", "educational", "school", "teacher", "classroom", "sticker",
        "birthday", "party", "celebration", "gift", "creative",
    ],
}

# =====================================================================
# Available Templates (Intervento 5)
# =====================================================================

AVAILABLE_TEMPLATES: dict[str, list[str]] = {
    "printable_pdf": [
        "weekly_planner", "daily_planner", "monthly_planner",
        "budget_tracker", "habit_tracker", "goal_planner",
        "meal_planner", "workout_tracker", "gratitude_journal",
        "reading_log", "travel_planner", "project_planner",
    ],
    "digital_art_png": [
        "wall_art_quote", "botanical_print", "abstract_art",
        "watercolor_print", "minimalist_poster", "vintage_poster",
    ],
    "svg_bundle": [
        "icon_set", "pattern_bundle", "monogram_set",
        "clipart_bundle", "frame_bundle",
    ],
}

# Mapping template → generator (file_gen.py supporta questi)
_TEMPLATE_TO_GEN: dict[str, str] = {
    "weekly_planner": "weekly_planner",
    "daily_planner": "daily_journal",
    "monthly_planner": "weekly_planner",
    "budget_tracker": "budget_sheet",
    "habit_tracker": "habit_tracker",
    "goal_planner": "weekly_planner",
    "meal_planner": "weekly_planner",
    "workout_tracker": "habit_tracker",
    "gratitude_journal": "daily_journal",
    "reading_log": "daily_journal",
    "travel_planner": "weekly_planner",
    "project_planner": "weekly_planner",
}

# =====================================================================
# Print Specs (Intervento 9)
# =====================================================================

SAFE_ZONE_MM = 5
BLEED_MM = 3
