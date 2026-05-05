"""Tests for SVGGenerator constants, _default_color_variants, and bundle generation."""
from __future__ import annotations

import pathlib
import pytest

from apps.backend.tools.svg_gen import (
    SVGGenerator,
    SVG_TYPES,
    SVG_WIDTH,
    SVG_HEIGHT,
    SVG_VIEWBOX,
    _default_color_variants,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

def test_svg_types_nonempty():
    assert len(SVG_TYPES) >= 4


def test_svg_types_has_mandala():
    assert "mandala" in SVG_TYPES


def test_svg_types_has_quote():
    assert "quote" in SVG_TYPES


def test_svg_width_is_string():
    assert isinstance(SVG_WIDTH, str)
    assert "in" in SVG_WIDTH


def test_svg_viewbox_format():
    parts = SVG_VIEWBOX.split()
    assert len(parts) == 4


# ---------------------------------------------------------------------------
# _default_color_variants
# ---------------------------------------------------------------------------

def test_default_color_variants_returns_5():
    variants = _default_color_variants()
    assert len(variants) == 5


def test_default_color_variants_have_required_keys():
    for v in _default_color_variants():
        assert "bg" in v
        assert "primary" in v
        assert "accent" in v


def test_default_color_variants_hex_colors():
    for v in _default_color_variants():
        assert v["bg"].startswith("#")
        assert v["primary"].startswith("#")


# ---------------------------------------------------------------------------
# SVGGenerator.generate_bundle — actual file creation
# ---------------------------------------------------------------------------

@pytest.fixture
def gen():
    return SVGGenerator()


async def test_generate_bundle_mandala(gen, tmp_path):
    brief = {"type": "mandala", "complexity": 1}
    files = await gen.generate_bundle(brief, tmp_path)
    assert len(files) == 5
    for f in files:
        assert f.exists()
        assert f.suffix == ".svg"


async def test_generate_bundle_geometric(gen, tmp_path):
    brief = {"type": "geometric", "complexity": 1}
    files = await gen.generate_bundle(brief, tmp_path)
    assert len(files) == 5
    assert all(f.exists() for f in files)


async def test_generate_bundle_quote(gen, tmp_path):
    brief = {"type": "quote", "quote_text": "Stay focused", "quote_author": "—"}
    files = await gen.generate_bundle(brief, tmp_path)
    assert len(files) == 5


async def test_generate_bundle_floral_frame(gen, tmp_path):
    brief = {"type": "floral_frame"}
    files = await gen.generate_bundle(brief, tmp_path)
    assert len(files) == 5


async def test_generate_bundle_with_color_variants(gen, tmp_path):
    # Bundle always generates 5 files, repeating provided palette if < 5
    brief = {
        "type": "mandala",
        "color_variants": [
            {"bg": "#FFFFFF", "primary": "#333333", "secondary": "#EEEEEE", "accent": "#999999"},
        ],
    }
    files = await gen.generate_bundle(brief, tmp_path)
    assert len(files) == 5


async def test_generate_bundle_default_falls_back_to_mandala(gen, tmp_path):
    # No type specified → should use default or first supported type
    brief = {}
    files = await gen.generate_bundle(brief, tmp_path)
    assert len(files) >= 1
