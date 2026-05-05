"""Shared PDF helpers and ColorScheme dataclass for PDFGenerator."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, A5, letter
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

# ---------------------------------------------------------------------------
# ColorScheme
# ---------------------------------------------------------------------------


@dataclass
class ColorScheme:
    name: str  # "sage", "blush", "slate", "terracotta", "midnight"
    primary: tuple[int, int, int]  # RGB 0-255
    secondary: tuple[int, int, int]  # sfondo pagina interna
    accent: tuple[int, int, int]  # testi, bordi
    background: tuple[int, int, int]  # sfondo cover


DEFAULT_SCHEMES = [
    ColorScheme("sage", (135, 168, 120), (245, 240, 232), (61, 61, 61), (255, 255, 255)),
    ColorScheme("blush", (232, 180, 184), (255, 248, 248), (74, 74, 74), (255, 255, 255)),
    ColorScheme("slate", (108, 132, 153), (242, 245, 248), (40, 40, 60), (255, 255, 255)),
    ColorScheme("terracotta", (193, 110, 82), (251, 244, 240), (55, 35, 25), (255, 255, 255)),
    ColorScheme("midnight", (45, 52, 80), (235, 237, 245), (220, 225, 240), (255, 255, 255)),
]

SCHEME_BY_NAME: dict[str, ColorScheme] = {s.name: s for s in DEFAULT_SCHEMES}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MARGIN = 36  # 0.5 pollice

SIZES: dict[str, tuple[float, float]] = {
    "A4": A4,
    "Letter": letter,
    "A5": A5,
}

FONTS = {
    "heading": "Helvetica-Bold",
    "body": "Helvetica",
    "light": "Helvetica-Oblique",
}

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _rgb(t: tuple[int, int, int]) -> colors.Color:
    return colors.Color(t[0] / 255.0, t[1] / 255.0, t[2] / 255.0)


def _fill_page(c: canvas.Canvas, color: tuple[int, int, int], w: float, h: float) -> None:
    c.setFillColor(_rgb(color))
    c.rect(0, 0, w, h, stroke=0, fill=1)


def _draw_lines(
    c: canvas.Canvas,
    x: float,
    y_start: float,
    width: float,
    count: int,
    spacing: float,
    color: tuple[int, int, int],
    dotted: bool = False,
) -> float:
    """Disegna linee orizzontali; ritorna la y finale."""
    c.setStrokeColor(_rgb(color))
    c.setLineWidth(0.4)
    if dotted:
        c.setDash(2, 4)
    else:
        c.setDash()
    y = y_start
    for _ in range(count):
        c.line(x, y, x + width, y)
        y -= spacing
    c.setDash()  # reset
    return y


# ---------------------------------------------------------------------------
# Instructions page
# ---------------------------------------------------------------------------


def _draw_instructions_page(c, scheme, w, h, fonts=None):
    """Pagina istruzioni standardizzata — ultima pagina di ogni PDF."""
    if fonts is None:
        fonts = FONTS
    _fill_page(c, scheme.background, w, h)
    margin = 20 * mm

    c.setFillColor(_rgb(scheme.accent))
    c.setFont(fonts["heading"], 18)
    c.drawCentredString(w / 2, h - margin - 10 * mm, "Thank You for Your Purchase!")

    c.setStrokeColor(_rgb(scheme.primary))
    c.setLineWidth(1)
    c.line(margin, h - margin - 18 * mm, w - margin, h - margin - 18 * mm)

    sections = [
        ("HOW TO USE:", [
            "\u2022 Print at home or at a local print shop",
            "\u2022 Recommended paper: 90gsm or heavier for best results",
            "\u2022 Print size: A4 / US Letter (as specified in product title)",
            "\u2022 For best quality: print at 300 DPI or higher",
        ]),
        ("TIPS:", [
            "\u2022 Use a PDF viewer (Adobe Acrobat) for best print quality",
            "\u2022 For digital use: open in GoodNotes, Notability, or Noteshelf",
            "\u2022 Laminate for durability if using physically",
        ]),
        ("LICENSE:", [
            "\u2022 Personal use only \u2014 not for resale or redistribution",
            "\u2022 You may print unlimited copies for personal use",
            "\u2022 Commercial license available \u2014 contact us",
        ]),
    ]

    y = h - margin - 30 * mm
    for section_title, items in sections:
        c.setFont(fonts["heading"], 10)
        c.setFillColor(_rgb(scheme.primary))
        c.drawString(margin, y, section_title)
        y -= 6 * mm
        c.setFont(fonts["body"], 10)
        c.setFillColor(_rgb(scheme.accent))
        for item in items:
            c.drawString(margin + 3 * mm, y, item)
            y -= 5.5 * mm
        y -= 5 * mm

    c.setFont(fonts["body"], 8)
    c.setFillColor(_rgb(scheme.primary))
    c.drawCentredString(w / 2, margin, "Questions? Visit our Etsy shop for support")
    c.showPage()


# ---------------------------------------------------------------------------
# Cover page
# ---------------------------------------------------------------------------


def _draw_cover(
    c: canvas.Canvas,
    scheme: ColorScheme,
    w: float,
    h: float,
    title: str,
    fonts=None,
) -> None:
    if fonts is None:
        fonts = FONTS
    _fill_page(c, scheme.primary, w, h)
    c.setFillColor(_rgb(scheme.background))
    c.setFont(fonts["heading"], 36)
    c.drawCentredString(w / 2, h / 2 + 30, title)
    c.setFont(fonts["body"], 16)
    c.drawCentredString(w / 2, h / 2 - 10, str(datetime.now().year))
    c.setFont(fonts["light"], 10)
    c.setFillColor(colors.Color(1, 1, 1, 0.6))
    c.drawCentredString(w / 2, MARGIN + 10, scheme.name.capitalize())
    c.showPage()
