"""RC2 — Coverage tests for _design generators_mixin, selection_mixin, layout.

Mock contract follows RC3 (test_llm_tools_mixin_coverage.py).
_call_llm → AsyncMock(return_value="<text>")
_call_tool → AsyncMock(return_value={})
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from apps.backend.agents._design.generators_mixin import (
    _DesignGeneratorsMixin,
    _build_5component_prompt,
    _verify_image_quality,
)
from apps.backend.agents._design.layout import (
    add_instructions_page,
    draw_corner_ornaments,
    draw_ornamental_separator,
    set_pdf_metadata,
)
from apps.backend.agents._design.selection_mixin import _DesignSelectionMixin
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus


# ─────────────────────────────────────────────────────────────────────────────
# Factories  (MOCK CONTRACT from RC3)
# ─────────────────────────────────────────────────────────────────────────────


def _make_selection_agent(llm_return: str = "minimal", extra_attrs: dict | None = None):
    """Minimal agent with _DesignSelectionMixin + mocked _call_llm."""

    class _Agent(_DesignSelectionMixin):
        pass

    agent = _Agent()
    agent._call_llm = AsyncMock(return_value=llm_return)

    if extra_attrs:
        for k, v in extra_attrs.items():
            setattr(agent, k, v)
    return agent


def _make_generators_agent(extra_attrs: dict | None = None):
    """Minimal agent with _DesignGeneratorsMixin + all required mocks."""

    class _Agent(_DesignGeneratorsMixin):
        pass

    agent = _Agent()
    agent.name = "design_test_agent"
    agent._log_step = AsyncMock(return_value=42)
    agent._call_llm = AsyncMock(return_value="minimal")
    agent._call_tool = AsyncMock(return_value={})
    agent._telegram_broadcast = None

    # Storage mock
    agent.storage = MagicMock()
    agent.storage.is_available = MagicMock(return_value=True)
    agent.storage.base_path = Path("/tmp/test_design_rc2")

    # Memory mock
    agent.memory = MagicMock()
    db_mock = MagicMock()
    agent.memory.get_db = AsyncMock(return_value=db_mock)

    # Image / SVG / PDF generators
    agent._image_gen = MagicMock()
    agent._image_gen.is_available = True
    agent._image_gen.provider_name = "flux"
    agent._image_gen.generate_digital_art = AsyncMock(return_value=Path("/tmp/rc2_art.png"))

    agent._svg_gen = MagicMock()
    agent._svg_gen.generate_bundle = AsyncMock(return_value=[Path("/tmp/rc2.svg")])

    agent._pdf_gen = MagicMock()
    agent._pdf_gen.generate = MagicMock()

    agent._get_mock_mode = MagicMock(return_value=False)

    if extra_attrs:
        for k, v in extra_attrs.items():
            setattr(agent, k, v)
    return agent


def _make_task(task_id: str = "task-rc2", input_data: dict | None = None) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        agent_name="DesignAgent",
        input_data=input_data or {},
    )


def _make_canvas_mock() -> MagicMock:
    c = MagicMock()
    c.beginPath = MagicMock()
    c.moveTo = MagicMock()
    c.lineTo = MagicMock()
    c.closePath = MagicMock()
    c.fill = MagicMock()
    c.line = MagicMock()
    c.circle = MagicMock()
    c.setDash = MagicMock()
    c.setLineWidth = MagicMock()
    c.setStrokeColor = MagicMock()
    c.setFillColor = MagicMock()
    c.showPage = MagicMock()
    c.saveState = MagicMock()
    c.restoreState = MagicMock()
    c.rect = MagicMock()
    c.setFont = MagicMock()
    c.drawCentredString = MagicMock()
    c.drawString = MagicMock()
    c.setTitle = MagicMock()
    c.setAuthor = MagicMock()
    c.setSubject = MagicMock()
    c.setKeywords = MagicMock()
    c.setCreator = MagicMock()
    c.setProducer = MagicMock()
    return c


# ─────────────────────────────────────────────────────────────────────────────
# _verify_image_quality (module-level fn, already in module scope)
# ─────────────────────────────────────────────────────────────────────────────


class TestVerifyImageQuality:
    def test_no_metadata_returns_true(self):
        assert _verify_image_quality({}) is True

    def test_missing_width_returns_true(self):
        assert _verify_image_quality({"height": 3000}) is True

    def test_missing_height_returns_true(self):
        assert _verify_image_quality({"width": 3000}) is True

    def test_below_2000px_returns_false(self):
        assert _verify_image_quality({"width": 1800, "height": 3000}) is False

    def test_exactly_2000px_returns_true(self):
        assert _verify_image_quality({"width": 2000, "height": 2000}) is True

    def test_large_image_returns_true(self):
        assert _verify_image_quality({"width": 3000, "height": 3000}) is True


# ─────────────────────────────────────────────────────────────────────────────
# _build_5component_prompt (module-level fn)
# ─────────────────────────────────────────────────────────────────────────────


class TestBuild5ComponentPrompt:
    def _make_identity(self, **kwargs):
        identity = MagicMock()
        identity.aesthetic_name = kwargs.get("aesthetic_name", "minimalist")
        identity.palette_primary = kwargs.get("palette_primary", "#FFFFFF")
        identity.palette_secondary = kwargs.get("palette_secondary", "#000000")
        identity.palette_accent = kwargs.get("palette_accent", "#FF0000")
        identity.mockup_style = kwargs.get("mockup_style", "flat_lay")
        identity.tone = kwargs.get("tone", "professional")
        return identity

    def test_returns_string_with_all_components(self):
        brief = {"niche": "wellness", "product_type": "planner", "section_key": ""}
        identity = self._make_identity()
        result = _build_5component_prompt(brief, identity)
        assert "SUBJECT:" in result
        assert "STYLE:" in result
        assert "COMPOSITION:" in result
        assert "TECHNICAL:" in result
        assert "NEGATIVE PROMPT:" in result

    def test_lifestyle_style_included_when_not_flat_lay(self):
        brief = {"niche": "wellness", "product_type": "planner", "section_key": ""}
        identity = self._make_identity(mockup_style="lifestyle")
        result = _build_5component_prompt(brief, identity)
        assert "lifestyle" in result

    def test_flat_lay_included_when_flat_lay(self):
        brief = {"niche": "wellness", "product_type": "planner", "section_key": ""}
        identity = self._make_identity(mockup_style="flat_lay")
        result = _build_5component_prompt(brief, identity)
        assert "flat lay" in result

    def test_section_style_override_applied(self):
        brief = {
            "niche": "wellness",
            "product_type": "planner",
            "section_key": "wellness_self_care",
        }
        identity = self._make_identity()
        result = _build_5component_prompt(brief, identity)
        assert "sage green" in result

    def test_empty_palette_fields_handled(self):
        brief = {"niche": "art", "product_type": "print", "section_key": ""}
        identity = self._make_identity()
        identity.palette_primary = ""
        identity.palette_secondary = ""
        identity.palette_accent = ""
        result = _build_5component_prompt(brief, identity)
        assert "natural tones" in result


# ─────────────────────────────────────────────────────────────────────────────
# layout.py — pure functions
# ─────────────────────────────────────────────────────────────────────────────


class TestDrawCornerOrnaments:
    def test_non_decorative_preset_returns_early(self):
        canvas = _make_canvas_mock()
        draw_corner_ornaments(canvas, 210, 297, "#000000", "minimal")
        canvas.line.assert_not_called()
        canvas.circle.assert_not_called()

    def test_corporate_preset_returns_early(self):
        canvas = _make_canvas_mock()
        draw_corner_ornaments(canvas, 210, 297, "#000000", "corporate")
        canvas.line.assert_not_called()

    def test_decorative_preset_draws_lines(self):
        canvas = _make_canvas_mock()
        draw_corner_ornaments(canvas, 210, 297, "#8B6914", "decorative")
        assert canvas.line.call_count > 0
        assert canvas.setLineWidth.call_count > 0

    def test_decorative_preset_draws_inner_corners(self):
        canvas = _make_canvas_mock()
        draw_corner_ornaments(canvas, 210, 297, "#8B6914", "decorative")
        # outer 8 + inner 8 = 16 lines total
        assert canvas.line.call_count == 16

    def test_playful_preset_draws_circles(self):
        canvas = _make_canvas_mock()
        draw_corner_ornaments(canvas, 210, 297, "#E91E63", "playful")
        # 4 positions × 2 circles each = 8 circle calls
        assert canvas.circle.call_count == 8
        canvas.line.assert_not_called()


class TestDrawOrnamentalSeparator:
    def test_minimal_preset_draws_single_line(self):
        canvas = _make_canvas_mock()
        draw_ornamental_separator(canvas, 10, 100, 190, "#000000", "minimal")
        canvas.line.assert_called_once()

    def test_corporate_preset_draws_single_line(self):
        canvas = _make_canvas_mock()
        draw_ornamental_separator(canvas, 10, 100, 190, "#000000", "corporate")
        canvas.line.assert_called_once()

    def test_decorative_preset_draws_diamonds(self):
        canvas = _make_canvas_mock()
        draw_ornamental_separator(canvas, 10, 100, 190, "#8B6914", "decorative")
        # Two side lines + 3 diamonds via fill()
        assert canvas.line.call_count == 2
        assert canvas.fill.call_count == 3

    def test_playful_preset_draws_dashed_line(self):
        canvas = _make_canvas_mock()
        draw_ornamental_separator(canvas, 10, 100, 190, "#E91E63", "playful")
        canvas.line.assert_called_once()
        canvas.setDash.assert_called()


class TestAddInstructionsPage:
    def test_calls_showPage_and_saveState(self):
        canvas = _make_canvas_mock()
        add_instructions_page(canvas, (595, 842), "minimal")
        canvas.showPage.assert_called_once()
        canvas.saveState.assert_called_once()
        canvas.restoreState.assert_called_once()

    def test_draws_title_and_footer(self):
        canvas = _make_canvas_mock()
        add_instructions_page(canvas, (595, 842), "minimal")
        assert canvas.drawCentredString.call_count >= 2

    def test_draws_instruction_sections(self):
        canvas = _make_canvas_mock()
        add_instructions_page(canvas, (595, 842), "decorative")
        # 3 section titles + bullet items → many drawString calls
        assert canvas.drawString.call_count > 0

    def test_unknown_preset_falls_back_to_minimal(self):
        canvas = _make_canvas_mock()
        # Should not raise
        add_instructions_page(canvas, (595, 842), "nonexistent_preset")
        canvas.showPage.assert_called_once()

    def test_fills_background(self):
        canvas = _make_canvas_mock()
        add_instructions_page(canvas, (595, 842), "playful")
        canvas.rect.assert_called_once()


class TestSetPdfMetadata:
    def test_sets_all_metadata_fields(self):
        canvas = _make_canvas_mock()
        set_pdf_metadata(canvas, "wedding planning", "weekly_planner", "printable_pdf")
        canvas.setTitle.assert_called_once()
        canvas.setAuthor.assert_called_once()
        canvas.setSubject.assert_called_once()
        canvas.setKeywords.assert_called_once()
        canvas.setCreator.assert_called_once()
        canvas.setProducer.assert_called_once()

    def test_title_contains_niche_and_template(self):
        canvas = _make_canvas_mock()
        set_pdf_metadata(canvas, "wedding", "weekly_planner", "printable_pdf")
        title_arg = canvas.setTitle.call_args[0][0]
        assert "Wedding" in title_arg
        assert "Weekly Planner" in title_arg

    def test_keywords_contain_niche(self):
        canvas = _make_canvas_mock()
        set_pdf_metadata(canvas, "budget", "budget_tracker", "printable_pdf")
        kw_arg = canvas.setKeywords.call_args[0][0]
        assert "budget" in kw_arg


# ─────────────────────────────────────────────────────────────────────────────
# selection_mixin.py
# ─────────────────────────────────────────────────────────────────────────────


class TestSelectPreset:
    # Stage 1: keyword score ≥ 2 → no LLM call

    @pytest.mark.asyncio
    async def test_stage1_minimal_two_keywords_no_llm(self):
        agent = _make_selection_agent()
        result = await asyncio.wait_for(
            agent._select_preset("budget planner", "weekly", None),
            timeout=5,
        )
        assert result == "minimal"
        agent._call_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_stage1_corporate_two_keywords(self):
        agent = _make_selection_agent()
        result = await asyncio.wait_for(
            agent._select_preset("business corporate report", "template", None),
            timeout=5,
        )
        assert result == "corporate"
        agent._call_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_stage1_decorative_two_keywords(self):
        agent = _make_selection_agent()
        result = await asyncio.wait_for(
            agent._select_preset("wedding floral", "invitation", None),
            timeout=5,
        )
        assert result == "decorative"
        agent._call_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_stage1_playful_two_keywords(self):
        agent = _make_selection_agent()
        result = await asyncio.wait_for(
            agent._select_preset("kids birthday party", "activity", None),
            timeout=5,
        )
        assert result == "playful"
        agent._call_llm.assert_not_called()

    # Stage 2: no clear keyword winner → LLM call

    @pytest.mark.asyncio
    async def test_stage2_llm_called_when_no_keyword_match(self):
        agent = _make_selection_agent(llm_return="decorative")
        result = await asyncio.wait_for(
            agent._select_preset("custom product", "special", None),
            timeout=5,
        )
        assert result == "decorative"
        agent._call_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_stage2_llm_returns_invalid_preset_fallback_minimal(self):
        agent = _make_selection_agent(llm_return="unknown_preset")
        result = await asyncio.wait_for(
            agent._select_preset("custom product", "special", None),
            timeout=5,
        )
        assert result == "minimal"

    @pytest.mark.asyncio
    async def test_stage2_llm_raises_fallback_minimal(self):
        agent = _make_selection_agent()
        agent._call_llm = AsyncMock(side_effect=RuntimeError("LLM down"))
        result = await asyncio.wait_for(
            agent._select_preset("custom product", "special", None),
            timeout=5,
        )
        assert result == "minimal"

    @pytest.mark.asyncio
    async def test_stage2_with_research_context(self):
        agent = _make_selection_agent(llm_return="minimal")
        research_context = {
            "target_audience": "brides",
            "avg_price": "15.99",
            "top_keywords": ["wedding", "planner"],
            "competition_level": "high",
        }
        result = await asyncio.wait_for(
            agent._select_preset("custom niche", "special", research_context),
            timeout=5,
        )
        assert result == "minimal"
        # Prompt must include research summary
        prompt_content = agent._call_llm.call_args[1].get(
            "messages", agent._call_llm.call_args[0][0]
            if agent._call_llm.call_args[0] else []
        )
        if isinstance(prompt_content, list):
            prompt_text = prompt_content[0]["content"]
            assert "Target audience" in prompt_text or "brides" in prompt_text

    @pytest.mark.asyncio
    async def test_stage2_with_failure_patterns_winners(self):
        agent = _make_selection_agent(llm_return="minimal")
        failure_patterns = {
            "winners": [
                {"template": "weekly_planner", "color_scheme": "neutral",
                 "sales": 10, "views": 200, "date": "2025-01-01"},
            ],
        }
        result = await asyncio.wait_for(
            agent._select_preset("custom niche", "special", None, failure_patterns),
            timeout=5,
        )
        assert result == "minimal"
        agent._call_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_stage2_with_failure_patterns_outcomes(self):
        agent = _make_selection_agent(llm_return="corporate")
        failure_patterns = {
            "recent_outcomes": [
                {"preset": "minimal", "template": "weekly_planner",
                 "color_scheme": "neutral", "pdf_valid": "True", "date": "2025-01-01"},
            ],
        }
        result = await asyncio.wait_for(
            agent._select_preset("custom niche", "special", None, failure_patterns),
            timeout=5,
        )
        assert result == "corporate"

    @pytest.mark.asyncio
    async def test_stage2_with_failure_patterns_known_issues(self):
        agent = _make_selection_agent(llm_return="minimal")
        failure_patterns = {
            "known_issues": ["PDF rendering broken for decorative preset"],
        }
        result = await asyncio.wait_for(
            agent._select_preset("custom niche", "special", None, failure_patterns),
            timeout=5,
        )
        assert result == "minimal"

    @pytest.mark.asyncio
    async def test_stage2_with_failure_patterns_low_ctr_combos(self):
        agent = _make_selection_agent(llm_return="minimal")
        failure_patterns = {
            "low_ctr_combos": [
                {"template": "weekly_planner", "color_scheme": "neutral"},
            ],
        }
        result = await asyncio.wait_for(
            agent._select_preset("custom niche", "special", None, failure_patterns),
            timeout=5,
        )
        assert result == "minimal"

    @pytest.mark.asyncio
    async def test_stage2_with_all_failure_pattern_keys(self):
        agent = _make_selection_agent(llm_return="playful")
        failure_patterns = {
            "winners": [
                {"template": "weekly_planner", "color_scheme": "warm",
                 "sales": 5, "views": 100, "date": "2025-02-01"},
            ],
            "recent_outcomes": [
                {"preset": "minimal", "template": "daily_planner",
                 "color_scheme": "cool", "pdf_valid": "False", "date": "2025-01-15"},
            ],
            "known_issues": ["Some font rendering issue"],
            "low_ctr_combos": [{"template": "goal_planner", "color_scheme": "dark"}],
        }
        result = await asyncio.wait_for(
            agent._select_preset("custom niche", "special", None, failure_patterns),
            timeout=5,
        )
        assert result == "playful"


class TestResolveColorSchemeNicheAware:
    @pytest.mark.asyncio
    async def test_returns_parsed_colors_when_llm_returns_valid_json(self):
        valid_json = '{"primary": "#FF0000", "secondary": "#00FF00", "accent": "#0000FF", "bg": "#FFFFFF", "text": "#000000"}'
        agent = _make_selection_agent(llm_return=valid_json)
        result = await asyncio.wait_for(
            agent._resolve_color_scheme_niche_aware("warm", "wellness", "minimal"),
            timeout=5,
        )
        assert result["primary"] == "#FF0000"
        assert result["bg"] == "#FFFFFF"
        assert result["text"] == "#000000"

    @pytest.mark.asyncio
    async def test_returns_fallback_when_llm_raises(self):
        agent = _make_selection_agent()
        agent._call_llm = AsyncMock(side_effect=RuntimeError("LLM error"))
        result = await asyncio.wait_for(
            agent._resolve_color_scheme_niche_aware("neutral", "wellness", "minimal"),
            timeout=5,
        )
        # Fallback: keys from preset_data
        assert "primary" in result
        assert "bg" in result
        assert "text" in result

    @pytest.mark.asyncio
    async def test_returns_fallback_when_llm_returns_invalid_hex(self):
        invalid_json = '{"primary": "red", "secondary": "#00FF00", "accent": "#0000FF", "bg": "#FFFFFF", "text": "#000000"}'
        agent = _make_selection_agent(llm_return=invalid_json)
        result = await asyncio.wait_for(
            agent._resolve_color_scheme_niche_aware("warm", "wellness", "minimal"),
            timeout=5,
        )
        # Invalid hex → fallback
        from apps.backend.agents._design.presets import STYLE_PRESETS
        assert result["bg"] == STYLE_PRESETS["minimal"]["bg_color"]

    @pytest.mark.asyncio
    async def test_returns_fallback_when_llm_returns_missing_keys(self):
        partial_json = '{"primary": "#FF0000", "secondary": "#00FF00"}'
        agent = _make_selection_agent(llm_return=partial_json)
        result = await asyncio.wait_for(
            agent._resolve_color_scheme_niche_aware("cool", "wellness", "decorative"),
            timeout=5,
        )
        from apps.backend.agents._design.presets import STYLE_PRESETS
        assert result["bg"] == STYLE_PRESETS["decorative"]["bg_color"]

    @pytest.mark.asyncio
    async def test_returns_fallback_when_llm_returns_non_json(self):
        agent = _make_selection_agent(llm_return="I cannot provide that information.")
        result = await asyncio.wait_for(
            agent._resolve_color_scheme_niche_aware("warm", "wellness", "corporate"),
            timeout=5,
        )
        from apps.backend.agents._design.presets import STYLE_PRESETS
        assert result["bg"] == STYLE_PRESETS["corporate"]["bg_color"]

    @pytest.mark.asyncio
    async def test_each_preset_fallback_returns_correct_colors(self):
        for preset in ("minimal", "decorative", "corporate", "playful"):
            agent = _make_selection_agent()
            agent._call_llm = AsyncMock(side_effect=RuntimeError("fail"))
            result = await asyncio.wait_for(
                agent._resolve_color_scheme_niche_aware("neutral", "test", preset),
                timeout=5,
            )
            from apps.backend.agents._design.presets import STYLE_PRESETS
            assert result["bg"] == STYLE_PRESETS[preset]["bg_color"]


class TestSelectTemplateLlm:
    @pytest.mark.asyncio
    async def test_returns_llm_result_when_valid_template(self):
        agent = _make_selection_agent(llm_return="budget_tracker")
        result = await asyncio.wait_for(
            agent._select_template_llm("finance", "printable_pdf", None),
            timeout=5,
        )
        assert result == "budget_tracker"

    @pytest.mark.asyncio
    async def test_returns_first_template_when_llm_returns_invalid(self):
        agent = _make_selection_agent(llm_return="nonexistent_template")
        result = await asyncio.wait_for(
            agent._select_template_llm("finance", "printable_pdf", None),
            timeout=5,
        )
        from apps.backend.agents._design.presets import AVAILABLE_TEMPLATES
        assert result == AVAILABLE_TEMPLATES["printable_pdf"][0]

    @pytest.mark.asyncio
    async def test_returns_first_template_when_llm_raises(self):
        agent = _make_selection_agent()
        agent._call_llm = AsyncMock(side_effect=RuntimeError("LLM error"))
        result = await asyncio.wait_for(
            agent._select_template_llm("finance", "printable_pdf", None),
            timeout=5,
        )
        from apps.backend.agents._design.presets import AVAILABLE_TEMPLATES
        assert result == AVAILABLE_TEMPLATES["printable_pdf"][0]

    @pytest.mark.asyncio
    async def test_with_research_context(self):
        agent = _make_selection_agent(llm_return="meal_planner")
        research_context = {
            "top_keywords": ["meal prep", "healthy"],
            "gaps": ["vegan plans"],
            "avg_price": "9.99",
        }
        result = await asyncio.wait_for(
            agent._select_template_llm("food", "printable_pdf", research_context),
            timeout=5,
        )
        assert result == "meal_planner"
        agent._call_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_failure_patterns_winners(self):
        agent = _make_selection_agent(llm_return="weekly_planner")
        failure_patterns = {
            "winners": [{"template": "weekly_planner"}],
        }
        result = await asyncio.wait_for(
            agent._select_template_llm("productivity", "printable_pdf", None, failure_patterns),
            timeout=5,
        )
        assert result == "weekly_planner"

    @pytest.mark.asyncio
    async def test_with_failure_patterns_outcomes(self):
        agent = _make_selection_agent(llm_return="habit_tracker")
        failure_patterns = {
            "recent_outcomes": [{"template": "weekly_planner"}],
        }
        result = await asyncio.wait_for(
            agent._select_template_llm("wellness", "printable_pdf", None, failure_patterns),
            timeout=5,
        )
        assert result == "habit_tracker"

    @pytest.mark.asyncio
    async def test_with_failure_patterns_known_issues(self):
        agent = _make_selection_agent(llm_return="goal_planner")
        failure_patterns = {
            "known_issues": ["Page count mismatch for weekly_planner"],
        }
        result = await asyncio.wait_for(
            agent._select_template_llm("productivity", "printable_pdf", None, failure_patterns),
            timeout=5,
        )
        assert result == "goal_planner"

    @pytest.mark.asyncio
    async def test_with_failure_patterns_low_ctr_combos(self):
        agent = _make_selection_agent(llm_return="reading_log")
        failure_patterns = {
            "low_ctr_combos": [{"template": "weekly_planner"}],
        }
        result = await asyncio.wait_for(
            agent._select_template_llm("books", "printable_pdf", None, failure_patterns),
            timeout=5,
        )
        assert result == "reading_log"

    @pytest.mark.asyncio
    async def test_svg_bundle_product_type(self):
        agent = _make_selection_agent(llm_return="icon_set")
        result = await asyncio.wait_for(
            agent._select_template_llm("icons", "svg_bundle", None),
            timeout=5,
        )
        assert result == "icon_set"

    @pytest.mark.asyncio
    async def test_unknown_product_type_falls_back_to_weekly_planner(self):
        agent = _make_selection_agent(llm_return="anything")
        result = await asyncio.wait_for(
            agent._select_template_llm("niche", "unknown_type", None),
            timeout=5,
        )
        # AVAILABLE_TEMPLATES.get(product_type, ["weekly_planner"])[0] = "weekly_planner"
        assert result == "weekly_planner"


class TestShouldIncludeDates:
    @pytest.mark.asyncio
    async def test_wall_art_quote_returns_false_immediately(self):
        agent = _make_selection_agent()
        result = await asyncio.wait_for(
            agent._should_include_dates("wall_art_quote", "art"),
            timeout=5,
        )
        assert result is False
        agent._call_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_botanical_print_returns_false_immediately(self):
        agent = _make_selection_agent()
        result = await asyncio.wait_for(
            agent._should_include_dates("botanical_print", "art"),
            timeout=5,
        )
        assert result is False
        agent._call_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_icon_set_returns_false_immediately(self):
        agent = _make_selection_agent()
        result = await asyncio.wait_for(
            agent._should_include_dates("icon_set", "design"),
            timeout=5,
        )
        assert result is False
        agent._call_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_planner_llm_returns_dated(self):
        agent = _make_selection_agent(llm_return="dated")
        result = await asyncio.wait_for(
            agent._should_include_dates("weekly_planner", "wellness"),
            timeout=5,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_planner_llm_returns_undated(self):
        agent = _make_selection_agent(llm_return="undated")
        result = await asyncio.wait_for(
            agent._should_include_dates("weekly_planner", "wellness"),
            timeout=5,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_planner_llm_raises_returns_false(self):
        agent = _make_selection_agent()
        agent._call_llm = AsyncMock(side_effect=RuntimeError("LLM down"))
        result = await asyncio.wait_for(
            agent._should_include_dates("weekly_planner", "wellness"),
            timeout=5,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_all_no_date_templates_return_false(self):
        no_date = [
            "wall_art_quote", "botanical_print", "abstract_art",
            "watercolor_print", "minimalist_poster", "vintage_poster",
            "icon_set", "pattern_bundle", "monogram_set",
            "clipart_bundle", "frame_bundle",
        ]
        agent = _make_selection_agent()
        for template in no_date:
            result = await asyncio.wait_for(
                agent._should_include_dates(template, "test"),
                timeout=5,
            )
            assert result is False, f"Expected False for template {template}"
        agent._call_llm.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# generators_mixin.py
# ─────────────────────────────────────────────────────────────────────────────


class TestNotifyTelegram:
    @pytest.mark.asyncio
    async def test_no_broadcast_set_does_nothing(self):
        agent = _make_generators_agent()
        agent._telegram_broadcast = None
        # Should not raise
        await asyncio.wait_for(agent._notify_telegram("hello"), timeout=5)

    @pytest.mark.asyncio
    async def test_broadcast_called_with_message(self):
        agent = _make_generators_agent()
        mock_broadcast = AsyncMock()
        agent._telegram_broadcast = mock_broadcast
        await asyncio.wait_for(agent._notify_telegram("test message"), timeout=5)
        mock_broadcast.assert_called_once_with("test message")

    @pytest.mark.asyncio
    async def test_broadcast_raises_silently_swallowed(self):
        agent = _make_generators_agent()
        agent._telegram_broadcast = AsyncMock(side_effect=RuntimeError("Telegram down"))
        # Must not raise
        await asyncio.wait_for(agent._notify_telegram("msg"), timeout=5)


class TestRunSvgBundle:
    def _make_svg_task(self, extra_input: dict | None = None) -> AgentTask:
        data = {
            "niche": "geometric art",
            "num_variants": 1,
            "num_files": 5,
            "color_schemes": ["neutral"],
            **(extra_input or {}),
        }
        return _make_task("task-svg-01", data)

    @pytest.mark.asyncio
    async def test_success_returns_completed(self, tmp_path):
        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path
        svg_paths = [tmp_path / "test.svg"]
        agent._svg_gen.generate_bundle = AsyncMock(return_value=svg_paths)

        task = self._make_svg_task()
        norm_input = {
            "niche": "geometric art",
            "svg_type": "geometric",
            "complexity": 2,
            "quote": "",
            "color_variants": [],
        }
        result = await asyncio.wait_for(
            agent._run_svg_bundle(task, norm_input, None),
            timeout=10,
        )
        assert result.status == TaskStatus.COMPLETED
        assert result.output_data["product_type"] == "svg_bundle"
        assert result.output_data["num_files"] == 1

    @pytest.mark.asyncio
    async def test_success_with_pq_task_id(self, tmp_path):
        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path
        svg_paths = [tmp_path / "a.svg", tmp_path / "b.svg"]
        agent._svg_gen.generate_bundle = AsyncMock(return_value=svg_paths)

        pq_svc_mock = MagicMock()
        pq_svc_mock.set_design_started = AsyncMock()
        pq_svc_mock.set_files_generated = AsyncMock()

        with patch(
            "apps.backend.agents._design.generators_mixin._PQService",
            return_value=pq_svc_mock,
        ):
            task = self._make_svg_task()
            norm_input = {
                "niche": "art",
                "production_queue_task_id": "pq-123",
                "svg_type": "geometric",
                "complexity": 2,
                "quote": "",
                "color_variants": [],
            }
            result = await asyncio.wait_for(
                agent._run_svg_bundle(task, norm_input, None),
                timeout=10,
            )
        assert result.status == TaskStatus.COMPLETED
        pq_svc_mock.set_design_started.assert_called_once_with("pq-123")
        pq_svc_mock.set_files_generated.assert_called_once()

    @pytest.mark.asyncio
    async def test_svg_gen_raises_returns_failed(self, tmp_path):
        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path
        agent._svg_gen.generate_bundle = AsyncMock(side_effect=RuntimeError("SVG gen error"))

        task = self._make_svg_task()
        norm_input = {
            "niche": "geometric art",
            "svg_type": "geometric",
            "complexity": 2,
            "quote": "",
            "color_variants": [],
        }
        result = await asyncio.wait_for(
            agent._run_svg_bundle(task, norm_input, None),
            timeout=10,
        )
        assert result.status == TaskStatus.FAILED
        assert "SVG generation failed" in result.output_data["error"]

    @pytest.mark.asyncio
    async def test_svg_gen_raises_with_pq_task_id_sets_failed(self, tmp_path):
        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path
        agent._svg_gen.generate_bundle = AsyncMock(side_effect=RuntimeError("SVG error"))

        pq_svc_mock = MagicMock()
        pq_svc_mock.set_design_started = AsyncMock()
        pq_svc_mock.set_failed_by_task_id = AsyncMock()

        with patch(
            "apps.backend.agents._design.generators_mixin._PQService",
            return_value=pq_svc_mock,
        ):
            task = self._make_svg_task()
            norm_input = {
                "niche": "art",
                "production_queue_task_id": "pq-456",
                "svg_type": "geometric",
                "complexity": 2,
                "quote": "",
                "color_variants": [],
            }
            result = await asyncio.wait_for(
                agent._run_svg_bundle(task, norm_input, None),
                timeout=10,
            )
        assert result.status == TaskStatus.FAILED
        pq_svc_mock.set_failed_by_task_id.assert_called_once()


class TestRunDigitalArt:
    def _make_art_input(self, extra: dict | None = None) -> dict:
        return {
            "niche": "wellness art",
            "num_variants": 1,
            "color_schemes": ["neutral"],
            "art_type": "wall_art",
            "style_preset": "minimal",
            "section_key": "",
            "colors": {},
            "quote": "",
            **(extra or {}),
        }

    @pytest.mark.asyncio
    async def test_success_returns_completed(self, tmp_path):
        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path

        # Fake PNG that passes stat() calls
        fake_png = tmp_path / "rc2_art.png"
        fake_png.write_bytes(b"PNG")
        agent._image_gen.generate_digital_art = AsyncMock(return_value=fake_png)

        # identity = None → standard brief (no AGT-4)
        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=None)

        with patch(
            "apps.backend.agents._design.generators_mixin._SIService",
            return_value=si_mock,
        ):
            task = _make_task("task-art-01")
            result = await asyncio.wait_for(
                agent._run_digital_art(task, self._make_art_input(), None),
                timeout=10,
            )
        assert result.status == TaskStatus.COMPLETED
        assert result.output_data["product_type"] == "digital_art_png"

    @pytest.mark.asyncio
    async def test_all_variants_fail_returns_failed(self, tmp_path):
        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path
        agent._image_gen.generate_digital_art = AsyncMock(
            side_effect=RuntimeError("Image gen failed")
        )

        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=None)

        with patch(
            "apps.backend.agents._design.generators_mixin._SIService",
            return_value=si_mock,
        ):
            task = _make_task("task-art-02")
            result = await asyncio.wait_for(
                agent._run_digital_art(task, self._make_art_input(), None),
                timeout=10,
            )
        assert result.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_placeholder_provider_reduces_confidence(self, tmp_path):
        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path
        agent._image_gen.provider_name = "placeholder"

        fake_png = tmp_path / "rc2_art_ph.png"
        fake_png.write_bytes(b"PNG")
        agent._image_gen.generate_digital_art = AsyncMock(return_value=fake_png)

        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=None)

        with patch(
            "apps.backend.agents._design.generators_mixin._SIService",
            return_value=si_mock,
        ):
            task = _make_task("task-art-03")
            result = await asyncio.wait_for(
                agent._run_digital_art(task, self._make_art_input(), None),
                timeout=10,
            )
        assert result.status == TaskStatus.COMPLETED
        assert result.confidence < 1.0

    @pytest.mark.asyncio
    async def test_with_pq_task_id_sets_files_generated(self, tmp_path):
        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path

        fake_png = tmp_path / "rc2_art_pq.png"
        fake_png.write_bytes(b"PNG")
        agent._image_gen.generate_digital_art = AsyncMock(return_value=fake_png)

        pq_mock = MagicMock()
        pq_mock.set_design_started = AsyncMock()
        pq_mock.set_files_generated = AsyncMock()

        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=None)

        with patch(
            "apps.backend.agents._design.generators_mixin._PQService",
            return_value=pq_mock,
        ), patch(
            "apps.backend.agents._design.generators_mixin._SIService",
            return_value=si_mock,
        ):
            task = _make_task("task-art-pq")
            inp = self._make_art_input({"production_queue_task_id": "pq-art-1"})
            result = await asyncio.wait_for(
                agent._run_digital_art(task, inp, None),
                timeout=10,
            )
        assert result.status == TaskStatus.COMPLETED
        pq_mock.set_files_generated.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_pq_task_id_and_all_failures_sets_failed(self, tmp_path):
        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path
        agent._image_gen.generate_digital_art = AsyncMock(
            side_effect=RuntimeError("gen fail")
        )

        pq_mock = MagicMock()
        pq_mock.set_design_started = AsyncMock()
        pq_mock.set_failed_by_task_id = AsyncMock()

        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=None)

        with patch(
            "apps.backend.agents._design.generators_mixin._PQService",
            return_value=pq_mock,
        ), patch(
            "apps.backend.agents._design.generators_mixin._SIService",
            return_value=si_mock,
        ):
            task = _make_task("task-art-fail")
            inp = self._make_art_input({"production_queue_task_id": "pq-fail-1"})
            result = await asyncio.wait_for(
                agent._run_digital_art(task, inp, None),
                timeout=10,
            )
        assert result.status == TaskStatus.FAILED
        pq_mock.set_failed_by_task_id.assert_called_once()


class TestGenerateShopDescription:
    @pytest.mark.asyncio
    async def test_returns_description_text(self):
        agent = _make_generators_agent()

        fake_msg = MagicMock()
        fake_msg.content = [MagicMock()]
        fake_msg.content[0].text = "  Shop description text  "

        fake_client = MagicMock()
        fake_client.messages = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=fake_msg)

        identity = MagicMock()
        identity.aesthetic_name = "minimalist chic"
        identity.palette_primary = "#FFFFFF"
        identity.palette_secondary = "#000000"
        identity.palette_accent = "#FF0000"
        identity.tone = "professional"
        identity.mockup_style = "flat_lay"

        with patch(
            "apps.backend.agents._design.generators_mixin._anthropic.AsyncAnthropic",
            return_value=fake_client,
        ):
            result = await asyncio.wait_for(
                agent.generate_shop_description(identity),
                timeout=10,
            )
        assert result == "Shop description text"
        fake_client.messages.create.assert_called_once()
