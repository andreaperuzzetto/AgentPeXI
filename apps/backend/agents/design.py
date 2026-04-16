"""DesignAgent — genera digital products (PDF, PNG, SVG) per Etsy."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable, Coroutine

import anthropic
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from apps.backend.agents.base import AgentBase
from apps.backend.core.config import MODEL_HAIKU
from apps.backend.core.memory import MemoryManager
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.core.storage import StorageManager
from apps.backend.tools.file_gen import ColorScheme, PDFGenerator
from apps.backend.tools.image_gen import ImageGenerator
from apps.backend.tools.svg_gen import SVGGenerator
from apps.backend.tools.playwright_export import generate_pdf_thumbnail

logger = logging.getLogger("agentpexi.design")

# =====================================================================
# Font Registration (Intervento 1-2)
# =====================================================================

FONTS_DIR = Path(__file__).parent.parent / "assets" / "fonts"


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


# =====================================================================
# Helpers — funzioni di modulo
# =====================================================================

def _niche_slug(niche: str, max_len: int = 40) -> str:
    """Converte niche in slug filesystem-safe."""
    slug = re.sub(r"[^a-z0-9]+", "_", niche.lower()).strip("_")
    return slug[:max_len]


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


def _get_cover_title(niche: str, template: str, research_context: dict | None) -> str:
    """Genera titolo cover che include la keyword primaria per SEO (Intervento 6)."""
    if research_context:
        top_keywords = research_context.get("top_keywords", [])
        if top_keywords:
            primary_keyword = top_keywords[0]
            title = f"{primary_keyword.title()} {template.replace('_', ' ').title()}"
            return title[:60]

    return f"{niche.title()} {template.replace('_', ' ').title()}"[:60]


def _count_pdf_pages(pdf_path: Path) -> int:
    """Conta le pagine di un PDF generato."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
    except Exception:
        return 0


# =====================================================================
# Decorative Elements (Intervento 8)
# =====================================================================

def draw_corner_ornaments(
    canvas: Any, page_width: float, page_height: float, color: str, preset: str,
) -> None:
    """Aggiunge ornamenti angolari per preset decorative/playful."""
    if preset not in ("decorative", "playful"):
        return

    c = HexColor(color)
    canvas.setStrokeColor(c)
    canvas.setFillColor(c)
    margin = 8 * mm
    size = 12 * mm

    if preset == "decorative":
        line_width = 0.8
        canvas.setLineWidth(line_width)
        corners = [
            [(margin, page_height - margin), (margin + size, page_height - margin)],
            [(margin, page_height - margin), (margin, page_height - margin - size)],
            [(page_width - margin, page_height - margin), (page_width - margin - size, page_height - margin)],
            [(page_width - margin, page_height - margin), (page_width - margin, page_height - margin - size)],
            [(margin, margin), (margin + size, margin)],
            [(margin, margin), (margin, margin + size)],
            [(page_width - margin, margin), (page_width - margin - size, margin)],
            [(page_width - margin, margin), (page_width - margin, margin + size)],
        ]
        for (x1, y1), (x2, y2) in corners:
            canvas.line(x1, y1, x2, y2)

        inner = 2 * mm
        inner_size = size * 0.6
        canvas.setLineWidth(line_width * 0.5)
        inner_corners = [
            [(margin + inner, page_height - margin - inner), (margin + inner + inner_size, page_height - margin - inner)],
            [(margin + inner, page_height - margin - inner), (margin + inner, page_height - margin - inner - inner_size)],
            [(page_width - margin - inner, page_height - margin - inner), (page_width - margin - inner - inner_size, page_height - margin - inner)],
            [(page_width - margin - inner, page_height - margin - inner), (page_width - margin - inner, page_height - margin - inner - inner_size)],
            [(margin + inner, margin + inner), (margin + inner + inner_size, margin + inner)],
            [(margin + inner, margin + inner), (margin + inner, margin + inner + inner_size)],
            [(page_width - margin - inner, margin + inner), (page_width - margin - inner - inner_size, margin + inner)],
            [(page_width - margin - inner, margin + inner), (page_width - margin - inner, margin + inner + inner_size)],
        ]
        for (x1, y1), (x2, y2) in inner_corners:
            canvas.line(x1, y1, x2, y2)

    elif preset == "playful":
        canvas.setLineWidth(1.5)
        radius = 4 * mm
        positions = [
            (margin, page_height - margin),
            (page_width - margin, page_height - margin),
            (margin, margin),
            (page_width - margin, margin),
        ]
        for x, y in positions:
            canvas.circle(x, y, radius, stroke=1, fill=0)
            canvas.circle(x, y, radius * 0.5, stroke=0, fill=1)


