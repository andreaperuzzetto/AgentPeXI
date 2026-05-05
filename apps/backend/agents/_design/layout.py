"""DesignAgent — decorative layout elements and PDF metadata."""
from __future__ import annotations

from typing import Any

from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm

from apps.backend.agents._design.presets import STYLE_PRESETS, _REGISTERED_FONTS


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
