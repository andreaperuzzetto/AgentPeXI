"""Tests for file_gen pure functions, ColorScheme, and PDFGenerator generate dispatch."""
from __future__ import annotations

import pathlib
import pytest

from apps.backend.tools.file_gen import (
    _rgb,
    ColorScheme,
    DEFAULT_SCHEMES,
    SCHEME_BY_NAME,
    MARGIN,
    PDFGenerator,
)


# ---------------------------------------------------------------------------
# _rgb — pure function
# ---------------------------------------------------------------------------

def test_rgb_white():
    c = _rgb((255, 255, 255))
    assert abs(c.red - 1.0) < 0.01
    assert abs(c.green - 1.0) < 0.01
    assert abs(c.blue - 1.0) < 0.01


def test_rgb_black():
    c = _rgb((0, 0, 0))
    assert c.red == 0.0
    assert c.green == 0.0
    assert c.blue == 0.0


def test_rgb_mid_value():
    c = _rgb((128, 64, 32))
    assert abs(c.red - 128 / 255) < 0.01
    assert abs(c.green - 64 / 255) < 0.01
    assert abs(c.blue - 32 / 255) < 0.01


def test_rgb_returns_color_object():
    from reportlab.lib import colors
    c = _rgb((100, 150, 200))
    assert isinstance(c, colors.Color)


# ---------------------------------------------------------------------------
# ColorScheme dataclass
# ---------------------------------------------------------------------------

def test_color_scheme_fields():
    cs = ColorScheme("test", (1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12))
    assert cs.name == "test"
    assert cs.primary == (1, 2, 3)
    assert cs.secondary == (4, 5, 6)
    assert cs.accent == (7, 8, 9)
    assert cs.background == (10, 11, 12)


def test_default_schemes_count():
    assert len(DEFAULT_SCHEMES) == 5


def test_default_schemes_have_names():
    names = {s.name for s in DEFAULT_SCHEMES}
    assert "sage" in names
    assert "blush" in names
    assert "midnight" in names


def test_scheme_by_name_lookup():
    sage = SCHEME_BY_NAME["sage"]
    assert sage.name == "sage"


def test_scheme_by_name_all_present():
    for s in DEFAULT_SCHEMES:
        assert s.name in SCHEME_BY_NAME


def test_margin_positive():
    assert MARGIN > 0


# ---------------------------------------------------------------------------
# PDFGenerator — generate dispatch
# ---------------------------------------------------------------------------

@pytest.fixture
def gen():
    return PDFGenerator()


@pytest.fixture
def sage_scheme():
    return SCHEME_BY_NAME["sage"]


async def test_generate_unknown_template_raises(gen, sage_scheme, tmp_path):
    with pytest.raises(ValueError, match="Template sconosciuto"):
        await gen.generate("unknown_template", sage_scheme, "A4", tmp_path / "out.pdf")


async def test_generate_weekly_planner_creates_file(gen, sage_scheme, tmp_path):
    out = tmp_path / "planner.pdf"
    result = await gen.generate("weekly_planner", sage_scheme, "A4", out)
    assert result.exists()
    assert result.stat().st_size > 100


async def test_generate_habit_tracker_creates_file(gen, sage_scheme, tmp_path):
    out = tmp_path / "habit.pdf"
    result = await gen.generate("habit_tracker", sage_scheme, "A4", out)
    assert result.exists()
    assert result.stat().st_size > 100


async def test_generate_budget_sheet_creates_file(gen, sage_scheme, tmp_path):
    out = tmp_path / "budget.pdf"
    result = await gen.generate("budget_sheet", sage_scheme, "A4", out)
    assert result.exists()
    assert result.stat().st_size > 100


async def test_generate_daily_journal_creates_file(gen, sage_scheme, tmp_path):
    out = tmp_path / "journal.pdf"
    result = await gen.generate("daily_journal", sage_scheme, "A4", out)
    assert result.exists()
    assert result.stat().st_size > 100


async def test_generate_letter_size_weekly(gen, sage_scheme, tmp_path):
    out = tmp_path / "planner_letter.pdf"
    result = await gen.generate("weekly_planner", sage_scheme, "Letter", out)
    assert result.exists()


async def test_generate_blush_scheme(gen, tmp_path):
    blush = SCHEME_BY_NAME["blush"]
    out = tmp_path / "blush_planner.pdf"
    result = await gen.generate("weekly_planner", blush, "A4", out)
    assert result.exists()