def draw_ornamental_separator(
    canvas: Any, x: float, y: float, width: float, color: str, preset: str,
) -> None:
    """Separatore ornamentale tra sezioni del documento."""
    canvas.setStrokeColor(HexColor(color))

    if preset not in ("decorative", "playful"):
        canvas.setLineWidth(0.5)
        canvas.line(x, y, x + width, y)
        return

    if preset == "decorative":
        mid = x + width / 2
        diamond_size = 2 * mm
        canvas.setLineWidth(0.6)
        canvas.line(x, y, mid - 3 * diamond_size, y)
        canvas.line(mid + 3 * diamond_size, y, x + width, y)
        canvas.setFillColor(HexColor(color))
        for offset in [-1.5 * diamond_size, 0, 1.5 * diamond_size]:
            cx = mid + offset
            canvas.beginPath()
            canvas.moveTo(cx, y + diamond_size)
            canvas.lineTo(cx + diamond_size, y)
            canvas.lineTo(cx, y - diamond_size)
            canvas.lineTo(cx - diamond_size, y)
            canvas.closePath()
            canvas.fill()

    elif preset == "playful":
        canvas.setLineWidth(1.5)
        canvas.setDash([3, 4], 0)
        canvas.line(x, y, x + width, y)
        canvas.setDash([], 0)


# =====================================================================
# Instructions Page (Intervento 10)
# =====================================================================

def add_instructions_page(canvas: Any, pagesize: tuple[float, float], preset: str) -> None:
    """Ultima pagina standardizzata con istruzioni d'uso e credenziali."""
    preset_data = STYLE_PRESETS.get(preset, STYLE_PRESETS["minimal"])
    font_primary = preset_data["font_primary"]

    canvas.showPage()
    canvas.saveState()

    w, h = pagesize

    # Sfondo
    canvas.setFillColor(HexColor(preset_data["bg_color"]))
    canvas.rect(0, 0, w, h, fill=1, stroke=0)

    margin = 20 * mm

    # Titolo
    heading_font = f"{font_primary}-Bold" if _REGISTERED_FONTS.get(font_primary.replace("-Bold", "").replace("Bold", "")) else "Helvetica-Bold"
    body_font = font_primary if _REGISTERED_FONTS.get(font_primary.replace("-Bold", "").replace("Bold", "")) else "Helvetica"

    canvas.setFillColor(HexColor(preset_data["text_color"]))
    canvas.setFont(heading_font, 18)
    canvas.drawCentredString(w / 2, h - margin - 10 * mm, "Thank You for Your Purchase!")

    # Linea separatrice
    canvas.setStrokeColor(HexColor(preset_data["accent_color"]))
    canvas.setLineWidth(1)
    canvas.line(margin, h - margin - 18 * mm, w - margin, h - margin - 18 * mm)

    # Contenuto
    canvas.setFont(body_font, 11)
    canvas.setFillColor(HexColor(preset_data["text_color"]))

    instructions = [
        ("HOW TO USE:", [
            "• Print at home or at a local print shop",
            "• Recommended paper: 90gsm or heavier for best results",
            "• Print size: A4 / US Letter (as specified in product title)",
            "• For best quality: print at 300 DPI or higher",
        ]),
        ("TIPS:", [
            "• Use a PDF viewer (Adobe Acrobat) for best print quality",
            "• For digital use: open in GoodNotes, Notability, or Noteshelf",
            "• Laminate for durability if using physically",
        ]),
        ("LICENSE:", [
            "• Personal use only — not for resale or redistribution",
            "• You may print unlimited copies for personal use",
            "• Commercial license available — contact us",
        ]),
    ]

    y_pos = h - margin - 30 * mm
    for section_title, items in instructions:
        canvas.setFont(heading_font, 10)
        canvas.setFillColor(HexColor(preset_data["accent_color"]))
        canvas.drawString(margin, y_pos, section_title)
        y_pos -= 6 * mm

        canvas.setFont(body_font, 10)
        canvas.setFillColor(HexColor(preset_data["text_color"]))
        for item in items:
            canvas.drawString(margin + 3 * mm, y_pos, item)
            y_pos -= 5.5 * mm
        y_pos -= 5 * mm

    # Footer
    canvas.setFont(body_font, 8)
    canvas.setFillColor(HexColor(preset_data["accent_color"]))
    canvas.drawCentredString(w / 2, margin, "Questions? We're here to help — visit our Etsy shop for support")

    canvas.restoreState()


# =====================================================================
# PDF Metadata (Intervento 11)
# =====================================================================

