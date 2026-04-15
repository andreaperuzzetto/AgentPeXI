"""PDFGenerator — genera Printable PDF con ReportLab + Pillow per AgentPeXI."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, A5, letter
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
# Helpers
# ---------------------------------------------------------------------------

MARGIN = 36  # 0.5 pollice


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
# Pagine di copertura
# ---------------------------------------------------------------------------


def _draw_cover(
    c: canvas.Canvas,
    scheme: ColorScheme,
    w: float,
    h: float,
    title: str,
) -> None:
    _fill_page(c, scheme.primary, w, h)
    c.setFillColor(_rgb(scheme.background))
    c.setFont("Helvetica-Bold", 36)
    c.drawCentredString(w / 2, h / 2 + 30, title)
    c.setFont("Helvetica", 16)
    c.drawCentredString(w / 2, h / 2 - 10, str(datetime.now().year))
    c.setFont("Helvetica-Oblique", 10)
    c.setFillColor(colors.Color(1, 1, 1, 0.6))
    c.drawCentredString(w / 2, MARGIN + 10, scheme.name.capitalize())
    c.showPage()


# ---------------------------------------------------------------------------
# SIZES mapping
# ---------------------------------------------------------------------------

SIZES: dict[str, tuple[float, float]] = {
    "A4": A4,          # (595.27, 841.89)
    "Letter": letter,  # (612, 792)
    "A5": A5,          # (419.53, 595.27)
}

FONTS = {
    "heading": "Helvetica-Bold",
    "body": "Helvetica",
    "light": "Helvetica-Oblique",
}


# ---------------------------------------------------------------------------
# PDFGenerator
# ---------------------------------------------------------------------------


class PDFGenerator:
    """Genera Printable PDF con ReportLab + Pillow."""

    # ------------------------------------------------------------------
    # Dispatcher async
    # ------------------------------------------------------------------

    async def generate(
        self,
        template: str,
        scheme: ColorScheme,
        size: str,
        output_path: Path,
        **kwargs,
    ) -> Path:
        """Dispatcher async. Chiama il metodo giusto, ritorna Path al file generato."""
        generators = {
            "weekly_planner": self._generate_weekly_planner,
            "habit_tracker": self._generate_habit_tracker,
            "budget_sheet": self._generate_budget_sheet,
            "daily_journal": self._generate_daily_journal,
        }
        fn = generators.get(template)
        if fn is None:
            raise ValueError(f"Template sconosciuto: {template!r}. Disponibili: {list(generators)}")

        # ReportLab è sync — eseguiamo in thread pool
        return await asyncio.to_thread(fn, scheme, size, output_path, **kwargs)

    # ------------------------------------------------------------------
    # Weekly Planner
    # ------------------------------------------------------------------

    def _generate_weekly_planner(
        self,
        scheme: ColorScheme,
        size: str,
        output_path: Path,
        weeks: int = 10,
        **_,
    ) -> Path:
        w, h = SIZES.get(size, SIZES["A4"])
        c = canvas.Canvas(str(output_path), pagesize=(w, h))

        # --- Cover ---
        _draw_cover(c, scheme, w, h, "Weekly Planner")

        # --- Goals page ---
        self._draw_goals_page(c, scheme, w, h)

        # --- Weekly spreads ---
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())

        for week_num in range(1, weeks + 1):
            week_start = monday + timedelta(weeks=week_num - 1)
            week_end = week_start + timedelta(days=6)
            self._draw_weekly_spread(c, scheme, w, h, week_num, week_start, week_end)

        c.save()
        return output_path

    def _draw_goals_page(
        self,
        c: canvas.Canvas,
        scheme: ColorScheme,
        w: float,
        h: float,
    ) -> None:
        _fill_page(c, scheme.background, w, h)

        # Titolo
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(FONTS["heading"], 22)
        c.drawCentredString(w / 2, h - MARGIN - 40, "My Goals This Period")

        # 6 box (2 colonne x 3 righe)
        box_w = (w - 3 * MARGIN) / 2
        box_h = (h - 3 * MARGIN - 80) / 3
        gap = MARGIN

        for row in range(3):
            for col in range(2):
                x = MARGIN + col * (box_w + gap)
                y = h - MARGIN - 70 - row * (box_h + 12)

                # Box sfondo
                c.setFillColor(_rgb(scheme.secondary))
                c.setStrokeColor(_rgb(scheme.primary))
                c.setLineWidth(0.8)
                c.roundRect(x, y - box_h, box_w, box_h, 6, stroke=1, fill=1)

                # Numero goal
                c.setFillColor(_rgb(scheme.primary))
                c.setFont(FONTS["heading"], 14)
                c.drawString(x + 10, y - 22, f"Goal {row * 2 + col + 1}")

                # Linee scrivibili
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
    ) -> None:
        _fill_page(c, scheme.background, w, h)

        # --- Header strip ---
        header_h = 50
        c.setFillColor(_rgb(scheme.primary))
        c.rect(0, h - header_h, w, header_h, stroke=0, fill=1)

        label = (
            f"Week {week_num}  ·  "
            f"{week_start.strftime('%d %b')} – {week_end.strftime('%d %b')}"
        )
        c.setFillColor(_rgb(scheme.background))
        c.setFont(FONTS["heading"], 16)
        c.drawCentredString(w / 2, h - header_h + 18, label)

        # --- Area utile sotto header ---
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

            # Header colonna
            c.setFillColor(_rgb(scheme.secondary))
            c.rect(x, area_top - 24, col_w, 24, stroke=0, fill=1)
            c.setFillColor(_rgb(scheme.accent))
            c.setFont(FONTS["heading"], 9)
            c.drawCentredString(x + col_w / 2, area_top - 18, day_name)

            # Linee scrivibili
            _draw_lines(
                c, x + 4, area_top - 38, col_w - 8, lines_per_day, line_spacing,
                scheme.secondary,
            )

        # --- Priority box (destra) ---
        px = w - MARGIN - priority_w
        py = area_top
        c.setFillColor(_rgb(scheme.secondary))
        c.roundRect(px, py - 160, priority_w, 160, 4, stroke=0, fill=1)
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(FONTS["heading"], 9)
        c.drawString(px + 8, py - 18, "Priority of the week")

        # 3 checkbox + linee
        for j in range(3):
            cy = py - 42 - j * 34
            c.setStrokeColor(_rgb(scheme.primary))
            c.setLineWidth(0.6)
            c.circle(px + 16, cy, 5, stroke=1, fill=0)
            _draw_lines(c, px + 28, cy - 2, priority_w - 40, 1, 0, scheme.secondary)

        # --- Notes box (footer) ---
        c.setFillColor(_rgb(scheme.secondary))
        c.roundRect(MARGIN, MARGIN, w - 2 * MARGIN, notes_h, 4, stroke=0, fill=1)
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(FONTS["heading"], 9)
        c.drawString(MARGIN + 8, MARGIN + notes_h - 16, "Notes")
        _draw_lines(
            c, MARGIN + 8, MARGIN + notes_h - 30, w - 2 * MARGIN - 16, 2, 18,
            scheme.secondary, dotted=True,
        )

        c.showPage()

    # ------------------------------------------------------------------
    # Habit Tracker
    # ------------------------------------------------------------------

    def _generate_habit_tracker(
        self,
        scheme: ColorScheme,
        size: str,
        output_path: Path,
        habits: int = 10,
        days: int = 31,
        **_,
    ) -> Path:
        w, h = SIZES.get(size, SIZES["A4"])
        c = canvas.Canvas(str(output_path), pagesize=(w, h))

        # --- Cover ---
        _draw_cover(c, scheme, w, h, "Habit Tracker")

        # --- Tracker grid page ---
        self._draw_habit_grid(c, scheme, w, h, habits, days)

        # --- Reflection page ---
        self._draw_reflection_page(c, scheme, w, h)

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
    ) -> None:
        _fill_page(c, scheme.background, w, h)

        c.setFillColor(_rgb(scheme.accent))
        c.setFont(FONTS["heading"], 18)
        c.drawCentredString(w / 2, h - MARGIN - 30, "Monthly Habit Tracker")

        label_w = 100
        cell_size = min(16, (w - 2 * MARGIN - label_w) / days)
        grid_w = cell_size * days
        grid_top = h - MARGIN - 60

        # Header giorni
        for d in range(1, days + 1):
            cx = MARGIN + label_w + (d - 1) * cell_size + cell_size / 2
            c.setFillColor(_rgb(scheme.primary))
            c.setFont(FONTS["body"], 7)
            c.drawCentredString(cx, grid_top + 4, str(d))

        # Righe habits
        for row in range(habits):
            ry = grid_top - row * (cell_size + 4)

            # Label
            c.setFillColor(_rgb(scheme.accent))
            c.setFont(FONTS["body"], 8)
            c.drawString(MARGIN, ry - cell_size + 4, f"Habit {row + 1}")

            # Celle
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
    ) -> None:
        _fill_page(c, scheme.background, w, h)
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(FONTS["heading"], 18)
        c.drawCentredString(w / 2, h - MARGIN - 30, "Monthly Reflection")

        titles = ["What worked", "What to improve", "Streak record", "Next month goal"]
        box_h = (h - 3 * MARGIN - 60) / 4

        for i, title in enumerate(titles):
            y = h - MARGIN - 60 - i * (box_h + 8)
            c.setFillColor(_rgb(scheme.secondary))
            c.roundRect(MARGIN, y - box_h, w - 2 * MARGIN, box_h, 6, stroke=0, fill=1)
            c.setFillColor(_rgb(scheme.primary))
            c.setFont(FONTS["heading"], 12)
            c.drawString(MARGIN + 10, y - 20, title)
            _draw_lines(
                c, MARGIN + 10, y - 38, w - 2 * MARGIN - 20, 3, 22,
                scheme.secondary,
            )

        c.showPage()

    # ------------------------------------------------------------------
    # Budget Sheet
    # ------------------------------------------------------------------

    def _generate_budget_sheet(
        self,
        scheme: ColorScheme,
        size: str,
        output_path: Path,
        **_,
    ) -> Path:
        w, h = SIZES.get(size, SIZES["A4"])
        c = canvas.Canvas(str(output_path), pagesize=(w, h))

        # --- Cover ---
        _draw_cover(c, scheme, w, h, "Monthly Budget Planner")

        # --- Income tracker ---
        self._draw_budget_table(c, scheme, w, h, "Income Tracker", 10)

        # --- Expenses tracker ---
        self._draw_expenses_page(c, scheme, w, h)

        # --- Summary ---
        self._draw_budget_summary(c, scheme, w, h)

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
    ) -> None:
        _fill_page(c, scheme.background, w, h)

        c.setFillColor(_rgb(scheme.accent))
        c.setFont(FONTS["heading"], 18)
        c.drawCentredString(w / 2, h - MARGIN - 30, title)

        cols = ["Source", "Expected", "Actual", "Difference"]
        col_w = (w - 2 * MARGIN) / len(cols)
        table_top = h - MARGIN - 60
        row_h = 26

        # Header
        c.setFillColor(_rgb(scheme.primary))
        c.rect(MARGIN, table_top - row_h, w - 2 * MARGIN, row_h, stroke=0, fill=1)
        c.setFillColor(_rgb(scheme.background))
        c.setFont(FONTS["heading"], 10)
        for i, col_name in enumerate(cols):
            c.drawCentredString(MARGIN + i * col_w + col_w / 2, table_top - row_h + 8, col_name)

        # Righe
        for r in range(rows):
            ry = table_top - (r + 2) * row_h
            fill = scheme.background if r % 2 == 0 else scheme.secondary
            c.setFillColor(_rgb(fill))
            c.rect(MARGIN, ry, w - 2 * MARGIN, row_h, stroke=0, fill=1)
            c.setStrokeColor(_rgb(scheme.secondary))
            c.setLineWidth(0.3)
            c.line(MARGIN, ry, w - MARGIN, ry)

        # Riga totale
        total_y = table_top - (rows + 2) * row_h
        c.setFillColor(_rgb(scheme.primary))
        c.rect(MARGIN, total_y, w - 2 * MARGIN, row_h, stroke=0, fill=1)
        c.setFillColor(_rgb(scheme.background))
        c.setFont(FONTS["heading"], 10)
        c.drawString(MARGIN + 10, total_y + 8, "TOTAL")

        c.showPage()

    def _draw_expenses_page(
        self,
        c: canvas.Canvas,
        scheme: ColorScheme,
        w: float,
        h: float,
    ) -> None:
        _fill_page(c, scheme.background, w, h)

        c.setFillColor(_rgb(scheme.accent))
        c.setFont(FONTS["heading"], 18)
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

            # Section header
            c.setFillColor(_rgb(scheme.primary))
            c.setFont(FONTS["heading"], 11)
            c.drawString(MARGIN, y, section)
            y -= row_h

            # Column header
            c.setFillColor(_rgb(scheme.secondary))
            c.rect(MARGIN, y - row_h, w - 2 * MARGIN, row_h, stroke=0, fill=1)
            c.setFillColor(_rgb(scheme.accent))
            c.setFont(FONTS["body"], 8)
            for i, col_name in enumerate(cols):
                c.drawCentredString(MARGIN + i * col_w + col_w / 2, y - row_h + 6, col_name)
            y -= row_h

            # Righe
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
    ) -> None:
        _fill_page(c, scheme.background, w, h)

        c.setFillColor(_rgb(scheme.accent))
        c.setFont(FONTS["heading"], 18)
        c.drawCentredString(w / 2, h - MARGIN - 30, "Summary")

        # Donut placeholder (cerchio)
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
        c.setFont(FONTS["heading"], 14)
        c.drawCentredString(cx, cy - 5, "Budget")

        # Savings goal bar
        bar_y = cy - r_outer - 60
        bar_w = w - 2 * MARGIN - 60
        bar_h = 20
        bx = MARGIN + 30
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(FONTS["heading"], 11)
        c.drawString(bx, bar_y + 28, "Savings Goal")
        c.setFillColor(_rgb(scheme.secondary))
        c.roundRect(bx, bar_y, bar_w, bar_h, 4, stroke=0, fill=1)
        c.setFillColor(_rgb(scheme.primary))
        c.roundRect(bx, bar_y, bar_w * 0.6, bar_h, 4, stroke=0, fill=1)

        # Notes
        notes_y = bar_y - 60
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(FONTS["heading"], 11)
        c.drawString(MARGIN, notes_y, "Notes")
        _draw_lines(
            c, MARGIN, notes_y - 20, w - 2 * MARGIN, 5, 22,
            scheme.secondary, dotted=True,
        )

        c.showPage()

    # ------------------------------------------------------------------
    # Daily Journal
    # ------------------------------------------------------------------

    def _generate_daily_journal(
        self,
        scheme: ColorScheme,
        size: str,
        output_path: Path,
        days: int = 30,
        **_,
    ) -> Path:
        w, h = SIZES.get(size, SIZES["A4"])
        c = canvas.Canvas(str(output_path), pagesize=(w, h))

        # --- Cover ---
        _draw_cover(c, scheme, w, h, "Daily Journal")

        # --- Day pages (2 giorni per pagina) ---
        today = datetime.now()
        for page_idx in range(0, days, 2):
            self._draw_journal_page(
                c, scheme, w, h,
                day1=today + timedelta(days=page_idx),
                day2=today + timedelta(days=page_idx + 1) if page_idx + 1 < days else None,
            )

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
    ) -> None:
        _fill_page(c, scheme.background, w, h)

        half_h = (h - MARGIN) / 2

        # Giorno 1 (metà superiore)
        self._draw_journal_day(c, scheme, w, MARGIN, h - MARGIN - 10, half_h - 20, day1)

        # Giorno 2 (metà inferiore)
        if day2:
            # Linea separatrice
            c.setStrokeColor(_rgb(scheme.secondary))
            c.setLineWidth(0.5)
            c.line(MARGIN, h / 2, w - MARGIN, h / 2)

            self._draw_journal_day(c, scheme, w, MARGIN, h / 2 - 10, half_h - 20, day2)

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
    ) -> None:
        usable_w = page_w - 2 * MARGIN
        y = y_top

        # Data
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(FONTS["heading"], 14)
        c.drawString(x_start, y, day.strftime("%A, %d %B %Y"))
        y -= 28

        # Mood tracker (5 cerchi)
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(FONTS["body"], 9)
        c.drawString(x_start, y, "Mood:")
        for i in range(5):
            c.setStrokeColor(_rgb(scheme.primary))
            c.setLineWidth(0.8)
            c.circle(x_start + 45 + i * 22, y + 3, 7, stroke=1, fill=0)
        y -= 26

        # Grateful for
        c.setFillColor(_rgb(scheme.primary))
        c.setFont(FONTS["heading"], 10)
        c.drawString(x_start, y, "Grateful for:")
        y -= 16
        for _ in range(3):
            _draw_lines(c, x_start + 10, y, usable_w - 10, 1, 0, scheme.secondary)
            y -= 18

        # Today's intention box
        c.setFillColor(_rgb(scheme.secondary))
        c.roundRect(x_start, y - 30, usable_w, 30, 4, stroke=0, fill=1)
        c.setFillColor(_rgb(scheme.accent))
        c.setFont(FONTS["light"], 9)
        c.drawString(x_start + 6, y - 12, "Today's intention:")
        _draw_lines(c, x_start + 100, y - 12, usable_w - 106, 1, 0, scheme.secondary)
        y -= 40

        # Journal lines
        remaining = y - (y_top - available_h)
        line_count = max(1, int(remaining / 18))
        _draw_lines(c, x_start, y, usable_w, line_count, 18, scheme.secondary)
