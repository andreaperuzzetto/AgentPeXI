"""Budget Sheet mixin for PDFGenerator."""
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


class _BudgetMixin:

    def _generate_budget_sheet(
        self,
        scheme: ColorScheme,
        size: str,
        output_path,
        font_heading: str = "Helvetica-Bold",
        font_body: str = "Helvetica",
        font_light: str = "Helvetica-Oblique",
        cover_title: str = "Monthly Budget Planner",
        add_instructions: bool = False,
        metadata: dict | None = None,
        **_,
    ):
        w, h = SIZES.get(size, SIZES["A4"])
        c = canvas.Canvas(str(output_path), pagesize=(w, h))

        if metadata:
            c.setTitle(metadata.get("title", cover_title))
            c.setAuthor("AgentPeXI Digital Products")
            c.setSubject(metadata.get("subject", "Printable Budget Sheet"))
            c.setKeywords(metadata.get("keywords", "printable, budget, digital download"))
            c.setCreator("AgentPeXI v1.0")

        fonts = {"heading": font_heading, "body": font_body, "light": font_light}

        _draw_cover(c, scheme, w, h, cover_title, fonts)
        self._draw_budget_table(c, scheme, w, h, "Income Tracker", 10, fonts)
        self._draw_expenses_page(c, scheme, w, h, fonts)
        self._draw_budget_summary(c, scheme, w, h, fonts)

        if add_instructions:
            _draw_instructions_page(c, scheme, w, h, fonts)

        c.save()
        return output_path

    def _draw_budget_table(
        self,
        c: canvas.Canvas,
        scheme: ColorScheme,
        w: float,
        h: float,
        title: str,
        rows: int,
        fonts=None,
    ) -> None:
        if fonts is None:
            fonts = FONTS
        _fill_page(c, scheme.background, w, h)

        c.setFillColor(_rgb(scheme.accent))
        c.setFont(fonts["heading"], 18)
        c.drawCentredString(w / 2, h - MARGIN - 30, title)

        cols = ["Source", "Expected", "Actual", "Difference"]
        col_w = (w - 2 * MARGIN) / len(cols)
        table_top = h - MARGIN - 60
        row_h = 26

        c.setFillColor(_rgb(scheme.primary))
        c.rect(MARGIN, table_top - row_h, w - 2 * MARGIN, row_h, stroke=0, fill=1)
        c.setFillColor(_rgb(scheme.background))
        c.setFont(fonts["heading"], 10)
        for i, col_name in enumerate(cols):
            c.drawCentredString(MARGIN + i * col_w + col_w / 2, table_top - row_h + 8, col_name)

        for r in range(rows):
            ry = table_top - (r + 2) * row_h
            fill = scheme.background if r % 2 == 0 else scheme.secondary
            c.setFillColor(_rgb(fill))
            c.rect(MARGIN, ry, w - 2 * MARGIN, row_h, stroke=0, fill=1)
            c.setStrokeColor(_rgb(scheme.secondary))
            c.setLineWidth(0.3)
            c.line(MARGIN, ry, w - MARGIN, ry)

        total_y = table_top - (rows + 2) * row_h
        c.setFillColor(_rgb(scheme.primary))
        c.rect(MARGIN, total_y, w - 2 * MARGIN, row_h, stroke=0, fill=1)
        c.setFillColor(_rgb(scheme.background))
        c.setFont(fonts["heading"], 10)
        c.drawString(MARGIN + 10, total_y + 8, "TOTAL")

        c.showPage()

    def _draw_expenses_page(
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
        c.drawCentredString(w / 2, h - MARGIN - 30, "Expenses Tracker")

        sections = ["Housing", "Food", "Transport", "Entertainment", "Other"]
        cols = ["Item", "Expected", "Actual", "Difference"]
        col_w = (w - 2 * MARGIN) / len(cols)

        y = h - MARGIN - 60
        row_h = 22
        rows_per_section = 3

        for section in sections:
            if y < MARGIN + 60:
                c.showPage()
                _fill_page(c, scheme.background, w, h)
                y = h - MARGIN - 30

            c.setFillColor(_rgb(scheme.primary))
            c.setFont(fonts["heading"], 11)
            c.drawString(MARGIN, y, section)
            y -= row_h

            c.setFillColor(_rgb(scheme.secondary))
            c.rect(MARGIN, y - row_h, w - 2 * MARGIN, row_h, stroke=0, fill=1)
            c.setFillColor(_rgb(scheme.accent))
            c.setFont(fonts["body"], 8)
            for i, col_name in enumerate(cols):
                c.drawCentredString(MARGIN + i * col_w + col_w / 2, y - row_h + 6, col_name)
            y -= row_h

            for r in range(rows_per_section):
                y -= row_h
                c.setStrokeColor(_rgb(scheme.secondary))
                c.setLineWidth(0.3)
                c.line(MARGIN, y, w - MARGIN, y)

            y -= 10

        c.showPage()

    def _draw_budget_summary(
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
        c.drawCentredString(w / 2, h - MARGIN - 30, "Summary")

        cx = w / 2
        cy = h / 2 + 80
        r_outer = 80
        r_inner = 50
        c.setStrokeColor(_rgb(scheme.primary))
        c.setFillColor(_rgb(scheme.primary))
        c.setLineWidth(2)
        c.circle(cx, cy, r_outer, stroke=1, fill=0)
        c.setFillColor(_rgb(scheme.background))
        c.circle(cx, cy, r_inner, stroke=0, fill=1)
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(fonts["heading"], 14)
        c.drawCentredString(cx, cy - 5, "Budget")

        bar_y = cy - r_outer - 60
        bar_w = w - 2 * MARGIN - 60
        bar_h = 20
        bx = MARGIN + 30
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(fonts["heading"], 11)
        c.drawString(bx, bar_y + 28, "Savings Goal")
        c.setFillColor(_rgb(scheme.secondary))
        c.roundRect(bx, bar_y, bar_w, bar_h, 4, stroke=0, fill=1)
        c.setFillColor(_rgb(scheme.primary))
        c.roundRect(bx, bar_y, bar_w * 0.6, bar_h, 4, stroke=0, fill=1)

        notes_y = bar_y - 60
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(fonts["heading"], 11)
        c.drawString(MARGIN, notes_y, "Notes")
        _draw_lines(
            c, MARGIN, notes_y - 20, w - 2 * MARGIN, 5, 22,
            scheme.secondary, dotted=True,
        )

        c.showPage()
