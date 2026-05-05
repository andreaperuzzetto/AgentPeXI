"""Tests for DesignAgent helper functions: colors, utils, scoring."""
from __future__ import annotations

import pytest

from apps.backend.agents._design.colors import _hex_to_rgb, _colors_to_scheme, get_print_specs
from apps.backend.agents._design.utils import _niche_slug, _get_cover_title
from apps.backend.agents._design.scoring import _calculate_design_confidence


# ---------------------------------------------------------------------------
# _hex_to_rgb
# ---------------------------------------------------------------------------

def test_hex_to_rgb_red():
    assert _hex_to_rgb("#FF0000") == (255, 0, 0)


def test_hex_to_rgb_black():
    assert _hex_to_rgb("#000000") == (0, 0, 0)


def test_hex_to_rgb_white():
    assert _hex_to_rgb("#FFFFFF") == (255, 255, 255)


def test_hex_to_rgb_no_hash():
    assert _hex_to_rgb("4A4A4A") == (74, 74, 74)


def test_hex_to_rgb_uppercase_equals_lowercase():
    assert _hex_to_rgb("#aabbcc") == _hex_to_rgb("#AABBCC")


# ---------------------------------------------------------------------------
# _colors_to_scheme
# ---------------------------------------------------------------------------

def test_colors_to_scheme_full_dict():
    colors = {
        "primary": "#FF0000",
        "secondary": "#00FF00",
        "text": "#0000FF",
        "bg": "#FFFFFF",
    }
    scheme = _colors_to_scheme("test", colors)
    assert scheme.name == "test"
    assert scheme.primary == (255, 0, 0)
    assert scheme.secondary == (0, 255, 0)
    assert scheme.accent == (0, 0, 255)
    assert scheme.background == (255, 255, 255)


def test_colors_to_scheme_missing_keys_use_defaults():
    scheme = _colors_to_scheme("default_test", {})
    assert scheme.primary == _hex_to_rgb("#4A4A4A")
    assert scheme.secondary == _hex_to_rgb("#F5F5F5")
    assert scheme.accent == _hex_to_rgb("#1A1A1A")
    assert scheme.background == (255, 255, 255)


def test_colors_to_scheme_bg_key_used():
    scheme = _colors_to_scheme("bg_test", {"bg": "#000000"})
    assert scheme.background == (0, 0, 0)


# ---------------------------------------------------------------------------
# get_print_specs
# ---------------------------------------------------------------------------

def test_get_print_specs_colored_bg_has_bleed():
    specs = get_print_specs(210.0, 297.0, has_colored_bg=True)
    assert specs["has_bleed"] is True
    assert specs["bleed_left"] < 0  # negative bleed
    assert specs["bleed_right"] > 210.0


def test_get_print_specs_no_colored_bg_no_bleed():
    specs = get_print_specs(210.0, 297.0, has_colored_bg=False)
    assert specs["has_bleed"] is False
    assert specs["bleed_left"] == 0
    assert specs["bleed_right"] == 210.0


def test_get_print_specs_content_dimensions():
    from reportlab.lib.units import mm
    from apps.backend.agents._design.presets import SAFE_ZONE_MM

    w, h = 200.0, 300.0
    specs = get_print_specs(w, h, has_colored_bg=False)
    safe = SAFE_ZONE_MM * mm
    assert abs(specs["content_width"] - (w - 2 * safe)) < 1e-6
    assert abs(specs["content_height"] - (h - 2 * safe)) < 1e-6


# ---------------------------------------------------------------------------
# _niche_slug
# ---------------------------------------------------------------------------

def test_niche_slug_basic():
    assert _niche_slug("Bullet Journal") == "bullet_journal"


def test_niche_slug_special_chars_removed():
    slug = _niche_slug("Café & Décor!")
    import re
    assert re.match(r'^[a-z0-9_]+$', slug)


def test_niche_slug_truncated_to_40():
    long_niche = "A Very Long Niche Name That Exceeds Forty Characters Here"
    slug = _niche_slug(long_niche)
    assert len(slug) <= 40


