"""RF-FINAL-A — Coverage for residual lines in generators_mixin and presets.

Targets:
  generators_mixin.py  lines 317-325  (except block in generate_single_variant)
  generators_mixin.py  lines 533-534  (except block in _run_digital_art variant B quality gate)
  presets.py           lines 38-40    (else branch + except block in _register_fonts)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.backend.agents._design.generators_mixin import _DesignGeneratorsMixin
from apps.backend.core.models import AgentTask, TaskStatus
from apps.backend.core.shop_identity_service import ShopIdentityRecord


# ─── Shared factories (mirror test_design_validation.py contract) ────────────


def _make_generators_agent(extra_attrs: dict | None = None):
    class _Agent(_DesignGeneratorsMixin):
        pass

    agent = _Agent()
    agent.name = "design_final_a_agent"
    agent._log_step = AsyncMock(return_value=42)
    agent._call_llm = AsyncMock(return_value="minimal")
    agent._call_tool = AsyncMock(return_value={})
    agent._telegram_broadcast = None

    agent.storage = MagicMock()
    agent.storage.is_available = MagicMock(return_value=True)
    agent.storage.base_path = Path("/tmp/test_design_final_a")

    agent.memory = MagicMock()
    db_mock = MagicMock()
    agent.memory.get_db = AsyncMock(return_value=db_mock)

    agent._image_gen = MagicMock()
    agent._image_gen.is_available = True
    agent._image_gen.provider_name = "flux"
    agent._image_gen.generate_digital_art = AsyncMock(return_value=Path("/tmp/art.png"))

    agent._svg_gen = MagicMock()
    agent._svg_gen.generate_bundle = AsyncMock(return_value=[Path("/tmp/test.svg")])

    agent._pdf_gen = MagicMock()
    agent._pdf_gen.generate = MagicMock()

    agent._get_mock_mode = MagicMock(return_value=False)

    if extra_attrs:
        for k, v in extra_attrs.items():
            setattr(agent, k, v)
    return agent


def _make_task(task_id: str = "task-final-a", input_data: dict | None = None) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        agent_name="DesignAgent",
        input_data=input_data or {},
    )


def _make_run_agent(tmp_path: Path):
    """Agent ready for run() PDF tests with all internal methods mocked."""
    agent = _make_generators_agent()
    agent.storage.base_path = tmp_path

    normalized: dict = {
        "niche": "wellness",
        "product_type": "printable_pdf",
        "num_variants": 1,
        "color_schemes": ["neutral"],
        "size": "A4",
        "template": None,
    }

    agent._validate_and_normalize_input = AsyncMock(return_value=(normalized, None))
    agent._extract_research_context = MagicMock(return_value=None)
    agent._lookup_failure_patterns = AsyncMock(return_value=None)
    agent._select_template_llm = AsyncMock(return_value="weekly_planner")
    agent._select_preset = AsyncMock(return_value="minimal")
    agent._should_include_dates = AsyncMock(return_value=False)
    agent._resolve_color_scheme_niche_aware = AsyncMock(
        return_value={
            "primary": "#4A4A4A",
            "secondary": "#F5F5F5",
            "accent": "#8B6914",
            "bg": "#FFFFFF",
            "text": "#1A1A1A",
        }
    )
    return agent, normalized


def _make_real_identity(mockup_style: str = "flat_lay") -> ShopIdentityRecord:
    return ShopIdentityRecord(
        id=1,
        aesthetic_name="minimalist chic",
        palette_primary="#FFFFFF",
        palette_secondary="#000000",
        palette_accent="#FF0000",
        mockup_style=mockup_style,
        tone="professional",
        logo_path=None,
        banner_path=None,
        approved_at=None,
        approved_by="admin",
        is_active=True,
    )


# ─── generators_mixin lines 317-325 ─────────────────────────────────────────


class TestGenerateSingleVariantExceptBlock:
    """Lines 317-325: except Exception inside generate_single_variant try block.

    Scenario: _colors_to_scheme raises inside the try block → the except handler
    logs a warning, calls self._log_step with output_data={"error": str(e)},
    and returns None.  All variants return None → run() returns FAILED.
    """

    @pytest.mark.asyncio
    async def test_exception_inside_try_logs_and_returns_none(self, tmp_path):
        agent, _ = _make_run_agent(tmp_path)

        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=_make_real_identity())

        agent._notify_telegram = AsyncMock()

        with patch(
            "apps.backend.agents._design.generators_mixin._SIService",
            return_value=si_mock,
        ), patch(
            "apps.backend.agents._design.generators_mixin._colors_to_scheme",
            side_effect=Exception("color error"),
        ):
            task = _make_task("task-gsv-except")
            result = await asyncio.wait_for(agent.run(task), timeout=15)

        assert result.status == TaskStatus.FAILED
        assert "All variants failed" in result.output_data["error"]

        # _log_step must have been called with output_data={"error": "color error"}
        error_logged = any(
            c.kwargs.get("output_data", {}).get("error") == "color error"
            for c in agent._log_step.call_args_list
        )
        assert error_logged, (
            "Expected _log_step to be called with output_data={'error': 'color error'}, "
            f"got: {agent._log_step.call_args_list}"
        )

    @pytest.mark.asyncio
    async def test_exception_step_type_is_tool_call(self, tmp_path):
        """Verify the step_type argument passed to _log_step is 'tool_call'."""
        agent, _ = _make_run_agent(tmp_path)

        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=_make_real_identity())

        agent._notify_telegram = AsyncMock()

        with patch(
            "apps.backend.agents._design.generators_mixin._SIService",
            return_value=si_mock,
        ), patch(
            "apps.backend.agents._design.generators_mixin._colors_to_scheme",
            side_effect=Exception("boom"),
        ):
            task = _make_task("task-gsv-type")
            await asyncio.wait_for(agent.run(task), timeout=15)

        tool_call_logged = any(
            c.args[0] == "tool_call"
            for c in agent._log_step.call_args_list
            if c.args
        )
        assert tool_call_logged, "Expected _log_step to be called with step_type='tool_call'"


# ─── generators_mixin lines 533-534 ─────────────────────────────────────────


class TestRunDigitalArtVariantBQualityGateExcept:
    """Lines 533-534: PIL Image.open raises inside variant B quality gate.

    Scenario: path_b exists, PIL Image.open raises an OSError → the except
    block at line 533 swallows it (pass).  The overall result is still COMPLETED.
    """

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
    async def test_pil_open_raises_for_variant_b_is_swallowed(self, tmp_path):
        from PIL import Image as PILImage

        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path
        output_dir = tmp_path / "pending" / "task-vb-qg-exc"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Create real PNG files so path.exists() is True
        png_a = output_dir / "wellness_art_art_1.png"
        png_b = output_dir / "wellness_art_art_1_b.png"
        PILImage.new("RGB", (3000, 3000)).save(png_a)  # large → passes quality gate for A
        PILImage.new("RGB", (3000, 3000)).save(png_b)

        agent._image_gen.generate_digital_art = AsyncMock(side_effect=[png_a, png_b])

        real_identity = _make_real_identity(mockup_style="flat_lay")
        svc_mock = MagicMock()
        svc_mock.get_active = AsyncMock(return_value=real_identity)

        # PIL Image.open for path_b raises; leave path_a open working normally
        original_open = PILImage.open

        def patched_open(path, *args, **kwargs):
            if str(path).endswith("_b.png"):
                raise OSError("corrupted B image")
            return original_open(path, *args, **kwargs)

        with patch(
            "apps.backend.core.shop_identity_service.ShopIdentityService",
            return_value=svc_mock,
        ), patch("PIL.Image.open", side_effect=patched_open):
            task = _make_task("task-vb-qg-exc")
            result = await asyncio.wait_for(
                agent._run_digital_art(task, self._make_art_input(), None),
                timeout=10,
            )

        # Exception is swallowed — overall task still completes
        assert result.status == TaskStatus.COMPLETED
        variant = result.output_data["variants"][0]
        assert variant.get("agt4_enabled") is True
        # image_path_b should still be set (B path was returned before quality gate)
        assert "image_path_b" in variant


# ─── presets.py lines 38-40 ─────────────────────────────────────────────────


class TestRegisterFonts:
    """Lines 38-40 in presets.py — _register_fonts exception and else paths.

    Line 38: else branch when font files are absent (Path.exists → False).
    Lines 39-40: except block when registerFont raises despite files present.
    """

    def test_font_files_missing_covers_else_branch(self):
        """Line 38: font files don't exist → else branch sets registered[name]=False."""
        from apps.backend.agents._design.presets import _register_fonts

        with patch("pathlib.Path.exists", return_value=False):
            result = _register_fonts()

        assert set(result.keys()) == {"PlayfairDisplay", "Lato", "Raleway", "JosefinSans"}
        assert all(v is False for v in result.values())

    def test_register_font_raises_covers_except_block(self):
        """Lines 39-40: pdfmetrics.registerFont raises → except sets registered[name]=False."""
        from apps.backend.agents._design.presets import _register_fonts

        with patch(
            "apps.backend.agents._design.presets.pdfmetrics.registerFont",
            side_effect=Exception("font error"),
        ), patch("pathlib.Path.exists", return_value=True):
            result = _register_fonts()

        assert set(result.keys()) == {"PlayfairDisplay", "Lato", "Raleway", "JosefinSans"}
        assert all(v is False for v in result.values())

    def test_register_font_exception_does_not_propagate(self):
        """Verify the exception is fully swallowed — no raise from _register_fonts."""
        from apps.backend.agents._design.presets import _register_fonts

        try:
            with patch(
                "apps.backend.agents._design.presets.pdfmetrics.registerFont",
                side_effect=Exception("font error"),
            ), patch("pathlib.Path.exists", return_value=True):
                _register_fonts()
        except Exception as exc:
            pytest.fail(f"_register_fonts propagated an exception: {exc}")
