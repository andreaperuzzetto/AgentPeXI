"""Tests for generate_shop_description()."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from apps.backend.core.shop_identity_service import ShopIdentityRecord


def _make_identity() -> ShopIdentityRecord:
    return ShopIdentityRecord(
        id=1, aesthetic_name="Warm Celebration Studio",
        palette_primary="#C9A84C", palette_secondary="#F2D0C4", palette_accent="#FAF7F2",
        mockup_style="flat_lay", tone="warm and celebratory, aspirational but accessible",
        logo_path=None, banner_path=None, approved_at=None, approved_by="andrea", is_active=True,
    )


@pytest.mark.asyncio
async def test_generate_shop_description_returns_string():
    """generate_shop_description should return a non-empty string."""
    from apps.backend.agents._design.generators_mixin import _DesignGeneratorsMixin

    class _MockGenerators(_DesignGeneratorsMixin):
        def __init__(self):
            self._image_gen = AsyncMock()
            self._output_dir = "."
            self._memory = AsyncMock()

    MOCK_DESC = "Welcome to Warm Celebration Studio — your go-to shop for beautifully designed printables."

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=MOCK_DESC)]
        mock_client.messages.create = AsyncMock(return_value=mock_msg)

        agent = _MockGenerators()
        result = await agent.generate_shop_description(_make_identity())

    assert isinstance(result, str)
    assert len(result) > 20
    assert result == MOCK_DESC


@pytest.mark.asyncio
async def test_generate_shop_description_mentions_aesthetic():
    """Description should reference the shop aesthetic name or tone."""
    from apps.backend.agents._design.generators_mixin import _DesignGeneratorsMixin

    class _MockGenerators(_DesignGeneratorsMixin):
        def __init__(self):
            self._image_gen = AsyncMock()
            self._output_dir = "."
            self._memory = AsyncMock()

    MOCK_DESC = "Warm Celebration Studio offers printable party decorations with a warm, celebratory style."

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=MOCK_DESC)]
        mock_client.messages.create = AsyncMock(return_value=mock_msg)

        agent = _MockGenerators()
        result = await agent.generate_shop_description(_make_identity())

    assert "Warm" in result or "celebrat" in result.lower()
