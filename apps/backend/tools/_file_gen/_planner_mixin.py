"""Weekly Planner mixin for PDFGenerator."""
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


class _PlannerMixin:

    def _generate_weekly_planner(
        self,
        scheme: ColorScheme,
        size: str,
        output_path,
        weeks: int = 10,
        font_heading: str = "Helvetica-Bold",
        font_body: str = "Helvetica",
        font_light: str = "Helvetica-Oblique",
        cover_title: str = "Weekly Planner",
        add_instructions: bool = False,
        metadata: dict | None = None,
        **_,
    ):
        w, h = SIZES.get(size, SIZES["A4"])
        c = canvas.Canvas(str(output_path), pagesize=(w, h))

        if metadata:
            c.setTitle(metadata.get("title", cover_title))
            c.setAuthor("AgentPeXI Digital Products")
            c.setSubject(metadata.get("subject", "Printable Planner"))
            c.setKeywords(metadata.get("keywords", "printable, planner, digital download"))
            c.setCreator("AgentPeXI v1.0")

        fonts = {"heading": font_heading, "body": font_body, "light": font_light}

        _draw_cover(c, scheme, w, h, cover_title, fonts)
        self._draw_goals_page(c, scheme, w, h, fonts)

        today = datetime.now()
        monday = today - timedelta(days=today.weekday())

        for week_num in range(1, weeks + 1):
            week_start = monday + timedelta(weeks=week_num - 1)
            week_end = week_start + timedelta(days=6)
            self._draw_weekly_spread(c, scheme, w, h, week_num, week_start, week_end, fonts)

        if add_instructions:
            _draw_instructions_page(c, scheme, w, h, fonts)

        c.save()
        return output_path

    def _draw_goals_page(
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
        c.setFont(fonts["heading"], 22)
        c.drawCentredString(w / 2, h - MARGIN - 40, "My Goals This Period")

        box_w = (w - 3 * MARGIN) / 2
        box_h = (h - 3 * MARGIN - 80) / 3
        gap = MARGIN

        for row in range(3):
            for col in range(2):
                x = MARGIN + col * (box_w + gap)
                y = h - MARGIN - 70 - row * (box_h + 12)

                c.setFillColor(_rgb(scheme.secondary))
                c.setStrokeColor(_rgb(scheme.primary))
                c.setLineWidth(0.8)
                c.roundRect(x, y - box_h, box_w, box_h, 6, stroke=1, fill=1)

                c.setFillColor(_rgb(scheme.primary))
                c.setFont(fonts["heading"], 14)
                c.drawString(x + 10, y - 22, f"Goal {row * 2 + col + 1}")

                _draw_lines(
                    c, x + 10, y - 44, box_w - 20, 4, 20,
                    scheme.secondary,
                )

        c.showPage()

    def _draw_weekly_spread(
        self,
        c: canvas.Canvas,
        scheme: ColorScheme,
        w: float,
        h: float,
        week_num: int,
        week_start: datetime,
        week_end: datetime,
        fonts=None,
    ) -> None:
        if fonts is None:
            fonts = FONTS
        _fill_page(c, scheme.background, w, h)

        header_h = 50
        c.setFillColor(_rgb(scheme.primary))
        c.rect(0, h - header_h, w, header_h, stroke=0, fill=1)

        label = (
            f"Week {week_num}  ·  "
            f"{week_start.strftime('%d %b')} – {week_end.strftime('%d %b')}"
        )
        c.setFillColor(_rgb(scheme.background))
        c.setFont(fonts["heading"], 16)
        c.drawCentredString(w / 2, h - header_h + 18, label)

        area_top = h - header_h - 10
        notes_h = 70
        priority_w = 120
        day_area_bottom = MARGIN + notes_h + 10

        days = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
        col_count = 7
        total_day_w = w - 2 * MARGIN - priority_w - 10
        col_w = total_day_w / col_count
        day_col_h = area_top - day_area_bottom
        line_spacing = 18
        lines_per_day = min(6, int((day_col_h - 30) / line_spacing))

        for i, day_name in enumerate(days):
            x = MARGIN + i * col_w

            c.setFillColor(_rgb(scheme.secondary))
            c.rect(x, area_top - 24, col_w, 24, stroke=0, fill=1)
            c.setFillColor(_rgb(scheme.accent))
            c.setFont(fonts["heading"], 9)
            c.drawCentredString(x + col_w / 2, area_top - 18, day_name)

            _draw_lines(
                c, x + 4, area_top - 38, col_w - 8, lines_per_day, line_spacing,
                scheme.secondary,
            )

        px = w - MARGIN - priority_w
        py = area_top
        c.setFillColor(_rgb(scheme.secondary))
        c.roundRect(px, py - 160, priority_w, 160, 4, stroke=0, fill=1)
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(fonts["heading"], 9)
        c.drawString(px + 8, py - 18, "Priority of the week")

        for j in range(3):
            cy = py - 42 - j * 34
            c.setStrokeColor(_rgb(scheme.primary))
            c.setLineWidth(0.6)
            c.circle(px + 16, cy, 5, stroke=1, fill=0)
            _draw_lines(c, px + 28, cy - 2, priority_w - 40, 1, 0, scheme.secondary)

        c.setFillColor(_rgb(scheme.secondary))
        c.roundRect(MARGIN, MARGIN, w - 2 * MARGIN, notes_h, 4, stroke=0, fill=1)
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(fonts["heading"], 9)
        c.drawString(MARGIN + 8, MARGIN + notes_h - 16, "Notes")
        _draw_lines(
            c, MARGIN + 8, MARGIN + notes_h - 30, w - 2 * MARGIN - 16, 2, 18,
            scheme.secondary, dotted=True,
        )

        c.showPage()
