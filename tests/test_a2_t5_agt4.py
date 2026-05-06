# tests/test_a2_t5_agt4.py
"""Tests for AGT-4 DesignAgent image framework upgrade."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock


def _make_brief() -> dict:
    return {
        "product_type": "wall_art",
        "niche": "boho wedding printables",
        "color_scheme": "warm gold, blush",
        "style": "minimalist",
        "selling_signals": {},
    }


def _make_identity():
    from apps.backend.core.shop_identity_service import ShopIdentityRecord
    return ShopIdentityRecord(
        id=1,
        aesthetic_name="Warm Celebration Studio",
        palette_primary="#C9A84C",
        palette_secondary="#F2D0C4",
        palette_accent="#FAF7F2",
        mockup_style="flat_lay",
        tone="warm and celebratory",
        logo_path=None,
        banner_path=None,
        approved_at=None,
        approved_by="andrea",
        is_active=True,
    )


@pytest.mark.asyncio
async def test_build_5component_prompt_has_all_components():
    """_build_5component_prompt should produce all 5 components."""
    from apps.backend.agents._design.generators_mixin import _build_5component_prompt
    brief = _make_brief()
    identity = _make_identity()
    prompt = _build_5component_prompt(brief, identity)
    assert "SUBJECT:" in prompt
    assert "STYLE:" in prompt
    assert "COMPOSITION:" in prompt
    assert "TECHNICAL:" in prompt
    assert "NEGATIVE PROMPT:" in prompt


@pytest.mark.asyncio
async def test_build_5component_prompt_uses_identity_palette():
    """Prompt should incorporate identity palette colors."""
    from apps.backend.agents._design.generators_mixin import _build_5component_prompt
    brief = _make_brief()
    identity = _make_identity()
    prompt = _build_5component_prompt(brief, identity)
    assert "#C9A84C" in prompt or "warm gold" in prompt.lower()


@pytest.mark.asyncio
async def test_verify_image_quality_passes_large_image():
    """_verify_image_quality should return True for 2000x2000px image."""
    from apps.backend.agents._design.generators_mixin import _verify_image_quality
    meta = {"width": 3000, "height": 3000}
    assert _verify_image_quality(meta) is True


@pytest.mark.asyncio
async def test_verify_image_quality_fails_small_image():
    """_verify_image_quality should return False for images under 2000px."""
    from apps.backend.agents._design.generators_mixin import _verify_image_quality
    meta = {"width": 1920, "height": 1080}
    assert _verify_image_quality(meta) is False


@pytest.mark.asyncio
async def test_verify_image_quality_empty_meta():
    """_verify_image_quality with no size data logs warning and returns True (fail open)."""
    from apps.backend.agents._design.generators_mixin import _verify_image_quality
    assert _verify_image_quality({}) is True  # fail open — no data = don't block