def set_pdf_metadata(canvas: Any, niche: str, template: str, product_type: str) -> None:
    """Imposta metadata PDF per SEO e identificazione prodotto."""
    title = f"{niche.title()} {template.replace('_', ' ').title()}"
    canvas.setTitle(title)
    canvas.setAuthor("AgentPeXI Digital Products")
    canvas.setSubject(f"Printable {product_type.replace('_', ' ').title()} - {niche}")
    canvas.setKeywords(f"{niche}, printable, {template.replace('_', ' ')}, digital download, Etsy")
    canvas.setCreator("AgentPeXI v1.0")
    canvas.setProducer("ReportLab PDF Library")


# =====================================================================
# PDF Validation (Intervento 14)
# =====================================================================

async def _validate_pdf(pdf_path: Path, template: str, expected_pages: int) -> dict:
    """Valida il PDF generato: dimensione minima e conteggio pagine."""
    issues: list[str] = []

    if not pdf_path.exists():
        return {"valid": False, "issues": ["PDF file not found"], "file_size_kb": 0, "page_count": 0}

    file_size_kb = pdf_path.stat().st_size / 1024

    MIN_SIZE_KB: dict[str, float] = {
        "weekly_planner": 50,
        "daily_planner": 60,
        "monthly_planner": 80,
        "budget_tracker": 50,
        "habit_tracker": 50,
        "default": 30,
    }
    min_size = MIN_SIZE_KB.get(template, MIN_SIZE_KB["default"])

    if file_size_kb < min_size:
        issues.append(f"File too small: {file_size_kb:.1f}KB (min {min_size}KB) — possible generation error")

    page_count = 0
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        page_count = len(reader.pages)

        if expected_pages > 0 and page_count < expected_pages:
            issues.append(f"Wrong page count: {page_count} (expected {expected_pages})")

        if page_count > 0 and file_size_kb / page_count < 1.0:
            issues.append(f"Pages seem empty: avg {file_size_kb / page_count:.1f}KB per page")

    except Exception as e:
        issues.append(f"Could not read PDF for validation: {e}")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "file_size_kb": round(file_size_kb, 1),
        "page_count": page_count,
    }


# =====================================================================
# Confidence Scoring (Intervento 15)
# =====================================================================

def _calculate_design_confidence(
    variants_generated: int,
    variants_requested: int,
    thumbnails: list[dict],
    validation_results: list[dict],
    fonts_available: dict[str, bool],
    research_available: bool,
) -> tuple[float, list[str]]:
    """
    Calcola confidence score per il Design Agent.

    Pesi:
    - Variants completati: 0.35
    - Thumbnails generati: 0.25
    - PDF validation passed: 0.20
    - Font custom disponibili: 0.10
    - Research context disponibile: 0.10
    """
    missing_data: list[str] = []
    score = 0.0

    # Variants (0.35)
    if variants_requested > 0:
        ratio = variants_generated / variants_requested
        score += 0.35 * ratio
        if ratio < 1.0:
            missing_data.append(f"{variants_requested - variants_generated} variants failed to generate")

    # Thumbnails (0.25)
    thumbnail_score = 0.0
    total_thumbnails = variants_generated * 3
    generated_thumbnails = sum(
        (1 if t.get("cover") else 0)
        + (1 if t.get("interior") else 0)
        + (1 if t.get("mockup") else 0)
        for t in thumbnails
    )
    if total_thumbnails > 0:
        thumbnail_score = generated_thumbnails / total_thumbnails
    score += 0.25 * thumbnail_score
    if thumbnail_score < 1.0:
        missing_data.append("Some thumbnails failed to generate")

    # PDF validation (0.20)
    if validation_results:
        valid_count = sum(1 for v in validation_results if v.get("valid", False))
        val_ratio = valid_count / len(validation_results)
        score += 0.20 * val_ratio
        if val_ratio < 1.0:
            missing_data.append("Some PDFs failed validation")
    else:
        score += 0.10
        missing_data.append("PDF validation not performed")

    # Font custom (0.10)
    fonts_ok = sum(1 for v in fonts_available.values() if v)
    fonts_total = len(fonts_available) if fonts_available else 1
    font_ratio = fonts_ok / fonts_total
    score += 0.10 * font_ratio
    if font_ratio < 0.5:
        missing_data.append("Custom fonts not available — using fallback fonts")

    # Research context (0.10)
    if research_available:
        score += 0.10
    else:
        missing_data.append("No research context — template/colors not niche-optimized")

    return round(min(score, 1.0), 3), missing_data


# =====================================================================
# Async module-level helpers
# =====================================================================

