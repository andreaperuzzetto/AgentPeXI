"""Daily Journal mixin for PDFGenerator."""
from __future__ import annotations

from datetime import datetime, timedelta

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


class _JournalMixin:

    def _generate_daily_journal(
        self,
        scheme: ColorScheme,
        size: str,
        output_path,
        days: int = 30,
        font_heading: str = "Helvetica-Bold",
        font_body: str = "Helvetica",
        font_light: str = "Helvetica-Oblique",
        cover_title: str = "Daily Journal",
        add_instructions: bool = False,
        metadata: dict | None = None,
        **_,
    ):
        w, h = SIZES.get(size, SIZES["A4"])
        c = canvas.Canvas(str(output_path), pagesize=(w, h))

        if metadata:
            c.setTitle(metadata.get("title", cover_title))
            c.setAuthor("AgentPeXI Digital Products")
            c.setSubject(metadata.get("subject", "Printable Daily Journal"))
            c.setKeywords(metadata.get("keywords", "printable, journal, digital download"))
            c.setCreator("AgentPeXI v1.0")

        fonts = {"heading": font_heading, "body": font_body, "light": font_light}

        _draw_cover(c, scheme, w, h, cover_title, fonts)
        today = datetime.now()
        for page_idx in range(0, days, 2):
            self._draw_journal_page(
                c, scheme, w, h,
                day1=today + timedelta(days=page_idx),
                day2=today + timedelta(days=page_idx + 1) if page_idx + 1 < days else None,
                fonts=fonts,
            )

        if add_instructions:
            _draw_instructions_page(c, scheme, w, h, fonts)

        c.save()
        return output_path

    def _draw_journal_page(
        self,
        c: canvas.Canvas,
        scheme: ColorScheme,
        w: float,
        h: float,
        day1: datetime,
        day2: datetime | None,
        fonts=None,
    ) -> None:
        if fonts is None:
            fonts = FONTS
        _fill_page(c, scheme.background, w, h)

        half_h = (h - MARGIN) / 2

        self._draw_journal_day(c, scheme, w, MARGIN, h - MARGIN - 10, half_h - 20, day1, fonts)

        if day2:
            c.setStrokeColor(_rgb(scheme.secondary))
            c.setLineWidth(0.5)
            c.line(MARGIN, h / 2, w - MARGIN, h / 2)

            self._draw_journal_day(c, scheme, w, MARGIN, h / 2 - 10, half_h - 20, day2, fonts)

        c.showPage()

    def _draw_journal_day(
        self,
        c: canvas.Canvas,
        scheme: ColorScheme,
        page_w: float,
        x_start: float,
        y_top: float,
        available_h: float,
        day: datetime,
        fonts=None,
    ) -> None:
        if fonts is None:
            fonts = FONTS
        usable_w = page_w - 2 * MARGIN
        y = y_top

        c.setFillColor(_rgb(scheme.accent))
        c.setFont(fonts["heading"], 14)
        c.drawString(x_start, y, day.strftime("%A, %d %B %Y"))
        y -= 28

        c.setFillColor(_rgb(scheme.accent))
        c.setFont(fonts["body"], 9)
        c.drawString(x_start, y, "Mood:")
        for i in range(5):
            c.setStrokeColor(_rgb(scheme.primary))
            c.setLineWidth(0.8)
            c.circle(x_start + 45 + i * 22, y + 3, 7, stroke=1, fill=0)
        y -= 26

        c.setFillColor(_rgb(scheme.primary))
        c.setFont(fonts["heading"], 10)
        c.drawString(x_start, y, "Grateful for:")
        y -= 16
        for _ in range(3):
            _draw_lines(c, x_start + 10, y, usable_w - 10, 1, 0, scheme.secondary)
            y -= 18

        c.setFillColor(_rgb(scheme.secondary))
        c.roundRect(x_start, y - 30, usable_w, 30, 4, stroke=0, fill=1)
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(fonts["light"], 9)
        c.drawString(x_start + 6, y - 12, "Today's intention:")
        _draw_lines(c, x_start + 100, y - 12, usable_w - 106, 1, 0, scheme.secondary)
        y -= 40

        remaining = y - (y_top - available_h)
        line_count = max(1, int(remaining / 18))
        _draw_lines(c, x_start, y, usable_w, line_count, 18, scheme.secondary)
