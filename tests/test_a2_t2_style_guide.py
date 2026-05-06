"""Tests for _style_guide_mixin.py — generate_style_options()."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import aiosqlite
from apps.backend.agents.market_data import MarketDataAgent
from apps.backend.core.memory import MemoryManager
from apps.backend.core.shop_identity_service import ShopIdentityService


MOCK_HAIKU_RESPONSE = json.dumps([
    {
        "aesthetic_name": "Warm Celebration Studio",
        "palette_primary": "#C9A84C",
        "palette_secondary": "#F2D0C4",
        "palette_accent": "#FAF7F2",
        "mockup_style": "flat_lay",
        "tone": "warm and celebratory, aspirational but accessible",
        "rationale": "Dominant warm gold palette aligns with party/wedding top sellers.",
    },
    {
        "aesthetic_name": "Sage Wellness Co.",
        "palette_primary": "#8FAF8F",
        "palette_secondary": "#F5F0E8",
        "palette_accent": "#C4A8B0",
        "mockup_style": "lifestyle",
        "tone": "gentle and supportive, science-backed but human",
        "rationale": "Sage + cream is the #1 color combo in wellness top sellers.",
    },
    {
        "aesthetic_name": "Clear Path Studio",
        "palette_primary": "#9EA8B2",
        "palette_secondary": "#F7F7F5",
        "palette_accent": "#4A6FA5",
        "mockup_style": "flat_lay",
        "tone": "efficient and empowering, no-nonsense but encouraging",
        "rationale": "Clean gray-white-blue appeals to planner buyers across all age groups.",
    },
])


@pytest.mark.asyncio
async def test_generate_style_options_creates_3_records(tmp_path):
    """generate_style_options() should call Haiku and save 3 records via ShopIdentityService."""
    mock_memory = AsyncMock(spec=MemoryManager)

    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""
            CREATE TABLE shop_identity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aesthetic_name TEXT NOT NULL,
                palette_primary TEXT NOT NULL DEFAULT '',
                palette_secondary TEXT NOT NULL DEFAULT '',
                palette_accent TEXT NOT NULL DEFAULT '',
                mockup_style TEXT NOT NULL DEFAULT '',
                tone TEXT NOT NULL DEFAULT '',
                logo_path TEXT,
                banner_path TEXT,
                approved_at TEXT,
                approved_by TEXT NOT NULL DEFAULT 'andrea',
                is_active INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.commit()

        with patch("apps.backend.agents._market_data._style_guide_mixin.settings") as mock_settings, \
             patch("anthropic.AsyncAnthropic") as mock_client_cls:
            mock_settings.ANTHROPIC_API_KEY = "test-api-key"
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text=MOCK_HAIKU_RESPONSE)]
            mock_client.messages.create = AsyncMock(return_value=mock_msg)

            agent = MarketDataAgent(memory=mock_memory, mock_mode=True)
            ids = await agent.generate_style_options(db=db)

        assert len(ids) == 3
        svc = ShopIdentityService(db)
        options = await svc.list_options()
        assert len(options) == 3
        assert options[0].aesthetic_name == "Warm Celebration Studio"
        assert options[1].aesthetic_name == "Sage Wellness Co."
        assert options[2].aesthetic_name == "Clear Path Studio"
        assert all(not o.is_active for o in options)


@pytest.mark.asyncio
async def test_generate_style_options_returns_ids(tmp_path):
    """Return value must be a list of 3 integer IDs."""
    mock_memory = AsyncMock(spec=MemoryManager)

    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""
            CREATE TABLE shop_identity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aesthetic_name TEXT NOT NULL,
                palette_primary TEXT NOT NULL DEFAULT '',
                palette_secondary TEXT NOT NULL DEFAULT '',
                palette_accent TEXT NOT NULL DEFAULT '',
                mockup_style TEXT NOT NULL DEFAULT '',
                tone TEXT NOT NULL DEFAULT '',
                logo_path TEXT, banner_path TEXT,
                approved_at TEXT,
                approved_by TEXT NOT NULL DEFAULT 'andrea',
                is_active INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.commit()

        with patch("apps.backend.agents._market_data._style_guide_mixin.settings") as mock_settings, \
             patch("anthropic.AsyncAnthropic") as mock_client_cls:
            mock_settings.ANTHROPIC_API_KEY = "test-api-key"
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text=MOCK_HAIKU_RESPONSE)]
            mock_client.messages.create = AsyncMock(return_value=mock_msg)

            agent = MarketDataAgent(memory=mock_memory, mock_mode=True)
            ids = await agent.generate_style_options(db=db)

        assert ids == [1, 2, 3]
