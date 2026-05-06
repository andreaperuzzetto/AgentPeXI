"""A.0: verifica _DISCOVERY_CATEGORIES_BY_SECTION e _TREND_KEYWORDS."""
from __future__ import annotations
import pytest


class TestDiscoveryCategoriesBySection:

    def test_attribute_exists(self):
        from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
        assert hasattr(_ResearchDiscoveryMixin, "_DISCOVERY_CATEGORIES_BY_SECTION")

    def test_old_attribute_removed(self):
        from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
        assert not hasattr(_ResearchDiscoveryMixin, "_DISCOVERY_CATEGORIES"), \
            "_DISCOVERY_CATEGORIES should be replaced"

    def test_four_sections(self):
        from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
        sections = _ResearchDiscoveryMixin._DISCOVERY_CATEGORIES_BY_SECTION
        assert len(sections) == 4

    def test_six_queries_per_section(self):
        from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
        sections = _ResearchDiscoveryMixin._DISCOVERY_CATEGORIES_BY_SECTION
        for key, queries in sections.items():
            assert len(queries) == 6, f"Section '{key}' has {len(queries)} queries, expected 6"

    def test_section_keys(self):
        from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
        sections = _ResearchDiscoveryMixin._DISCOVERY_CATEGORIES_BY_SECTION
        expected_keys = {"party_celebrations", "wellness_self_care", "planners_organizers", "kids_learning"}
        assert set(sections.keys()) == expected_keys

    def test_queries_are_buyer_persona_style(self):
        from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
        sections = _ResearchDiscoveryMixin._DISCOVERY_CATEGORIES_BY_SECTION
        for key, queries in sections.items():
            for q in queries:
                assert len(q.split()) > 4, \
                    f"Query '{q}' in section '{key}' too short for buyer-persona style"

    def test_trend_keywords_attribute_exists(self):
        from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
        assert hasattr(_ResearchDiscoveryMixin, "_TREND_KEYWORDS")

    def test_trend_keywords_count(self):
        from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
        assert len(_ResearchDiscoveryMixin._TREND_KEYWORDS) == 4

    def test_seasonal_map_untouched(self):
        """_SEASONAL_MAP must still exist and be a dict (not modified)."""
        from apps.backend.agents._research.discovery_mixin import _ResearchDiscoveryMixin
        assert hasattr(_ResearchDiscoveryMixin, "_SEASONAL_MAP")
        assert isinstance(_ResearchDiscoveryMixin._SEASONAL_MAP, dict)
        assert len(_ResearchDiscoveryMixin._SEASONAL_MAP) > 0
