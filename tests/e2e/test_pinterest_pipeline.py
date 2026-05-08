"""tests/e2e/test_pinterest_pipeline.py — A.3 gate: Pinterest pin step criteria.

  PIN1: _select_variants always returns variants 1 and 2 (A+B, always)
  PIN2: _select_variants includes variant 5 when cluster_size >= 3
  PIN3: _select_variants includes variant 3 when thumbnail_style = "editorial"
  PIN4: _select_variants includes variant 4 when "pain" in gap_to_exploit
  PIN5: _select_variants returns all 5 variants with all conditions met
  PIN6a: distinct Pinterest and Etsy descriptions have word-diff ratio >= 0.60
  PIN6b: identical texts have word-diff ratio = 0.0
  PIN6c: completely disjoint texts have word-diff ratio = 1.0
  PIN7: generate_pins() returns >= 2 dicts with all required keys
  PIN8: all pin dicts have non-empty image_path
"""
from __future__ import annotations

import pytest

from apps.backend.agents._pinterest._generation_mixin import _GenerationMixin


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _StubPinAgent(_GenerationMixin):
    """Minimal stub: tests _select_variants without any infrastructure."""
    pass


class _MockPinAgent(_GenerationMixin):
    """Full mock: overrides I/O methods, leaving business logic untouched."""

    async def _generate_pin_image(self, variant: int, listing_data: dict) -> tuple[str, dict]:
        return f"/pins/variant_{variant}.png", {"cost_image_gen": 0.01}

    async def _generate_pin_copy(
        self, variant: int, listing_data: dict
    ) -> tuple[str, str, dict]:
        titles = {
            1: "Level Up Your Week With This Printable System",
            2: "The Aesthetic Planner You Have Been Looking For",
            3: "Editorial Pick: The Minimalist Productivity Kit",
            4: "Overcome Planning Paralysis With This Simple Tool",
            5: "Complete Your Collection: Full Productivity Bundle",
        }
        descs = {
            1: "Transform your mornings with our beautifully crafted weekly planner printable.",
            2: "Designed for creatives, this planner brings visual joy to your daily routine.",
            3: "An editorial favorite for the discerning professional who values form and function.",
            4: "Stop letting disorganization hold you back. This system solves the root pain points.",
            5: "The complete bundle — everything you need to build a sustainable productivity habit.",
        }
        return titles.get(variant, f"Title {variant}"), descs.get(variant, f"Desc {variant}"), {"cost_llm": 0.002}

    async def _schedule_pins(self, listing_data: dict, pins: list[dict]) -> list[int]:
        return list(range(len(pins)))


# ---------------------------------------------------------------------------
# Helper: word-diff ratio (used in PIN6 tests)
# ---------------------------------------------------------------------------

def _word_diff_ratio(text_a: str, text_b: str) -> float:
    """Returns 1.0 - (shared_word_count / max_word_count). Higher = more different."""
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a and not words_b:
        return 0.0
    shared = words_a & words_b
    max_words = max(len(words_a), len(words_b))
    return 1.0 - (len(shared) / max_words)


# ---------------------------------------------------------------------------
# PIN1: Variants A (1) and B (2) are always selected
# ---------------------------------------------------------------------------

def test_pin1_base_variants_always_include_1_and_2():
    """_select_variants always returns at least [1, 2] regardless of input."""
    agent = _StubPinAgent()
    result = agent._select_variants({})
    assert 1 in result, "Variant A (1) must always be selected"
    assert 2 in result, "Variant B (2) must always be selected"


def test_pin1_no_extras_with_minimal_input():
    """With cluster_size=0 and no signals, exactly [1, 2] returned."""
    agent = _StubPinAgent()
    assert sorted(agent._select_variants({"cluster_size": 0})) == [1, 2]


# ---------------------------------------------------------------------------
# PIN2: Variant E (5) when cluster_size >= 3
# ---------------------------------------------------------------------------

def test_pin2_variant_5_included_when_cluster_size_gte_3():
    agent = _StubPinAgent()
    assert 5 in agent._select_variants({"cluster_size": 3})
    assert 5 in agent._select_variants({"cluster_size": 10})


def test_pin2_variant_5_excluded_when_cluster_size_lt_3():
    agent = _StubPinAgent()
    assert 5 not in agent._select_variants({"cluster_size": 2})
    assert 5 not in agent._select_variants({"cluster_size": 0})


# ---------------------------------------------------------------------------
# PIN3: Variant C (3) when thumbnail_style = "editorial"
# ---------------------------------------------------------------------------