async def _select_preset(
    niche: str,
    template: str,
    research_context: dict | None,
    anthropic_client: anthropic.AsyncAnthropic,
) -> str:
    """
    Stage 1: Keyword scoring veloce (70-80% dei casi).
    Stage 2: LLM con contesto completo per i casi ambigui.
    Stage 3: Validazione output finale.
    (Intervento 3)
    """
    text = f"{niche} {template}".lower()

    # Stage 1: Keyword scoring
    scores = {preset: 0 for preset in PRESET_KEYWORDS}
    for preset, keywords in PRESET_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                scores[preset] += 1

    max_score = max(scores.values())
    if max_score >= 2:
        winner = max(scores, key=scores.get)  # type: ignore[arg-type]
        return winner

    # Stage 2: LLM per casi ambigui
    research_summary = ""
    if research_context:
        research_summary = f"""
Research context:
- Target audience: {research_context.get('target_audience', 'unknown')}
- Price range: {research_context.get('avg_price', 'unknown')}
- Top keywords: {', '.join(research_context.get('top_keywords', [])[:5])}
- Competition level: {research_context.get('competition_level', 'unknown')}
"""

    prompt = f"""Select the best visual style preset for this Etsy digital product.

Product niche: {niche}
Template type: {template}
{research_summary}

Available presets:
- minimal: Clean, professional, whitespace-focused. For planners, budgets, productivity tools.
- decorative: Elegant, ornamental, serif fonts. For weddings, botanical, vintage, luxury products.
- corporate: Structured, business-ready. For professional templates, business tools, reports.
- playful: Fun, colorful, casual. For kids, educational, party, creative products.

Respond with ONLY one word: minimal, decorative, corporate, or playful"""

    try:
        response = await anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip().lower()
    except Exception:
        result = "minimal"

    # Stage 3: Validazione
    if result not in STYLE_PRESETS:
        result = "minimal"

    return result


async def _resolve_color_scheme_niche_aware(
    color_scheme_name: str,
    niche: str,
    preset: str,
    anthropic_client: anthropic.AsyncAnthropic,
) -> dict[str, str]:
    """
    Genera palette colori coerente con nicchia e preset (Intervento 4).
    Ritorna: {"primary": "#hex", "secondary": "#hex", "accent": "#hex", "bg": "#hex", "text": "#hex"}
    """
    preset_data = STYLE_PRESETS[preset]

    prompt = f"""Generate a color palette for an Etsy digital product.
Return ONLY a JSON object, no explanation.

Product niche: {niche}
Style preset: {preset} ({preset_data['description']})
Requested color scheme: {color_scheme_name}

Requirements:
- Colors must feel cohesive and professional
- Background should be light (for printability)
- Text must have minimum 4.5:1 contrast ratio with background
- Accent should complement, not clash
- Inspired by {color_scheme_name} palette but adapted for {niche}

Return exactly:
{{"primary": "#hex", "secondary": "#hex", "accent": "#hex", "bg": "#hex", "text": "#hex"}}"""

    try:
        response = await anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        match = re.search(r"\{[^}]+\}", raw)
        if match:
            data = json.loads(match.group())
            required_keys = {"primary", "secondary", "accent", "bg", "text"}
            if required_keys.issubset(data.keys()):
                for val in data.values():
                    if not re.match(r"^#[0-9A-Fa-f]{6}$", val):
                        raise ValueError(f"Invalid hex: {val}")
                return data
    except Exception:
        pass

    # Fallback: usa colori del preset
    return {
        "primary": preset_data["accent_color"],
        "secondary": preset_data["bg_color"],
        "accent": preset_data["accent_color"],
        "bg": preset_data["bg_color"],
        "text": preset_data["text_color"],
    }


async def _select_template_llm(
    niche: str,
    product_type: str,
    research_context: dict | None,
    anthropic_client: anthropic.AsyncAnthropic,
) -> str:
    """Seleziona il template più adatto alla nicchia tramite LLM (Intervento 5)."""
    templates = AVAILABLE_TEMPLATES.get(product_type, ["weekly_planner"])

    research_info = ""
    if research_context:
        top_keywords = research_context.get("top_keywords", [])
        gaps = research_context.get("gaps", [])
        research_info = f"""
Research insights:
- Top buyer keywords: {', '.join(top_keywords[:5])}
- Market gaps to fill: {', '.join(gaps[:3])}
- Avg price: {research_context.get('avg_price', 'unknown')}
"""

    prompt = f"""Select the best template for this Etsy digital product.

Niche: {niche}
Product type: {product_type}
{research_info}

Available templates:
{chr(10).join(f'- {t}' for t in templates)}

Choose the template that:
1. Best matches what buyers in this niche actually search for
2. Has the highest commercial potential
3. Is coherent with the niche identity

Respond with ONLY the template name, exactly as listed."""

    try:
        response = await anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=30,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip().lower().replace(" ", "_")
        if result in templates:
            return result
    except Exception:
        pass

    return templates[0]


