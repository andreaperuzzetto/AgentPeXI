"""A.1: verifica section lookup nel publish flow."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_svc(section_id=None, suggest_result=(None, None)):
    """Helper: crea mock EtsySectionsService con comportamento configurabile."""
    svc = MagicMock()
    svc.get_section_for_niche = AsyncMock(return_value=section_id)
    svc.suggest_section_for_niche = AsyncMock(return_value=suggest_result)
    svc.map_niche = AsyncMock()
    svc.add_to_uncategorized = AsyncMock()
    svc.update_section_listing_count = AsyncMock()
    return svc


class TestPublisherSectionLookup:

    @pytest.mark.asyncio
    async def test_uses_mapped_section_id(self):
        """Se la niche ha una sezione mappata, _resolve_section_id ritorna section_id."""
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin

        publisher = MagicMock()
        publisher.memory = MagicMock()
        publisher.memory.get_db = AsyncMock(return_value=MagicMock())

        svc_mock = _make_svc(section_id="s1")

        with patch("apps.backend.agents._publisher._publish_mixin.EtsySectionsService", return_value=svc_mock):
            result = await _PublishMixin._resolve_section_id(publisher, "wedding_planner_printable")

        assert result == "s1"
        svc_mock.get_section_for_niche.assert_called_once_with("wedding_planner_printable")
        svc_mock.add_to_uncategorized.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_maps_when_high_confidence(self):
        """Se confidence ≥ 0.5, la niche viene auto-mappata e section_id viene usato."""
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin

        publisher = MagicMock()
        publisher.memory = MagicMock()
        publisher.memory.get_db = AsyncMock(return_value=MagicMock())

        svc_mock = _make_svc(section_id=None, suggest_result=("s2", 0.75))

        with patch("apps.backend.agents._publisher._publish_mixin.EtsySectionsService", return_value=svc_mock):
            result = await _PublishMixin._resolve_section_id(publisher, "wedding_invitation_printable")

        assert result == "s2"
        svc_mock.map_niche.assert_called_once_with(
            "wedding_invitation_printable", "s2",
            mapped_by="auto", auto_confidence=0.75,
        )
        svc_mock.add_to_uncategorized.assert_not_called()

    @pytest.mark.asyncio
    async def test_adds_to_uncategorized_when_no_match(self):
        """Se nessun match, va in uncategorized e ritorna None."""
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin

        publisher = MagicMock()
        publisher.memory = MagicMock()
        publisher.memory.get_db = AsyncMock(return_value=MagicMock())

        svc_mock = _make_svc(section_id=None, suggest_result=(None, None))

        with patch("apps.backend.agents._publisher._publish_mixin.EtsySectionsService", return_value=svc_mock):
            result = await _PublishMixin._resolve_section_id(publisher, "abstract_niche_xyz")

        assert result is None
        svc_mock.add_to_uncategorized.assert_called_once_with(
            "abstract_niche_xyz",
            suggested_section_id=None,
            suggested_confidence=None,
        )

    @pytest.mark.asyncio
    async def test_low_confidence_goes_to_uncategorized(self):
        """Se confidence < 0.5, la niche va in uncategorized con suggestion come hint."""
        from apps.backend.agents._publisher._publish_mixin import _PublishMixin

        publisher = MagicMock()
        publisher.memory = MagicMock()
        publisher.memory.get_db = AsyncMock(return_value=MagicMock())

        svc_mock = _make_svc(section_id=None, suggest_result=("s1", 0.25))

        with patch("apps.backend.agents._publisher._publish_mixin.EtsySectionsService", return_value=svc_mock):
            result = await _PublishMixin._resolve_section_id(publisher, "vague_planner_thing")

        assert result is None
        svc_mock.map_niche.assert_not_called()
        svc_mock.add_to_uncategorized.assert_called_once_with(
            "vague_planner_thing",
            suggested_section_id="s1",
            suggested_confidence=0.25,
        )