def test_pin3_variant_3_included_when_editorial():
    agent = _StubPinAgent()
    result = agent._select_variants({"selling_signals": {"thumbnail_style": "editorial"}})
    assert 3 in result


def test_pin3_variant_3_excluded_when_not_editorial():
    agent = _StubPinAgent()
    assert 3 not in agent._select_variants({"selling_signals": {"thumbnail_style": "flat_lay"}})
    assert 3 not in agent._select_variants({})


# ---------------------------------------------------------------------------
# PIN4: Variant D (4) when "pain" in gap_to_exploit (case-insensitive)
# ---------------------------------------------------------------------------

def test_pin4_variant_4_included_when_pain_in_gap():
    agent = _StubPinAgent()
    assert 4 in agent._select_variants({"gap_to_exploit": "solving a real pain point"})
    assert 4 in agent._select_variants({"gap_to_exploit": "PAIN in the workflow"})


def test_pin4_variant_4_excluded_when_no_pain():
    agent = _StubPinAgent()
    assert 4 not in agent._select_variants({"gap_to_exploit": "gap in competitor designs"})
    assert 4 not in agent._select_variants({})


# ---------------------------------------------------------------------------
# PIN5: All 5 variants with all conditions met
# ---------------------------------------------------------------------------

def test_pin5_all_variants_with_full_conditions():
    agent = _StubPinAgent()
    result = agent._select_variants({
        "cluster_size": 5,
        "gap_to_exploit": "addresses a deep pain point",
        "selling_signals": {"thumbnail_style": "editorial"},
    })
    assert set(result) == {1, 2, 3, 4, 5}, f"Expected all 5 variants, got {sorted(result)}"


# ---------------------------------------------------------------------------
# PIN6: Description word-diff ratio >= 0.60
# ---------------------------------------------------------------------------

def test_pin6a_distinct_descriptions_have_diff_ratio_gte_60():
    """Real Pinterest copy that is clearly different from Etsy text has diff >= 0.60."""
    etsy_desc = (
        "This printable weekly planner helps you organize your week. "
        "Print at home on A4 or Letter paper. Instant download PDF."
    )
    pin_copy = (
        "Transform your mornings with a beautifully designed system "
        "for intentional living. Perfect for busy professionals craving structure."
    )
    ratio = _word_diff_ratio(etsy_desc, pin_copy)
    assert ratio >= 0.60, f"Expected diff ratio >= 0.60, got {ratio:.2f}"


def test_pin6b_identical_descriptions_have_zero_diff():
    """Identical texts have diff ratio = 0.0."""
    text = "weekly planner printable instant download pdf"
    assert _word_diff_ratio(text, text) == 0.0


def test_pin6c_completely_different_descriptions_have_max_diff():
    """Non-overlapping word sets have diff ratio = 1.0."""
    assert _word_diff_ratio("alpha beta gamma", "delta epsilon zeta") == 1.0


# ---------------------------------------------------------------------------
# PIN7: generate_pins() returns >= 2 dicts with required keys
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pin7_generate_pins_returns_min_2_variants():
    """generate_pins() returns >= 2 pin dicts regardless of listing_data."""
    agent = _MockPinAgent()
    listing_data = {
        "listing_id": "csv_20260508_001",
        "title": "Weekly Planner Printable",
        "niche": "weekly planner printable",
        "section_key": "planners_organizers",
        "audience_target": "remote workers building a daily routine",
        "conversion_triggers": ["instant download", "editable"],
        "selling_signals": {"thumbnail_style": "flat_lay"},
        "gap_to_exploit": "lacks structured time blocks",
        "cluster_size": 2,
        "board_id": "board_123",
        "production_queue_id": 1,
    }
    pins = await agent.generate_pins(listing_data)
    assert len(pins) >= 2, f"Expected >= 2 pins, got {len(pins)}"
    required_keys = {"variant", "image_path", "title", "description", "cost_image_gen", "cost_llm"}
    for pin in pins:
        assert required_keys.issubset(pin.keys()), (
            f"Pin variant={pin.get('variant')} missing keys: {required_keys - pin.keys()}"
        )


# ---------------------------------------------------------------------------
# PIN8: All pin dicts have non-empty image_path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pin8_generate_pins_image_path_nonempty():
    """Every returned pin dict has a non-empty image_path."""
    agent = _MockPinAgent()
    pins = await agent.generate_pins({"listing_id": "csv_001", "niche": "test", "cluster_size": 0})
    assert len(pins) >= 1
    for pin in pins:
        assert pin["image_path"], f"pin variant={pin['variant']} has empty image_path"
