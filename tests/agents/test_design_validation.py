"""Coverage tests for _design/validation_mixin.py + generators_mixin.py (residual).

Targets:
- validation_mixin.py: ≥90%  (covers _lookup_failure_patterns + all methods)
- generators_mixin.py: ≥65%  (covers run() body + AGT-4 paths + shop_assets errors)

Mock contract follows RC3 (test_llm_tools_mixin_coverage.py).
Do NOT duplicate tests already present in test_design_coverage.py.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.backend.agents._design.generators_mixin import _DesignGeneratorsMixin
from apps.backend.agents._design.validation_mixin import _DesignValidationMixin
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus
from apps.backend.core.shop_identity_service import ShopIdentityRecord


# ─── Helper factories ─────────────────────────────────────────────────────────


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


def _make_task(task_id: str = "task-vld-01", input_data: dict | None = None) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        agent_name="DesignAgent",
        input_data=input_data or {},
    )


def _make_validation_agent():
    """Minimal agent with _DesignValidationMixin only."""

    class _Agent(_DesignValidationMixin):
        pass

    agent = _Agent()
    agent.memory = MagicMock()
    agent.memory.query_chromadb_recent = AsyncMock(return_value=[])
    return agent


def _make_generators_agent(extra_attrs: dict | None = None):
    """Minimal agent with _DesignGeneratorsMixin + all required mocks."""

    class _Agent(_DesignGeneratorsMixin):
        pass

    agent = _Agent()
    agent.name = "design_residue_agent"
    agent._log_step = AsyncMock(return_value=42)
    agent._call_llm = AsyncMock(return_value="minimal")
    agent._call_tool = AsyncMock(return_value={})
    agent._telegram_broadcast = None

    agent.storage = MagicMock()
    agent.storage.is_available = MagicMock(return_value=True)
    agent.storage.base_path = Path("/tmp/test_design_residue")

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


def _make_run_agent(tmp_path: Path, product_type: str = "printable_pdf"):
    """Agent ready for run() tests with all internal methods mocked."""
    agent = _make_generators_agent()
    agent.storage.base_path = tmp_path

    normalized: dict = {
        "niche": "wellness",
        "product_type": product_type,
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
    agent._resolve_color_scheme_niche_aware = AsyncMock(return_value={
        "primary": "#4A4A4A",
        "secondary": "#F5F5F5",
        "accent": "#8B6914",
        "bg": "#FFFFFF",
        "text": "#1A1A1A",
    })
    return agent, normalized


# ─── TestValidationMixin ──────────────────────────────────────────────────────


class TestValidationMixin:
    """Tests for _DesignValidationMixin — _validate_and_normalize_input,
    _extract_research_context, and _lookup_failure_patterns."""

    # ── _validate_and_normalize_input ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_missing_niche_returns_error(self):
        agent = _make_validation_agent()
        result, err = await asyncio.wait_for(
            agent._validate_and_normalize_input({"product_type": "printable_pdf"}),
            timeout=5,
        )
        assert result is None
        assert "niche" in err

    @pytest.mark.asyncio
    async def test_missing_product_type_returns_error(self):
        agent = _make_validation_agent()
        result, err = await asyncio.wait_for(
            agent._validate_and_normalize_input({"niche": "wellness"}),
            timeout=5,
        )
        assert result is None
        assert "product_type" in err

    @pytest.mark.asyncio
    async def test_invalid_product_type_returns_error(self):
        agent = _make_validation_agent()
        result, err = await asyncio.wait_for(
            agent._validate_and_normalize_input(
                {"niche": "wellness", "product_type": "invalid_type"}
            ),
            timeout=5,
        )
        assert result is None
        assert "Invalid product_type" in err

    @pytest.mark.asyncio
    async def test_valid_input_returns_normalized_data(self):
        agent = _make_validation_agent()
        data = {"niche": "wellness", "product_type": "printable_pdf"}
        result, err = await asyncio.wait_for(
            agent._validate_and_normalize_input(data),
            timeout=5,
        )
        assert err is None
        assert result is not None
        assert result["niche"] == "wellness"

    @pytest.mark.asyncio
    async def test_invalid_template_cleared(self):
        agent = _make_validation_agent()
        data = {
            "niche": "wellness",
            "product_type": "printable_pdf",
            "template": "not_a_real_template",
        }
        result, err = await asyncio.wait_for(
            agent._validate_and_normalize_input(data),
            timeout=5,
        )
        assert err is None
        assert result["template"] is None

    @pytest.mark.asyncio
    async def test_valid_template_retained(self):
        agent = _make_validation_agent()
        data = {
            "niche": "wellness",
            "product_type": "printable_pdf",
            "template": "weekly_planner",
        }
        result, err = await asyncio.wait_for(
            agent._validate_and_normalize_input(data),
            timeout=5,
        )
        assert err is None
        assert result["template"] == "weekly_planner"

    @pytest.mark.asyncio
    async def test_num_variants_below_1_normalized_to_2(self):
        agent = _make_validation_agent()
        data = {"niche": "art", "product_type": "printable_pdf", "num_variants": 0}
        result, _ = await asyncio.wait_for(
            agent._validate_and_normalize_input(data),
            timeout=5,
        )
        assert result["num_variants"] == 2

    @pytest.mark.asyncio
    async def test_num_variants_above_5_clamped_to_5(self):
        agent = _make_validation_agent()
        data = {"niche": "art", "product_type": "printable_pdf", "num_variants": 10}
        result, _ = await asyncio.wait_for(
            agent._validate_and_normalize_input(data),
            timeout=5,
        )
        assert result["num_variants"] == 5

    @pytest.mark.asyncio
    async def test_non_int_num_variants_normalized_to_2(self):
        agent = _make_validation_agent()
        data = {"niche": "art", "product_type": "printable_pdf", "num_variants": "bad"}
        result, _ = await asyncio.wait_for(
            agent._validate_and_normalize_input(data),
            timeout=5,
        )
        assert result["num_variants"] == 2

    @pytest.mark.asyncio
    async def test_empty_color_schemes_defaults_to_neutral_warm(self):
        agent = _make_validation_agent()
        data = {
            "niche": "art",
            "product_type": "printable_pdf",
            "num_variants": 2,
            "color_schemes": [],
        }
        result, err = await asyncio.wait_for(
            agent._validate_and_normalize_input(data),
            timeout=5,
        )
        assert err is None
        assert "neutral" in result["color_schemes"]
        assert "warm" in result["color_schemes"]

    @pytest.mark.asyncio
    async def test_color_schemes_truncated_to_num_variants(self):
        agent = _make_validation_agent()
        data = {
            "niche": "art",
            "product_type": "printable_pdf",
            "num_variants": 2,
            "color_schemes": ["neutral", "warm", "cool", "dark"],
        }
        result, _ = await asyncio.wait_for(
            agent._validate_and_normalize_input(data),
            timeout=5,
        )
        assert len(result["color_schemes"]) == 2

    # ── _extract_research_context ─────────────────────────────────────────

    def test_extract_research_context_no_research_returns_none(self):
        agent = _make_validation_agent()
        assert agent._extract_research_context({}) is None

    def test_extract_research_context_from_research_result(self):
        agent = _make_validation_agent()
        task_input = {
            "research_result": {
                "top_keywords": ["wellness", "yoga"],
                "market_insights": {"avg_price": "9.99", "competition_level": "low"},
                "confidence": 0.8,
            }
        }
        result = agent._extract_research_context(task_input)
        assert result is not None
        assert result["top_keywords"] == ["wellness", "yoga"]
        assert result["avg_price"] == "9.99"
        assert result["confidence"] == 0.8

    def test_extract_research_context_from_research_context_key(self):
        agent = _make_validation_agent()
        task_input = {
            "research_context": {
                "top_keywords": ["budget", "finance"],
                "market_insights": {},
                "confidence": 0.5,
            }
        }
        result = agent._extract_research_context(task_input)
        assert result is not None
        assert "budget" in result["top_keywords"]

    def test_extract_research_context_top_keywords_limited_to_10(self):
        agent = _make_validation_agent()
        task_input = {
            "research_result": {
                "top_keywords": [f"kw{i}" for i in range(20)],
                "market_insights": {},
                "confidence": 0.6,
            }
        }
        result = agent._extract_research_context(task_input)
        assert len(result["top_keywords"]) == 10

    def test_extract_research_context_returns_full_market_fields(self):
        agent = _make_validation_agent()
        task_input = {
            "research_result": {
                "top_keywords": [],
                "market_insights": {
                    "target_audience": "brides",
                    "gaps": ["eco packaging"],
                    "trending_styles": ["minimalist"],
                },
                "confidence": 0.75,
            }
        }
        result = agent._extract_research_context(task_input)
        assert result["target_audience"] == "brides"
        assert result["gaps"] == ["eco packaging"]
        assert result["trending_styles"] == ["minimalist"]

    # ── _lookup_failure_patterns ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_lookup_empty_results_returns_none(self):
        agent = _make_validation_agent()
        agent.memory.query_chromadb_recent = AsyncMock(return_value=[])
        result = await asyncio.wait_for(
            agent._lookup_failure_patterns("wellness", "weekly_planner"),
            timeout=5,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_with_failures_returns_known_issues_and_avoid(self):
        agent = _make_validation_agent()
        failures = [
            {"document": "PDF rendering broken", "metadata": {"failure_type": "pdf_invalid"}},
            {"document": "No conversion", "metadata": {"failure_type": "no_conversion"}},
        ]
        agent.memory.query_chromadb_recent = AsyncMock(side_effect=[failures, [], [], []])
        result = await asyncio.wait_for(
            agent._lookup_failure_patterns("wellness", "weekly_planner"),
            timeout=5,
        )
        assert result is not None
        assert "known_issues" in result
        assert len(result["known_issues"]) == 2
        assert "avoid" in result
        assert "pdf_invalid" in result["avoid"]

    @pytest.mark.asyncio
    async def test_lookup_failures_without_failure_type_no_avoid_entry(self):
        agent = _make_validation_agent()
        failures = [{"document": "issue", "metadata": {}}]
        agent.memory.query_chromadb_recent = AsyncMock(side_effect=[failures, [], [], []])
        result = await asyncio.wait_for(
            agent._lookup_failure_patterns("wellness", "weekly_planner"),
            timeout=5,
        )
        assert result is not None
        assert not result.get("avoid")

    @pytest.mark.asyncio
    async def test_lookup_with_outcomes_returns_structured_outcomes(self):
        agent = _make_validation_agent()
        outcomes = [
            {
                "document": "outcome1",
                "metadata": {
                    "preset": "minimal",
                    "template": "weekly_planner",
                    "color_scheme": "neutral",
                    "pdf_valid": "True",
                    "pages": "7",
                    "date": "2025-01-01",
                },
            }
        ]
        agent.memory.query_chromadb_recent = AsyncMock(side_effect=[[], outcomes, [], []])
        result = await asyncio.wait_for(
            agent._lookup_failure_patterns("wellness", "weekly_planner"),
            timeout=5,
        )
        assert result is not None
        assert "recent_outcomes" in result
        assert result["recent_outcomes"][0]["preset"] == "minimal"
        assert result["recent_outcomes"][0]["template"] == "weekly_planner"

    @pytest.mark.asyncio
    async def test_lookup_with_winners_returns_structured_winners(self):
        agent = _make_validation_agent()
        winners = [
            {
                "document": "winner1",
                "metadata": {
                    "template": "weekly_planner",
                    "color_scheme": "neutral",
                    "views": "500",
                    "sales": "30",
                    "date": "2025-02-01",
                },
            }
        ]
        agent.memory.query_chromadb_recent = AsyncMock(side_effect=[[], [], winners, []])
        result = await asyncio.wait_for(
            agent._lookup_failure_patterns("wellness", "weekly_planner"),
            timeout=5,
        )
        assert result is not None
        assert "winners" in result
        assert result["winners"][0]["template"] == "weekly_planner"
        assert result["winners"][0]["sales"] == "30"

    @pytest.mark.asyncio
    async def test_lookup_with_low_ctr_signals_returns_combos(self):
        agent = _make_validation_agent()
        low_ctr = [
            {"document": "ctr1", "metadata": {"template": "weekly_planner", "color_scheme": "dark"}},
            {"document": "ctr2", "metadata": {}},  # no template/color → skipped
        ]
        agent.memory.query_chromadb_recent = AsyncMock(side_effect=[[], [], [], low_ctr])
        result = await asyncio.wait_for(
            agent._lookup_failure_patterns("wellness", "weekly_planner"),
            timeout=5,
        )
        assert result is not None
        assert "low_ctr_combos" in result
        assert len(result["low_ctr_combos"]) == 1

    @pytest.mark.asyncio
    async def test_lookup_exception_returns_none(self):
        agent = _make_validation_agent()
        agent.memory.query_chromadb_recent = AsyncMock(side_effect=RuntimeError("DB error"))
        result = await asyncio.wait_for(
            agent._lookup_failure_patterns("wellness", "weekly_planner"),
            timeout=5,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_all_data_combined_returns_full_result(self):
        agent = _make_validation_agent()
        failures = [
            {"document": "issue1", "metadata": {"failure_type": "bad_pdf"}},
            {"document": "issue2", "metadata": {}},
        ]
        outcomes = [
            {"document": "o1", "metadata": {
                "preset": "decorative", "template": "t1", "color_scheme": "warm",
                "pdf_valid": "False", "pages": "3", "date": "2025-01-01",
            }}
        ]
        winners = [
            {"document": "w1", "metadata": {
                "template": "t2", "color_scheme": "cool",
                "views": "300", "sales": "15", "date": "2025-03-01",
            }}
        ]
        low_ctr = [{"document": "c1", "metadata": {"template": "t3", "color_scheme": "dark"}}]
        agent.memory.query_chromadb_recent = AsyncMock(
            side_effect=[failures, outcomes, winners, low_ctr]
        )
        result = await asyncio.wait_for(
            agent._lookup_failure_patterns("wellness", "weekly_planner"),
            timeout=5,
        )
        assert result is not None
        assert "known_issues" in result
        assert "recent_outcomes" in result
        assert "winners" in result
        assert "low_ctr_combos" in result

    @pytest.mark.asyncio
    async def test_lookup_only_color_scheme_in_low_ctr_meta(self):
        """Low-CTR entry with only color_scheme (no template) is still included."""
        agent = _make_validation_agent()
        low_ctr = [{"document": "c1", "metadata": {"color_scheme": "dark"}}]
        agent.memory.query_chromadb_recent = AsyncMock(side_effect=[[], [], [], low_ctr])
        result = await asyncio.wait_for(
            agent._lookup_failure_patterns("wellness", "weekly_planner"),
            timeout=5,
        )
        assert result is not None
        assert "low_ctr_combos" in result
        assert result["low_ctr_combos"][0]["color_scheme"] == "dark"


# ─── TestGeneratorsResidue ────────────────────────────────────────────────────


class TestGeneratorsResidue:
    """Residual coverage tests for generators_mixin.py.

    Targets: 111-388, 469-479, 486, 497-499, 514-541, 681-682, 687.
    Does NOT duplicate tests already in test_design_coverage.py.
    """

    def _make_art_input(self, extra: dict | None = None) -> dict:
        return {
            "niche": "wellness art",
            "num_variants": 1,
            "color_schemes": ["neutral"],
            "art_type": "wall_art",
            "style_preset": "minimal",
            "section_key": "wellness_self_care",
            "colors": {},
            "quote": "",
            **(extra or {}),
        }

    # ── run() — ShopIdentity guard (lines 111-129) ────────────────────────

    @pytest.mark.asyncio
    async def test_run_no_shop_identity_returns_failed(self, tmp_path):
        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path
        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=None)
        with patch("apps.backend.agents._design.generators_mixin._SIService", return_value=si_mock):
            task = _make_task("run-no-id", {"niche": "wellness", "product_type": "printable_pdf"})
            result = await asyncio.wait_for(agent.run(task), timeout=10)
        assert result.status == TaskStatus.FAILED
        assert result.output_data["error"] == "no_active_shop_identity"

    # ── run() — storage guard (lines 131-140) ────────────────────────────

    @pytest.mark.asyncio
    async def test_run_storage_unavailable_returns_failed(self, tmp_path):
        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path
        agent.storage.is_available = MagicMock(return_value=False)
        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=_make_real_identity())
        with patch("apps.backend.agents._design.generators_mixin._SIService", return_value=si_mock):
            task = _make_task("run-no-storage", {"niche": "wellness", "product_type": "printable_pdf"})
            result = await asyncio.wait_for(agent.run(task), timeout=10)
        assert result.status == TaskStatus.FAILED
        assert "Storage" in result.output_data["error"]

    # ── run() — validation failure (lines 142-152) ───────────────────────

    @pytest.mark.asyncio
    async def test_run_invalid_input_returns_failed(self, tmp_path):
        agent, _ = _make_run_agent(tmp_path)
        agent._validate_and_normalize_input = AsyncMock(
            return_value=(None, "Missing required field: niche")
        )
        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=_make_real_identity())
        with patch("apps.backend.agents._design.generators_mixin._SIService", return_value=si_mock):
            task = _make_task("run-invalid", {})
            result = await asyncio.wait_for(agent.run(task), timeout=10)
        assert result.status == TaskStatus.FAILED
        assert "Missing required field" in result.output_data["error"]

    # ── run() — routing to digital_art (lines 164-165) ───────────────────

    @pytest.mark.asyncio
    async def test_run_routes_to_digital_art(self, tmp_path):
        agent, _ = _make_run_agent(tmp_path, product_type="digital_art_png")
        expected = AgentResult(
            task_id="run-to-art",
            agent_name="design_residue_agent",
            status=TaskStatus.COMPLETED,
            output_data={"product_type": "digital_art_png"},
            confidence=1.0,
        )
        agent._run_digital_art = AsyncMock(return_value=expected)
        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=_make_real_identity())
        with patch("apps.backend.agents._design.generators_mixin._SIService", return_value=si_mock):
            task = _make_task("run-to-art", {"niche": "wellness"})
            result = await asyncio.wait_for(agent.run(task), timeout=10)
        assert result.status == TaskStatus.COMPLETED
        agent._run_digital_art.assert_called_once()

    # ── run() — routing to svg_bundle (lines 166-167) ────────────────────

    @pytest.mark.asyncio
    async def test_run_routes_to_svg_bundle(self, tmp_path):
        agent, _ = _make_run_agent(tmp_path, product_type="svg_bundle")
        expected = AgentResult(
            task_id="run-to-svg",
            agent_name="design_residue_agent",
            status=TaskStatus.COMPLETED,
            output_data={"product_type": "svg_bundle"},
            confidence=1.0,
        )
        agent._run_svg_bundle = AsyncMock(return_value=expected)
        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=_make_real_identity())
        with patch("apps.backend.agents._design.generators_mixin._SIService", return_value=si_mock):
            task = _make_task("run-to-svg", {"niche": "wellness"})
            result = await asyncio.wait_for(agent.run(task), timeout=10)
        assert result.status == TaskStatus.COMPLETED
        agent._run_svg_bundle.assert_called_once()

    # ── run() — PDF path, all variants fail (lines 168-348) ──────────────

    @pytest.mark.asyncio
    async def test_run_pdf_all_variants_fail_returns_failed(self, tmp_path):
        agent, _ = _make_run_agent(tmp_path, product_type="printable_pdf")
        agent._resolve_color_scheme_niche_aware = AsyncMock(
            side_effect=RuntimeError("resolve fail")
        )
        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=_make_real_identity())
        with patch("apps.backend.agents._design.generators_mixin._SIService", return_value=si_mock):
            task = _make_task("run-pdf-fail", {"niche": "wellness"})
            result = await asyncio.wait_for(agent.run(task), timeout=15)
        assert result.status == TaskStatus.FAILED
        assert "All variants failed" in result.output_data["error"]

    @pytest.mark.asyncio
    async def test_run_pdf_all_fail_with_pq_task_id_sets_failed(self, tmp_path):
        agent, normalized = _make_run_agent(tmp_path, product_type="printable_pdf")
        normalized_pq = {**normalized, "production_queue_task_id": "pq-pdf-fail-01"}
        agent._validate_and_normalize_input = AsyncMock(return_value=(normalized_pq, None))
        agent._resolve_color_scheme_niche_aware = AsyncMock(side_effect=RuntimeError("fail"))
        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=_make_real_identity())
        pq_mock = MagicMock()
        pq_mock.set_design_started = AsyncMock()
        pq_mock.set_failed_by_task_id = AsyncMock()
        with patch("apps.backend.agents._design.generators_mixin._SIService", return_value=si_mock), \
             patch("apps.backend.agents._design.generators_mixin._PQService", return_value=pq_mock):
            task = _make_task("run-pdf-pq-fail", {"niche": "wellness"})
            result = await asyncio.wait_for(agent.run(task), timeout=15)
        assert result.status == TaskStatus.FAILED
        pq_mock.set_design_started.assert_called_once_with("pq-pdf-fail-01")
        pq_mock.set_failed_by_task_id.assert_called_once()

    # ── run() — PDF success path (lines 350-388) ─────────────────────────

    @pytest.mark.asyncio
    async def test_run_pdf_success(self, tmp_path):
        agent, _ = _make_run_agent(tmp_path, product_type="printable_pdf")
        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=_make_real_identity())
        with patch("apps.backend.agents._design.generators_mixin._SIService", return_value=si_mock), \
             patch("apps.backend.agents._design.generators_mixin._count_pdf_pages", return_value=7), \
             patch(
                 "apps.backend.agents._design.generators_mixin._validate_pdf",
                 new=AsyncMock(return_value={"valid": True, "issues": []}),
             ), \
             patch(
                 "apps.backend.agents._design.generators_mixin._calculate_design_confidence",
                 return_value=(0.9, []),
             ):
            task = _make_task("run-pdf-ok", {"niche": "wellness"})
            result = await asyncio.wait_for(agent.run(task), timeout=15)
        assert result.status == TaskStatus.COMPLETED
        assert "variants" in result.output_data
        assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_run_pdf_success_with_pq_task_id(self, tmp_path):
        agent, normalized = _make_run_agent(tmp_path, product_type="printable_pdf")
        normalized_pq = {**normalized, "production_queue_task_id": "pq-pdf-ok-01"}
        agent._validate_and_normalize_input = AsyncMock(return_value=(normalized_pq, None))
        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=_make_real_identity())
        pq_mock = MagicMock()
        pq_mock.set_design_started = AsyncMock()
        pq_mock.set_files_generated = AsyncMock()
        with patch("apps.backend.agents._design.generators_mixin._SIService", return_value=si_mock), \
             patch("apps.backend.agents._design.generators_mixin._PQService", return_value=pq_mock), \
             patch("apps.backend.agents._design.generators_mixin._count_pdf_pages", return_value=7), \
             patch(
                 "apps.backend.agents._design.generators_mixin._validate_pdf",
                 new=AsyncMock(return_value={"valid": True, "issues": []}),
             ), \
             patch(
                 "apps.backend.agents._design.generators_mixin._calculate_design_confidence",
                 return_value=(0.9, []),
             ):
            task = _make_task("run-pdf-pq-ok", {"niche": "wellness"})
            result = await asyncio.wait_for(agent.run(task), timeout=15)
        assert result.status == TaskStatus.COMPLETED
        pq_mock.set_design_started.assert_called_once_with("pq-pdf-ok-01")
        pq_mock.set_files_generated.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_pdf_validation_failure_still_completes(self, tmp_path):
        """PDF variant succeeds despite validation=False; covers line 283 (warning log)."""
        agent, _ = _make_run_agent(tmp_path, product_type="printable_pdf")
        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=_make_real_identity())
        with patch("apps.backend.agents._design.generators_mixin._SIService", return_value=si_mock), \
             patch("apps.backend.agents._design.generators_mixin._count_pdf_pages", return_value=5), \
             patch(
                 "apps.backend.agents._design.generators_mixin._validate_pdf",
                 new=AsyncMock(return_value={"valid": False, "issues": ["pages mismatch"]}),
             ), \
             patch(
                 "apps.backend.agents._design.generators_mixin._calculate_design_confidence",
                 return_value=(0.7, ["validation failed"]),
             ):
            task = _make_task("run-pdf-val-fail", {"niche": "wellness"})
            result = await asyncio.wait_for(agent.run(task), timeout=15)
        assert result.status == TaskStatus.COMPLETED

    # ── _run_digital_art — AGT-4 paths (lines 469-479, 486, 497-499, 514-541) ─

    @pytest.mark.asyncio
    async def test_run_digital_art_agt4_real_identity_flat_lay(self, tmp_path):
        """Covers 469-479 (try block + dc_replace), 486 (prompt_override), 497-499
        (quality gate warning for small image), 514-541 (variant B full path)."""
        from PIL import Image as PILImage

        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path
        output_dir = tmp_path / "pending" / "task-agt4-flat"
        output_dir.mkdir(parents=True, exist_ok=True)

        png_a = output_dir / "wellness_art_art_1.png"
        png_b = output_dir / "wellness_art_art_1_b.png"
        # Small images (<2000px) → _verify_image_quality returns False → line 499 covered
        PILImage.new("RGB", (500, 500)).save(png_a)
        PILImage.new("RGB", (500, 500)).save(png_b)
        agent._image_gen.generate_digital_art = AsyncMock(side_effect=[png_a, png_b])

        real_identity = _make_real_identity(mockup_style="flat_lay")
        svc_mock = MagicMock()
        svc_mock.get_active = AsyncMock(return_value=real_identity)

        with patch(
            "apps.backend.core.shop_identity_service.ShopIdentityService",
            return_value=svc_mock,
        ):
            task = _make_task("task-agt4-flat")
            inp = self._make_art_input()
            result = await asyncio.wait_for(
                agent._run_digital_art(task, inp, None),
                timeout=10,
            )

        assert result.status == TaskStatus.COMPLETED
        variants = result.output_data["variants"]
        assert len(variants) == 1
        assert variants[0].get("agt4_enabled") is True
        assert variants[0].get("image_path_a") is not None
        assert "image_path_b" in variants[0]

    @pytest.mark.asyncio
    async def test_run_digital_art_agt4_lifestyle_swap_large_image(self, tmp_path):
        """Covers dc_replace lifestyle→flat_lay swap; large images cover lines 497-498
        (quality gate passes, no warning)."""
        from PIL import Image as PILImage

        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path
        output_dir = tmp_path / "pending" / "task-agt4-ls"
        output_dir.mkdir(parents=True, exist_ok=True)

        png_a = output_dir / "wellness_art_art_1.png"
        png_b = output_dir / "wellness_art_art_1_b.png"
        PILImage.new("RGB", (3000, 3000)).save(png_a)  # large → passes quality gate
        PILImage.new("RGB", (3000, 3000)).save(png_b)
        agent._image_gen.generate_digital_art = AsyncMock(side_effect=[png_a, png_b])

        real_identity = _make_real_identity(mockup_style="lifestyle")
        svc_mock = MagicMock()
        svc_mock.get_active = AsyncMock(return_value=real_identity)

        with patch(
            "apps.backend.core.shop_identity_service.ShopIdentityService",
            return_value=svc_mock,
        ):
            task = _make_task("task-agt4-ls")
            inp = self._make_art_input()
            result = await asyncio.wait_for(
                agent._run_digital_art(task, inp, None),
                timeout=10,
            )

        assert result.status == TaskStatus.COMPLETED
        assert result.output_data["variants"][0].get("agt4_enabled") is True

    @pytest.mark.asyncio
    async def test_run_digital_art_agt4_variant_b_fails_gracefully(self, tmp_path):
        """Covers lines 538-539: variant B generation raises → warning logged,
        image_path_b stays None."""
        from PIL import Image as PILImage

        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path
        output_dir = tmp_path / "pending" / "task-agt4-bfail"
        output_dir.mkdir(parents=True, exist_ok=True)

        png_a = output_dir / "wellness_art_art_1.png"
        PILImage.new("RGB", (3000, 3000)).save(png_a)
        # A succeeds, B raises → caught by except at line 538
        agent._image_gen.generate_digital_art = AsyncMock(
            side_effect=[png_a, RuntimeError("B gen failed")]
        )

        real_identity = _make_real_identity()
        svc_mock = MagicMock()
        svc_mock.get_active = AsyncMock(return_value=real_identity)

        with patch(
            "apps.backend.core.shop_identity_service.ShopIdentityService",
            return_value=svc_mock,
        ):
            task = _make_task("task-agt4-bfail")
            inp = self._make_art_input()
            result = await asyncio.wait_for(
                agent._run_digital_art(task, inp, None),
                timeout=10,
            )

        assert result.status == TaskStatus.COMPLETED
        variant = result.output_data["variants"][0]
        assert variant.get("agt4_enabled") is True
        assert variant.get("image_path_b") is None  # B failed → None

    @pytest.mark.asyncio
    async def test_run_digital_art_agt4_prompt_exception_falls_through(self, tmp_path):
        """Covers line 479: dc_replace fails on non-dataclass identity →
        except branch logged → falls through to standard brief (no agt4_enabled)."""
        agent = _make_generators_agent()
        agent.storage.base_path = tmp_path

        fake_png = tmp_path / "art_broken.png"
        fake_png.write_bytes(b"FAKE")
        agent._image_gen.generate_digital_art = AsyncMock(return_value=fake_png)

        # MagicMock is not a dataclass → dc_replace raises TypeError → except at line 478
        broken_identity = MagicMock()
        broken_identity.aesthetic_name = "minimal"
        broken_identity.palette_primary = "#FFF"
        broken_identity.palette_secondary = "#000"
        broken_identity.palette_accent = "#F00"
        broken_identity.mockup_style = "flat_lay"
        svc_mock = MagicMock()
        svc_mock.get_active = AsyncMock(return_value=broken_identity)

        with patch(
            "apps.backend.core.shop_identity_service.ShopIdentityService",
            return_value=svc_mock,
        ):
            task = _make_task("task-agt4-broken")
            inp = self._make_art_input()
            result = await asyncio.wait_for(
                agent._run_digital_art(task, inp, None),
                timeout=10,
            )

        # Even with broken identity, generation continues without AGT-4
        assert result.status == TaskStatus.COMPLETED

    # ── generate_shop_assets — error paths (lines 681-682, 687) ──────────

    @pytest.mark.asyncio
    async def test_shop_assets_non_numeric_id_raises_value_error(self):
        """Covers lines 681-682: non-numeric identity_id raises ValueError."""
        agent = _make_generators_agent()
        db_mock = MagicMock()
        with pytest.raises(ValueError, match="identity_id must be numeric"):
            await asyncio.wait_for(
                agent.generate_shop_assets("not_a_number", db_mock),
                timeout=5,
            )

    @pytest.mark.asyncio
    async def test_shop_assets_identity_not_found_raises_value_error(self):
        """Covers line 687: active identity is None → raises ValueError."""
        agent = _make_generators_agent()
        db_mock = MagicMock()
        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=None)
        with patch(
            "apps.backend.agents._design.generators_mixin._SIService",
            return_value=si_mock,
        ):
            with pytest.raises(ValueError, match="is not the active identity"):
                await asyncio.wait_for(
                    agent.generate_shop_assets("42", db_mock),
                    timeout=5,
                )

    @pytest.mark.asyncio
    async def test_shop_assets_identity_id_mismatch_raises_value_error(self):
        """Covers line 687: active identity.id != identity_id → raises ValueError."""
        agent = _make_generators_agent()
        db_mock = MagicMock()
        wrong_identity = ShopIdentityRecord(
            id=99,
            aesthetic_name="minimalist chic",
            palette_primary="#FFFFFF",
            palette_secondary="#000000",
            palette_accent="#FF0000",
            mockup_style="flat_lay",
            tone="professional",
            logo_path=None,
            banner_path=None,
            approved_at=None,
            approved_by="admin",
            is_active=True,
        )
        si_mock = MagicMock()
        si_mock.get_active = AsyncMock(return_value=wrong_identity)
        with patch(
            "apps.backend.agents._design.generators_mixin._SIService",
            return_value=si_mock,
        ):
            with pytest.raises(ValueError, match="is not the active identity"):
                await asyncio.wait_for(
                    agent.generate_shop_assets("42", db_mock),
                    timeout=5,
                )
