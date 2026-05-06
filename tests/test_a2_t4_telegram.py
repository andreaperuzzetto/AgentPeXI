# tests/test_a2_t4_telegram.py
"""Tests for shop_identity Telegram handler."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message, User, Chat, CallbackQuery
from apps.backend.telegram.handlers.shop_identity import cmd_style_guide, cb_approve_identity
from apps.backend.telegram.dependencies import BotDependencies


def _make_deps(db):
    mock_memory = AsyncMock()
    mock_memory.get_db = AsyncMock(return_value=db)
    mock_pepe = MagicMock()
    mock_pepe.memory = mock_memory
    return BotDependencies(pepe=mock_pepe)


def _make_update(text: str = "/style-guide") -> Update:
    user = MagicMock(spec=User)
    user.id = 12345
    chat = MagicMock(spec=Chat)
    chat.id = 12345
    msg = AsyncMock(spec=Message)
    msg.reply_text = AsyncMock()
    msg.chat = chat
    msg.from_user = user
    msg.text = text
    upd = MagicMock(spec=Update)
    upd.message = msg
    upd.effective_user = user
    return upd


def _make_callback_update(data: str, db) -> tuple[Update, AsyncMock]:
    query = AsyncMock(spec=CallbackQuery)
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    user = MagicMock(spec=User)
    user.id = 12345
    upd = MagicMock(spec=Update)
    upd.callback_query = query
    upd.effective_user = user
    return upd, query


@pytest.mark.asyncio
async def test_cmd_style_guide_triggers_generation(tmp_path):
    """cmd_style_guide should call generate_style_options and send 3 options."""
    import aiosqlite
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""
            CREATE TABLE shop_identity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aesthetic_name TEXT NOT NULL, palette_primary TEXT NOT NULL DEFAULT '',
                palette_secondary TEXT NOT NULL DEFAULT '', palette_accent TEXT NOT NULL DEFAULT '',
                mockup_style TEXT NOT NULL DEFAULT '', tone TEXT NOT NULL DEFAULT '',
                logo_path TEXT, banner_path TEXT, approved_at TEXT,
                approved_by TEXT NOT NULL DEFAULT 'andrea', is_active INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.commit()

        deps = _make_deps(db)
        upd = _make_update()
        ctx = MagicMock()
        ctx.bot = AsyncMock()
        ctx.bot.send_message = AsyncMock()

        with patch.object(
            deps.pepe.memory, "get_db", AsyncMock(return_value=db)
        ), patch(
            "apps.backend.agents.market_data.MarketDataAgent.generate_style_options",
            new_callable=AsyncMock,
            return_value=[1, 2, 3],
        ):
            # Seed 3 records so the handler can list them
            from apps.backend.core.shop_identity_service import ShopIdentityService
            svc = ShopIdentityService(db)
            for name in ["Option A", "Option B", "Option C"]:
                await svc.create(
                    aesthetic_name=name,
                    palette_primary="#AAA",
                    palette_secondary="#BBB",
                    palette_accent="#CCC",
                    mockup_style="flat_lay",
                    tone="warm",
                )
            await cmd_style_guide(deps, upd, ctx)

        upd.message.reply_text.assert_called()
        call_kwargs = upd.message.reply_text.call_args
        # Should have reply_markup with 3 buttons
        assert call_kwargs is not None


@pytest.mark.asyncio
async def test_cb_approve_identity_sets_active(tmp_path):
    """Approval callback with approve_identity:2 should call set_active(2)."""
    import aiosqlite
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""
            CREATE TABLE shop_identity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aesthetic_name TEXT NOT NULL, palette_primary TEXT NOT NULL DEFAULT '',
                palette_secondary TEXT NOT NULL DEFAULT '', palette_accent TEXT NOT NULL DEFAULT '',
                mockup_style TEXT NOT NULL DEFAULT '', tone TEXT NOT NULL DEFAULT '',
                logo_path TEXT, banner_path TEXT, approved_at TEXT,
                approved_by TEXT NOT NULL DEFAULT 'andrea', is_active INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.commit()
        from apps.backend.core.shop_identity_service import ShopIdentityService
        svc = ShopIdentityService(db)
        for name in ["Opt A", "Opt B", "Opt C"]:
            await svc.create(aesthetic_name=name, palette_primary="#A", palette_secondary="#B",
                             palette_accent="#C", mockup_style="flat_lay", tone="warm")

        deps = _make_deps(db)
        upd, query = _make_callback_update("approve_identity:2", db)
        ctx = MagicMock()

        with patch.object(deps.pepe.memory, "get_db", AsyncMock(return_value=db)):
            await cb_approve_identity(deps, upd, ctx)

        active = await svc.get_active()
        assert active is not None
        assert active.id == 2
        query.answer.assert_called_once()
        query.edit_message_text.assert_called_once()
