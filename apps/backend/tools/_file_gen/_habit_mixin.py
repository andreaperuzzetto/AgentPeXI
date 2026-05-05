"""Habit Tracker mixin for PDFGenerator."""
from __future__ import annotations

from reportlab.pdfgen import canvas

from apps.backend.tools._file_gen._pdf_helpers import (
    ColorScheme,
    FONTS,
    MARGIN,
    SIZES,
    _draw_cover,
    _draw_instructions_page,
    _draw_lines,
    _fill_page,
    _rgb,
)


class _HabitMixin:

    def _generate_habit_tracker(
        self,
        scheme: ColorScheme,
        size: str,
        output_path,
        habits: int = 10,
        days: int = 31,
        font_heading: str = "Helvetica-Bold",
        font_body: str = "Helvetica",
        font_light: str = "Helvetica-Oblique",
        cover_title: str = "Habit Tracker",
        add_instructions: bool = False,
        metadata: dict | None = None,
        **_,
    ):
        w, h = SIZES.get(size, SIZES["A4"])
        c = canvas.Canvas(str(output_path), pagesize=(w, h))

        if metadata:
            c.setTitle(metadata.get("title", cover_title))
            c.setAuthor("AgentPeXI Digital Products")
            c.setSubject(metadata.get("subject", "Printable Habit Tracker"))
            c.setKeywords(metadata.get("keywords", "printable, habit tracker, digital download"))
            c.setCreator("AgentPeXI v1.0")

        fonts = {"heading": font_heading, "body": font_body, "light": font_light}

        _draw_cover(c, scheme, w, h, cover_title, fonts)
        self._draw_habit_grid(c, scheme, w, h, habits, days, fonts)
        self._draw_reflection_page(c, scheme, w, h, fonts)

        if add_instructions:
            _draw_instructions_page(c, scheme, w, h, fonts)

        c.save()
        return output_path

    def _draw_habit_grid(
        self,
        c: canvas.Canvas,
        scheme: ColorScheme,
        w: float,
        h: float,
        habits: int,
        days: int,
        fonts=None,
    ) -> None:
        if fonts is None:
            fonts = FONTS
        _fill_page(c, scheme.background, w, h)

        c.setFillColor(_rgb(scheme.accent))
        c.setFont(fonts["heading"], 18)
        c.drawCentredString(w / 2, h - MARGIN - 30, "Monthly Habit Tracker")

        label_w = 100
        cell_size = min(16, (w - 2 * MARGIN - label_w) / days)
        grid_top = h - MARGIN - 60

        for d in range(1, days + 1):
            cx = MARGIN + label_w + (d - 1) * cell_size + cell_size / 2
            c.setFillColor(_rgb(scheme.primary))
            c.setFont(fonts["body"], 7)
            c.drawCentredString(cx, grid_top + 4, str(d))

        for row in range(habits):
            ry = grid_top - row * (cell_size + 4)

            c.setFillColor(_rgb(scheme.accent))
            c.setFont(fonts["body"], 8)
            c.drawString(MARGIN, ry - cell_size + 4, f"Habit {row + 1}")

            for d in range(days):
                cx = MARGIN + label_w + d * cell_size
                is_alt = d % 2 == 0
                fill = scheme.background if is_alt else scheme.secondary
                c.setFillColor(_rgb(fill))
                c.setStrokeColor(_rgb(scheme.secondary))
                c.setLineWidth(0.3)
                c.rect(cx, ry - cell_size, cell_size, cell_size, stroke=1, fill=1)

        c.showPage()

    def _draw_reflection_page(
        self,
        c: canvas.Canvas,
        scheme: ColorScheme,
        w: float,
        h: float,
        fonts=None,
    ) -> None:
        if fonts is None:
            fonts = FONTS
        _fill_page(c, scheme.background, w, h)
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(fonts["heading"], 18)
        c.drawCentredString(w / 2, h - MARGIN - 30, "Monthly Reflection")

        titles = ["What worked", "What to improve", "Streak record", "Next month goal"]
        box_h = (h - 3 * MARGIN - 60) / 4

        for i, title in enumerate(titles):
            y = h - MARGIN - 60 - i * (box_h + 8)
            c.setFillColor(_rgb(scheme.secondary))
            c.roundRect(MARGIN, y - box_h, w - 2 * MARGIN, box_h, 6, stroke=0, fill=1)
            c.setFillColor(_rgb(scheme.primary))
            c.setFont(fonts["heading"], 12)
            c.drawString(MARGIN + 10, y - 20, title)
            _draw_lines(
                c, MARGIN + 10, y - 38, w - 2 * MARGIN - 20, 3, 22,
                scheme.secondary,
            )

        c.showPage()