def test_niche_slug_lowercase():
    assert _niche_slug("UPPER CASE") == "upper_case"


# ---------------------------------------------------------------------------
# _get_cover_title
# ---------------------------------------------------------------------------

def test_get_cover_title_with_top_keywords():
    research = {"top_keywords": ["minimalist planner"]}
    title = _get_cover_title("Bullet Journal", "weekly_planner", research)
    assert "Minimalist Planner" in title


def test_get_cover_title_empty_research_uses_niche():
    title = _get_cover_title("Bullet Journal", "weekly_planner", {})
    assert "Bullet Journal" in title


def test_get_cover_title_none_research_uses_niche():
    title = _get_cover_title("Bullet Journal", "weekly_planner", None)
    assert "Bullet Journal" in title


def test_get_cover_title_never_exceeds_60_chars():
    long_niche = "A" * 50
    title = _get_cover_title(long_niche, "weekly_planner_template_very_long", None)
    assert len(title) <= 60


def test_get_cover_title_keyword_no_longer_than_60():
    research = {"top_keywords": ["keyword " * 10]}
    title = _get_cover_title("Test Niche", "weekly_planner", research)
    assert len(title) <= 60


# ---------------------------------------------------------------------------
# _calculate_design_confidence
# ---------------------------------------------------------------------------

def test_design_confidence_full_success():
    score, missing = _calculate_design_confidence(
        variants_generated=3,
        variants_requested=3,
        thumbnails=[
            {"cover": True, "interior": True, "mockup": True},
            {"cover": True, "interior": True, "mockup": True},
            {"cover": True, "interior": True, "mockup": True},
        ],
        validation_results=[{"valid": True}, {"valid": True}, {"valid": True}],
        fonts_available={"Lato": True, "PlayfairDisplay": True},
        research_available=True,
    )
    assert score >= 0.95
    assert missing == []


def test_design_confidence_zero_variants_requested_not_penalized():
    score, missing = _calculate_design_confidence(
        variants_generated=0,
        variants_requested=0,
        thumbnails=[],
        validation_results=[{"valid": True}],
        fonts_available={"Lato": True},
        research_available=True,
    )
    # variants component is 0 (no penalty), rest contributes
    assert score > 0.0


def test_design_confidence_partial_thumbnails_reflected():
    score_full, _ = _calculate_design_confidence(
        variants_generated=2,
        variants_requested=2,
        thumbnails=[
            {"cover": True, "interior": True, "mockup": True},
            {"cover": True, "interior": True, "mockup": True},
        ],
        validation_results=[{"valid": True}, {"valid": True}],
        fonts_available={"Lato": True},
        research_available=True,
    )
    score_partial, missing_partial = _calculate_design_confidence(
        variants_generated=2,
        variants_requested=2,
        thumbnails=[
            {"cover": True, "interior": False, "mockup": False},
            {"cover": False, "interior": False, "mockup": False},
        ],
        validation_results=[{"valid": True}, {"valid": True}],
        fonts_available={"Lato": True},
        research_available=True,
    )
    assert score_partial < score_full
    assert any("thumbnail" in m.lower() for m in missing_partial)


def test_design_confidence_no_validation_results_partial_credit():
    score, missing = _calculate_design_confidence(
        variants_generated=1,
        variants_requested=1,
        thumbnails=[{"cover": True, "interior": True, "mockup": True}],
        validation_results=[],
        fonts_available={"Lato": True},
        research_available=True,
    )
    # No validation_results → 0.10 partial credit, warning in missing
    assert any("validation" in m.lower() for m in missing)


def test_design_confidence_low_font_ratio_adds_missing():
    _, missing = _calculate_design_confidence(
        variants_generated=1,
        variants_requested=1,
        thumbnails=[{"cover": True, "interior": True, "mockup": True}],
        validation_results=[{"valid": True}],
        fonts_available={"Lato": False, "PlayfairDisplay": False, "Raleway": False},
        research_available=True,
    )
    assert any("font" in m.lower() for m in missing)