async def _should_include_dates(
    template: str,
    niche: str,
    anthropic_client: anthropic.AsyncAnthropic,
) -> bool:
    """Decide se il planner/tracker deve avere date specifiche o essere undated (Intervento 7)."""
    NO_DATE_TEMPLATES = {
        "wall_art_quote", "botanical_print", "abstract_art",
        "watercolor_print", "minimalist_poster", "vintage_poster",
        "icon_set", "pattern_bundle", "monogram_set",
        "clipart_bundle", "frame_bundle",
    }

    if template in NO_DATE_TEMPLATES:
        return False

    current_month = date.today().month

    prompt = f"""Should this Etsy planner be dated (specific year: 2026) or undated (no specific dates)?

Template: {template}
Niche: {niche}
Current month: {current_month} (1=January, 12=December)

Rules:
- Undated planners sell year-round (safer for evergreen sales)
- Dated planners are more relevant but expire after the year
- If current month is October-December: dated for next year can work
- If current month is January-February: dated for current year works
- Otherwise: undated is usually safer

Respond with ONLY: dated or undated"""

    try:
        response = await anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip().lower()
        return result == "dated"
    except Exception:
        return False


# =====================================================================
# DesignAgent
# =====================================================================

class DesignAgent(AgentBase):
    """Agente per generazione digital products (Printable PDF focus)."""

    def __init__(
        self,
        *,
        anthropic_client: anthropic.AsyncAnthropic,
        memory: MemoryManager,
        storage: StorageManager,
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
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
        self._pdf_gen = PDFGenerator()
        self._image_gen = ImageGenerator()
        self._svg_gen = SVGGenerator()
        self._get_mock_mode = get_mock_mode or (lambda: False)

    # ------------------------------------------------------------------
    # Input validation (Intervento 19)
    # ------------------------------------------------------------------

    async def _validate_and_normalize_input(self, input_data: dict) -> tuple[dict | None, str | None]:
        """Valida e normalizza l'input del Design Agent."""
        required_fields = ["niche", "product_type"]
        for field in required_fields:
            if not input_data.get(field):
                return None, f"Missing required field: {field}"

        product_type = input_data.get("product_type", "")
        valid_types = set(AVAILABLE_TEMPLATES.keys())
        if product_type not in valid_types:
            return None, f"Invalid product_type: {product_type}. Must be one of: {', '.join(valid_types)}"

        template = input_data.get("template")
        if template:
            valid_templates = AVAILABLE_TEMPLATES.get(product_type, [])
            if template not in valid_templates:
                input_data["template"] = None

        num_variants = input_data.get("num_variants", 2)
        if not isinstance(num_variants, int) or num_variants < 1:
            num_variants = 2
        if num_variants > 5:
            num_variants = 5
        input_data["num_variants"] = num_variants

        color_schemes = input_data.get("color_schemes", [])
        if not color_schemes:
            color_schemes = ["neutral", "warm"]
        input_data["color_schemes"] = color_schemes[:num_variants]

        return input_data, None

    # ------------------------------------------------------------------
    # Research context extraction (Intervento 17)
    # ------------------------------------------------------------------

    def _extract_research_context(self, task_input: dict) -> dict | None:
        """Estrae e normalizza il contesto research dal task input."""
        research = task_input.get("research_result") or task_input.get("research_context")
        if not research:
            return None

        market = research.get("market_insights", {})
        return {
            "top_keywords": research.get("top_keywords", [])[:10],
            "avg_price": market.get("avg_price"),
            "competition_level": market.get("competition_level"),
            "target_audience": market.get("target_audience"),
            "gaps": market.get("gaps", []),
            "trending_styles": market.get("trending_styles", []),
            "confidence": research.get("confidence", 0.0),
        }

    # ------------------------------------------------------------------
    # Failure pattern lookup (Intervento 16)
    # ------------------------------------------------------------------

    async def _lookup_failure_patterns(self, niche: str, template: str) -> dict | None:
        """Cerca in ChromaDB pattern di fallimento e design outcome precedenti."""
        try:
            # Failure analysis recenti
            failures = await self.memory.query_chromadb_recent(
                query=f"FAILURE niche {niche} template {template}",
                n_results=3,
                where={"type": "failure_analysis"},
                primary_days=90,
                fallback_days=180,
            )
            # Design outcome recenti
            outcomes = await self.memory.query_chromadb_recent(
                query=f"DESIGN_OUTCOME niche {niche}",
                n_results=5,
                where={"type": "design_outcome"},
                primary_days=90,
                fallback_days=180,
            )

            known_issues = [r["document"] for r in failures[:2]] if failures else []
            avoid = []
            for r in failures:
                meta = r.get("metadata", {})
                if meta.get("failure_type"):
                    avoid.append(meta["failure_type"])

            if known_issues or outcomes:
                result: dict = {}
                if known_issues:
                    result["known_issues"] = known_issues
                    result["avoid"] = avoid
                if outcomes:
                    result["recent_outcomes"] = [
                        {
                            "document": o["document"],
                            "performance": o.get("metadata", {}).get("performance", ""),
                        }
                        for o in outcomes[:3]
                    ]
                return result
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentResult:
        data = task.input_data or {}

        # --- 1. Verifica storage ---
        if not self.storage.is_available():
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": f"Storage non disponibile: {self.storage.base_path}"},
                confidence=0.0,
                missing_data=["Storage non disponibile"],
            )

        # --- 2. Validazione input (Intervento 19) ---
        normalized_input, error = await self._validate_and_normalize_input(data)
        if error:
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": error},
                confidence=0.0,
                missing_data=[error],
            )

        niche = normalized_input["niche"]
        product_type = normalized_input["product_type"]
        num_variants = normalized_input["num_variants"]
        color_schemes = normalized_input["color_schemes"]
        size = normalized_input.get("size", "A4")

        # --- 3. Estrai research context (Intervento 17) ---
        research_context = self._extract_research_context(normalized_input)

        # --- Route per product_type non-PDF ---
        if product_type == "digital_art_png":
            return await self._run_digital_art(task, normalized_input, research_context)
        if product_type == "svg_bundle":
            return await self._run_svg_bundle(task, normalized_input, research_context)

        # --- 4. Lookup failure patterns da ChromaDB (Intervento 16) ---
        failure_patterns = await self._lookup_failure_patterns(niche, product_type)

        # --- 5. Seleziona template via LLM (Intervento 5) ---
        template = normalized_input.get("template") or await _select_template_llm(
            niche, product_type, research_context, self.client,
        )

        # --- 6. Seleziona preset 2-stage (Intervento 3) ---
        preset = await _select_preset(niche, template, research_context, self.client)

        # --- 7. Decide dated/undated (Intervento 7) ---
        include_dates = await _should_include_dates(template, niche, self.client)

        # --- 8. Cover title con keyword primaria (Intervento 6) ---
        cover_title = _get_cover_title(niche, template, research_context)

        await self._log_step(
            "thinking",
            f"Generazione {num_variants} varianti '{template}' preset={preset} per niche '{niche}'",
            input_data={
                "niche": niche, "template": template, "preset": preset,
                "include_dates": include_dates, "cover_title": cover_title,
            },
        )

        # --- 9. Update production_queue → in_progress ---
        pq_task_id: str | None = normalized_input.get("production_queue_task_id")
        if pq_task_id:
            await self.memory.update_production_queue_status(pq_task_id, "in_progress")

        # --- 10. Prepara output directory ---
        output_dir = self.storage.base_path / "pending" / task.task_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # --- 11. Genera varianti in parallelo con Semaphore(3) ---
        semaphore = asyncio.Semaphore(3)
        generated_variants: list[dict] = []
        validation_results: list[dict] = []
        all_thumbnails: list[dict] = []
        slug = _niche_slug(niche)

        # Mappa template → generator di file_gen.py
        gen_template = _TEMPLATE_TO_GEN.get(template, "weekly_planner")

        async def generate_single_variant(idx: int, color_scheme: str) -> dict | None:
            async with semaphore:
                # Colori niche-aware (Intervento 4)
                colors = await _resolve_color_scheme_niche_aware(
                    color_scheme, niche, preset, self.client,
                )

                variant_dir = output_dir / f"variant_{idx}"
                variant_dir.mkdir(exist_ok=True)
                pdf_path = variant_dir / f"{slug}_{template}_{idx}.pdf"

                try:
                    # Bridge: converti hex colors → ColorScheme per PDFGenerator
                    scheme = _colors_to_scheme(f"{preset}_{idx}", colors)

                    # Genera PDF via file_gen.py
                    preset_data = STYLE_PRESETS[preset]
                    font_heading_name = preset_data["font_heading"]
                    font_body_name = preset_data["font_primary"]

                    # Usa font custom se disponibili, altrimenti fallback
                    font_heading = (
                        f"{font_heading_name}-Bold"
                        if _REGISTERED_FONTS.get(font_heading_name)
                        else "Helvetica-Bold"
                    )
                    font_body = (
                        font_heading_name
                        if _REGISTERED_FONTS.get(font_heading_name)
                        else "Helvetica"
                    )
                    font_light = (
                        font_body_name
                        if _REGISTERED_FONTS.get(font_body_name)
                        else "Helvetica-Oblique"
                    )

                    pdf_metadata = {
                        "title": cover_title,
                        "subject": f"Printable {template.replace('_', ' ').title()} - {niche}",
                        "keywords": f"{niche}, printable, {template.replace('_', ' ')}, digital download, Etsy",
                    }

                    await self._call_tool(
                        "pdf_generator",
                        f"generate_{gen_template}",
                        {"scheme": scheme.name, "size": size, "output": str(pdf_path)},
                        self._pdf_gen.generate,
                        gen_template,
                        scheme,
                        size,
                        pdf_path,
                        font_heading=font_heading,
                        font_body=font_body,
                        font_light=font_light,
                        cover_title=cover_title,
                        add_instructions=True,
                        metadata=pdf_metadata,
                    )

                    # Conta pagine dopo generazione
                    pages_count = _count_pdf_pages(pdf_path)

                    # Validazione PDF (Intervento 14)
                    validation = await _validate_pdf(pdf_path, template, expected_pages=pages_count)
                    validation_results.append(validation)

                    if not validation["valid"]:
                        logger.warning("PDF validation failed: %s — %s", pdf_path, validation["issues"])

                    # Genera thumbnails Playwright (Intervento 12)
                    preset_data = STYLE_PRESETS[preset]
                    thumbnails = await self._call_tool(
                        "playwright",
                        "generate_thumbnails",
                        {"pdf": str(pdf_path), "preset": preset},
                        generate_pdf_thumbnail,
                        pdf_path=pdf_path,
                        output_dir=variant_dir,
                        preset=preset,
                        preset_data=preset_data,
                        niche=niche,
                        colors=colors,
                    )
                    all_thumbnails.append(thumbnails)

                    return {
                        "pdf_path": str(pdf_path),
                        "variant_index": idx,
                        "color_scheme": color_scheme,
                        "preset": preset,
                        "template": template,
                        "colors": colors,
                        "include_dates": include_dates,
                        "thumbnails": {
                            k: str(v) for k, v in thumbnails.items()
                            if v and k != "errors"
                        },
                        "validation": validation,
                        "pages": pages_count,
                    }

                except Exception as e:
                    logger.warning("Errore generazione variante %d: %s", idx, e)
                    await self._log_step(
                        "tool_call",
                        f"variant_{idx} error: {e}",
                        input_data={"variant": idx, "color_scheme": color_scheme},
                        output_data={"error": str(e)},
                    )
                    return None

        tasks_coroutines = [
            generate_single_variant(i, color_schemes[i % len(color_schemes)])
            for i in range(num_variants)
        ]
        results = await asyncio.gather(*tasks_coroutines, return_exceptions=True)

        for r in results:
            if isinstance(r, dict):
                generated_variants.append(r)

        if not generated_variants:
            if pq_task_id:
                await self.memory.update_production_queue_status(pq_task_id, "failed")
            return AgentResult(
                task_id=task.task_id,
                agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "All variants failed to generate"},
                confidence=0.0,
                missing_data=["No variants generated successfully"],
            )

        # --- 12. Confidence scoring (Intervento 15) ---
        confidence, missing_data = _calculate_design_confidence(
            variants_generated=len(generated_variants),
            variants_requested=num_variants,
            thumbnails=all_thumbnails,
            validation_results=validation_results,
            fonts_available=_REGISTERED_FONTS,
            research_available=research_context is not None,
        )

        # --- 13. Update production_queue → completed ---
        if pq_task_id:
            file_paths = [v["pdf_path"] for v in generated_variants]
            await self.memory.update_production_queue_status(
                pq_task_id, "completed", file_paths=file_paths,
            )

        # --- 14. Log step finale ---
        total_size = sum(
            Path(v["pdf_path"]).stat().st_size
            for v in generated_variants
            if Path(v["pdf_path"]).exists()
        )
        summary = (
            f"Generati {len(generated_variants)}/{num_variants} varianti PDF "
            f"({total_size / 1024:.0f} KB), preset={preset}, confidence={confidence}"
        )
        await self._log_step(
            "file_operation",
            summary,
            input_data={"template": template, "size": size, "preset": preset},
            output_data={
                "variants_generated": len(generated_variants),
                "confidence": confidence,
            },
        )

        return AgentResult(
            task_id=task.task_id,
            agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "variants": generated_variants,
                "preset": preset,
                "template": template,
                "include_dates": include_dates,
                "cover_title": cover_title,
                "niche": niche,
                "product_type": product_type,
                "failure_patterns_checked": failure_patterns is not None,
            },
            confidence=confidence,
            missing_data=missing_data,
        )

    # ------------------------------------------------------------------
    # Digital Art PNG pipeline
    # ------------------------------------------------------------------

    async def _run_digital_art(
        self,
        task: AgentTask,
        normalized_input: dict,
        research_context: dict | None,
    ) -> AgentResult:
        """Genera Digital Art PNG via ImageGenerator (Flux Pro / placeholder)."""
        niche = normalized_input["niche"]
        num_variants = normalized_input["num_variants"]
        pq_task_id: str | None = normalized_input.get("production_queue_task_id")

        if pq_task_id:
            await self.memory.update_production_queue_status(pq_task_id, "in_progress")

        output_dir = self.storage.base_path / "pending" / task.task_id
        output_dir.mkdir(parents=True, exist_ok=True)

        slug = _niche_slug(niche)
        art_type = normalized_input.get("art_type", "wall_art")
        style_preset = normalized_input.get("style_preset", "minimal")

        await self._log_step(
            "thinking",
            f"Generazione {num_variants} Digital Art PNG per niche '{niche}' "
            f"(art_type={art_type}, api={'flux' if self._image_gen.is_available else 'placeholder'})",
        )

        generated: list[dict] = []
        color_schemes = normalized_input.get("color_schemes", ["neutral", "warm"])

        for i in range(num_variants):
            brief = {
                "niche": niche,
                "art_type": art_type,
                "style_preset": style_preset,
                "colors": normalized_input.get("colors", {}),
                "quote": normalized_input.get("quote", ""),
            }
            out_path = output_dir / f"{slug}_art_{i + 1}.png"
            try:
                path = await self._image_gen.generate_digital_art(brief, out_path, mock_mode=self._get_mock_mode())
                generated.append({
                    "file_path": str(path),
                    "variant_index": i,
                    "art_type": art_type,
                    "file_size_kb": round(path.stat().st_size / 1024, 1),
                    "used_replicate": self._image_gen.is_available,
                })
            except Exception as e:
                logger.warning("Errore Digital Art variante %d: %s", i, e)

        if not generated:
            if pq_task_id:
                await self.memory.update_production_queue_status(pq_task_id, "failed")
            return AgentResult(
                task_id=task.task_id, agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": "All digital art variants failed"},
                confidence=0.0,
                missing_data=["No digital art generated"],
            )

        confidence = len(generated) / num_variants
        if not self._image_gen.is_available:
            confidence *= 0.6  # placeholder = fiducia ridotta

        if pq_task_id:
            file_paths = [v["file_path"] for v in generated]
            await self.memory.update_production_queue_status(
                pq_task_id, "completed", file_paths=file_paths,
            )

        await self._log_step(
            "file_operation",
            f"Digital Art: {len(generated)}/{num_variants} PNG generati, confidence={confidence:.2f}",
        )

        return AgentResult(
            task_id=task.task_id, agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "variants": generated,
                "niche": niche,
                "product_type": "digital_art_png",
                "art_type": art_type,
                "used_replicate": self._image_gen.is_available,
            },
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # SVG Bundle pipeline
    # ------------------------------------------------------------------

    async def _run_svg_bundle(
        self,
        task: AgentTask,
        normalized_input: dict,
        research_context: dict | None,
    ) -> AgentResult:
        """Genera SVG bundle via SVGGenerator."""
        niche = normalized_input["niche"]
        pq_task_id: str | None = normalized_input.get("production_queue_task_id")

        if pq_task_id:
            await self.memory.update_production_queue_status(pq_task_id, "in_progress")

        output_dir = self.storage.base_path / "pending" / task.task_id
        output_dir.mkdir(parents=True, exist_ok=True)

        svg_type = normalized_input.get("svg_type", "geometric")
        brief = {
            "niche": niche,
            "svg_type": svg_type,
            "complexity": normalized_input.get("complexity", 2),
            "quote": normalized_input.get("quote", ""),
            "color_variants": normalized_input.get("color_variants", []),
        }

        await self._log_step(
            "thinking",
            f"Generazione SVG bundle '{svg_type}' per niche '{niche}'",
        )

        try:
            paths = await self._svg_gen.generate_bundle(brief, output_dir)
        except Exception as e:
            logger.error("SVG bundle generation failed: %s", e)
            if pq_task_id:
                await self.memory.update_production_queue_status(pq_task_id, "failed")
            return AgentResult(
                task_id=task.task_id, agent_name=self.name,
                status=TaskStatus.FAILED,
                output_data={"error": f"SVG generation failed: {e}"},
                confidence=0.0,
                missing_data=["SVG generation error"],
            )

        file_paths_str = [str(p) for p in paths]

        if pq_task_id:
            await self.memory.update_production_queue_status(
                pq_task_id, "completed", file_paths=file_paths_str,
            )

        await self._log_step(
            "file_operation",
            f"SVG bundle: {len(paths)} file generati (type={svg_type})",
        )

        return AgentResult(
            task_id=task.task_id, agent_name=self.name,
            status=TaskStatus.COMPLETED,
            output_data={
                "svg_files": file_paths_str,
                "niche": niche,
                "product_type": "svg_bundle",
                "svg_type": svg_type,
                "num_files": len(paths),
            },
            confidence=1.0,
        )
