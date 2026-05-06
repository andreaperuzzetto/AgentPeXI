"""A.1 AGT-3: verifica LADDER_PROMPTS integration nel _build_seo_system_prompt."""
from __future__ import annotations

import pytest


class TestLadderContextInPrompt:

    def test_ladder_prompts_exists_at_module_level(self):
        from apps.backend.agents._publisher._seo_mixin import LADDER_PROMPTS
        assert isinstance(LADDER_PROMPTS, dict)
        assert "tripwire" in LADDER_PROMPTS
        assert "core" in LADDER_PROMPTS
        assert "core_premium" in LADDER_PROMPTS
        assert "bundle" in LADDER_PROMPTS

    def test_tripwire_title_prefix(self):
        from apps.backend.agents._publisher._seo_mixin import LADDER_PROMPTS
        assert LADDER_PROMPTS["tripwire"]["title_prefix"] == "Printable"

    def test_bundle_contains_keywords(self):
        from apps.backend.agents._publisher._seo_mixin import LADDER_PROMPTS
        bundle_contains = LADDER_PROMPTS["bundle"]["title_contains"]
        assert "Bundle" in bundle_contains or "Complete Set" in bundle_contains

    def test_build_ladder_context_tripwire_mentions_printable(self):
        from apps.backend.agents._publisher._seo_mixin import _SeoMixin

        mixin = _SeoMixin.__new__(_SeoMixin)
        ctx = mixin._build_ladder_context("tripwire")
        assert "Printable" in ctx
        assert "tripwire" in ctx.lower() or "price" in ctx.lower()

    def test_build_ladder_context_bundle_mentions_bundle(self):
        from apps.backend.agents._publisher._seo_mixin import _SeoMixin

        mixin = _SeoMixin.__new__(_SeoMixin)
        ctx = mixin._build_ladder_context("bundle")
        assert "Bundle" in ctx or "Complete Set" in ctx

    def test_build_ladder_context_core_returns_neutral(self):
        from apps.backend.agents._publisher._seo_mixin import _SeoMixin

        mixin = _SeoMixin.__new__(_SeoMixin)
        ctx = mixin._build_ladder_context("core")
        assert isinstance(ctx, str)
        assert "Printable" not in ctx

    def test_system_prompt_includes_ladder_for_tripwire(self):
        from apps.backend.agents._publisher._seo_mixin import _SeoMixin
        from unittest.mock import MagicMock

        mixin = _SeoMixin.__new__(_SeoMixin)
        mixin._get_seasonal_context = MagicMock(return_value={"keywords": [], "season": "spring"})

        prompt = mixin._build_seo_system_prompt({}, product_tier="tripwire")
        assert "Printable" in prompt
        assert "tripwire" in prompt.lower() or "LADDER" in prompt

    def test_system_prompt_includes_cro_structure(self):
        from apps.backend.agents._publisher._seo_mixin import _SeoMixin
        from unittest.mock import MagicMock

        mixin = _SeoMixin.__new__(_SeoMixin)
        mixin._get_seasonal_context = MagicMock(return_value={"keywords": [], "season": "spring"})

        prompt = mixin._build_seo_system_prompt({})
        assert "160" in prompt or "above the fold" in prompt.lower() or "Above the fold" in prompt

    def test_system_prompt_includes_audience_formula(self):
        from apps.backend.agents._publisher._seo_mixin import _SeoMixin
        from unittest.mock import MagicMock

        mixin = _SeoMixin.__new__(_SeoMixin)
        mixin._get_seasonal_context = MagicMock(return_value={"keywords": [], "season": "spring"})

        prompt = mixin._build_seo_system_prompt({})
        assert "Audience Benefit" in prompt or "benefit" in prompt.lower()

    def test_system_prompt_no_keyword_stuffing_rule(self):
        from apps.backend.agents._publisher._seo_mixin import _SeoMixin
        from unittest.mock import MagicMock

        mixin = _SeoMixin.__new__(_SeoMixin)
        mixin._get_seasonal_context = MagicMock(return_value={"keywords": [], "season": "spring"})

        prompt = mixin._build_seo_system_prompt({})
        assert "keyword stuffing" in prompt.lower() or "8+" in prompt
