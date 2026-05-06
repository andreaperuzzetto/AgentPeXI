"""Tests for A.1 Telegram /sections commands."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update
from telegram.ext import ContextTypes


@pytest.mark.asyncio
class TestCmdSections:
    """Test suite for /sections command and subcommands."""

    async def test_no_args_calls_list(self):
        """Test /sections with no args dispatches to _sections_list."""
        from apps.backend.telegram.handlers._queue._sections import cmd_sections

        deps = MagicMock()
        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        context.args = []

        with patch(
            "apps.backend.telegram.handlers._queue._sections._sections_list",
            new_callable=AsyncMock,
        ) as mock_list:
            await cmd_sections(deps, update, context)
            mock_list.assert_called_once_with(deps, update)

    async def test_uncategorized_subcommand(self):
        """Test /sections uncategorized dispatches to _sections_uncategorized."""
        from apps.backend.telegram.handlers._queue._sections import cmd_sections

        deps = MagicMock()
        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        context.args = ["uncategorized"]

        with patch(
            "apps.backend.telegram.handlers._queue._sections._sections_uncategorized",
            new_callable=AsyncMock,
        ) as mock_uncat:
            await cmd_sections(deps, update, context)
            mock_uncat.assert_called_once_with(deps, update)

    async def test_map_subcommand(self):
        """Test /sections map <niche> <section> dispatches to _sections_map."""
        from apps.backend.telegram.handlers._queue._sections import cmd_sections

        deps = MagicMock()
        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        context.args = ["map", "niche1", "My", "Section"]

        with patch(
            "apps.backend.telegram.handlers._queue._sections._sections_map",
            new_callable=AsyncMock,
        ) as mock_map:
            await cmd_sections(deps, update, context)
            mock_map.assert_called_once_with(deps, update, "niche1", "My Section")

    async def test_add_subcommand(self):
        """Test /sections add <name> dispatches to _sections_add."""
        from apps.backend.telegram.handlers._queue._sections import cmd_sections

        deps = MagicMock()
        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        context.args = ["add", "New", "Section"]

        with patch(
            "apps.backend.telegram.handlers._queue._sections._sections_add",
            new_callable=AsyncMock,
        ) as mock_add:
            await cmd_sections(deps, update, context)
            mock_add.assert_called_once_with(deps, update, "New Section")

    async def test_flag_subcommand(self):
        """Test /sections flag <name> dispatches to _sections_flag."""
        from apps.backend.telegram.handlers._queue._sections import cmd_sections

        deps = MagicMock()
        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        context.args = ["flag", "Bad", "Section"]

        with patch(
            "apps.backend.telegram.handlers._queue._sections._sections_flag",
            new_callable=AsyncMock,
        ) as mock_flag:
            await cmd_sections(deps, update, context)
            mock_flag.assert_called_once_with(deps, update, "Bad Section")

    async def test_unknown_subcommand_shows_help(self):
        """Test /sections unknown shows help text."""
        from apps.backend.telegram.handlers._queue._sections import cmd_sections

        deps = MagicMock()
        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        context.args = ["unknown"]

        await cmd_sections(deps, update, context)
        update.message.reply_text.assert_called_once()
        call_args = update.message.reply_text.call_args
        assert "📂 *Comandi sezioni:*" in call_args[0][0]
        assert "parse_mode" in call_args[1]
        assert call_args[1]["parse_mode"] == "Markdown"

    async def test_map_insufficient_args_shows_help(self):
        """Test /sections map <niche> (missing section) shows help."""
        from apps.backend.telegram.handlers._queue._sections import cmd_sections

        deps = MagicMock()
        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        context.args = ["map", "niche1"]  # Missing section name

        await cmd_sections(deps, update, context)
        update.message.reply_text.assert_called_once()
        call_args = update.message.reply_text.call_args
        assert "📂 *Comandi sezioni:*" in call_args[0][0]

    async def test_none_message_returns_early(self):
        """Test cmd_sections returns early if update.message is None."""
        from apps.backend.telegram.handlers._queue._sections import cmd_sections

        deps = MagicMock()
        update = MagicMock(spec=Update)
        update.message = None
        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        context.args = []

        # Should not raise, just return
        await cmd_sections(deps, update, context)


@pytest.mark.asyncio
class TestSectionsList:
    """Test _sections_list function."""

    async def test_sections_list_no_sections(self):
        """Test _sections_list with no sections."""
        from apps.backend.telegram.handlers._queue._sections import _sections_list

        deps = MagicMock()
        deps.memory.get_db = AsyncMock()
        db_mock = AsyncMock()
        deps.memory.get_db.return_value = db_mock

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        with patch(
            "apps.backend.core.etsy_sections_service.EtsySectionsService"
        ) as mock_ess:
            mock_ess_instance = AsyncMock()
            mock_ess.return_value = mock_ess_instance
            mock_ess_instance.get_sections_with_uncategorized_counts.return_value = []

            await _sections_list(deps, update)
            update.message.reply_text.assert_called_once()
            assert "Nessuna sezione Etsy" in update.message.reply_text.call_args[0][0]

    async def test_sections_list_with_sections(self):
        """Test _sections_list with sections and pending uncategorized."""
        from apps.backend.telegram.handlers._queue._sections import _sections_list

        deps = MagicMock()
        deps.memory.get_db = AsyncMock()
        db_mock = AsyncMock()
        deps.memory.get_db.return_value = db_mock

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        with patch(
            "apps.backend.core.etsy_sections_service.EtsySectionsService"
        ) as mock_ess:
            mock_ess_instance = AsyncMock()
            mock_ess.return_value = mock_ess_instance
            mock_ess_instance.get_sections_with_uncategorized_counts.return_value = [
                {
                    "section_name": "Section A",
                    "listing_count": 10,
                    "last_listing_at": "2024-01-01",
                    "pending_uncategorized": 5,
                },
                {
                    "section_name": "Section B",
                    "listing_count": 3,
                    "last_listing_at": None,
                    "pending_uncategorized": 5,
                },
            ]

            await _sections_list(deps, update)
            update.message.reply_text.assert_called_once()
            text = update.message.reply_text.call_args[0][0]
            assert "📂 *Sezioni Etsy*" in text
            assert "Section A" in text
            assert "Section B" in text
            assert "5 niche in attesa" in text

    async def test_sections_list_error(self):
        """Test _sections_list handles exceptions."""
        from apps.backend.telegram.handlers._queue._sections import _sections_list

        deps = MagicMock()
        deps.memory.get_db = AsyncMock(side_effect=Exception("DB error"))

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        await _sections_list(deps, update)
        update.message.reply_text.assert_called_once()
        assert "❌ Errore" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
class TestSectionsUncategorized:
    """Test _sections_uncategorized function."""

    async def test_uncategorized_no_pending(self):
        """Test _sections_uncategorized with no pending niches."""
        from apps.backend.telegram.handlers._queue._sections import _sections_uncategorized

        deps = MagicMock()
        deps.memory.get_db = AsyncMock()
        db_mock = AsyncMock()
        cursor_mock = AsyncMock()
        cursor_mock.fetchall.return_value = []
        db_mock.execute.return_value = cursor_mock
        deps.memory.get_db.return_value = db_mock

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        await _sections_uncategorized(deps, update)
        update.message.reply_text.assert_called_once()
        assert "✅ Nessuna niche" in update.message.reply_text.call_args[0][0]

    async def test_uncategorized_with_suggestions(self):
        """Test _sections_uncategorized with pending niches and suggestions."""
        from apps.backend.telegram.handlers._queue._sections import _sections_uncategorized

        deps = MagicMock()
        deps.memory.get_db = AsyncMock()
        db_mock = AsyncMock()
        cursor_mock = AsyncMock()
        cursor_mock.fetchall.return_value = [
            {
                "niche_key": "niche1",
                "detected_at": "2024-01-01",
                "suggested_section_id": "123",
                "suggested_confidence": 0.85,
            },
            {
                "niche_key": "niche2",
                "detected_at": "2024-01-02",
                "suggested_section_id": None,
                "suggested_confidence": None,
            },
        ]
        db_mock.execute.return_value = cursor_mock
        deps.memory.get_db.return_value = db_mock

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        await _sections_uncategorized(deps, update)
        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "📋 *Niche non mappate*" in text
        assert "niche1" in text
        assert "niche2" in text
        assert "suggerita: 123 (85%)" in text


@pytest.mark.asyncio
class TestSectionsMap:
    """Test _sections_map function."""

    async def test_map_section_found(self):
        """Test _sections_map with valid section."""
        from apps.backend.telegram.handlers._queue._sections import _sections_map

        deps = MagicMock()
        deps.memory.get_db = AsyncMock()
        deps.ws_broadcaster = AsyncMock()
        db_mock = AsyncMock()
        cursor_mock = AsyncMock()
        cursor_mock.fetchone.return_value = {
            "section_id": "123",
            "section_name": "My Section",
        }
        db_mock.execute.return_value = cursor_mock
        deps.memory.get_db.return_value = db_mock

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        with patch(
            "apps.backend.core.etsy_sections_service.EtsySectionsService"
        ) as mock_ess:
            mock_ess_instance = AsyncMock()
            mock_ess.return_value = mock_ess_instance

            await _sections_map(deps, update, "niche1", "My Section")

            mock_ess_instance.map_niche.assert_called_once_with(
                "niche1", "123", mapped_by="human"
            )
            deps.ws_broadcaster.assert_called_once()
            ws_call = deps.ws_broadcaster.call_args[0][0]
            assert ws_call["type"] == "section_mapped"
            assert ws_call["niche_key"] == "niche1"
            update.message.reply_text.assert_called_once()
            assert "✅" in update.message.reply_text.call_args[0][0]

    async def test_map_section_not_found(self):
        """Test _sections_map with unknown section."""
        from apps.backend.telegram.handlers._queue._sections import _sections_map

        deps = MagicMock()
        deps.memory.get_db = AsyncMock()
        db_mock = AsyncMock()
        cursor_mock = AsyncMock()
        cursor_mock.fetchone.return_value = None
        db_mock.execute.return_value = cursor_mock
        deps.memory.get_db.return_value = db_mock

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        with patch(
            "apps.backend.core.etsy_sections_service.EtsySectionsService"
        ):
            await _sections_map(deps, update, "niche1", "Unknown")
            update.message.reply_text.assert_called_once()
            assert "❌" in update.message.reply_text.call_args[0][0]
            assert "non trovata" in update.message.reply_text.call_args[0][0]

    async def test_map_ws_broadcaster_failure_still_reports_success(self):
        """Test _sections_map reports success even if ws_broadcaster fails."""
        from apps.backend.telegram.handlers._queue._sections import _sections_map

        deps = MagicMock()
        deps.memory.get_db = AsyncMock()
        deps.ws_broadcaster = AsyncMock(side_effect=Exception("WS failed"))
        db_mock = AsyncMock()
        cursor_mock = AsyncMock()
        cursor_mock.fetchone.return_value = {
            "section_id": "123",
            "section_name": "My Section",
        }
        db_mock.execute.return_value = cursor_mock
        deps.memory.get_db.return_value = db_mock

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        with patch(
            "apps.backend.core.etsy_sections_service.EtsySectionsService"
        ) as mock_ess:
            mock_ess_instance = AsyncMock()
            mock_ess.return_value = mock_ess_instance

            await _sections_map(deps, update, "niche1", "My Section")

            # Mapping was successful
            mock_ess_instance.map_niche.assert_called_once_with(
                "niche1", "123", mapped_by="human"
            )
            db_mock.commit.assert_called_once()
            # Success message sent despite WS failure
            update.message.reply_text.assert_called_once()
            assert "✅" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
class TestSectionsAdd:
    """Test _sections_add function."""

    async def test_add_section_success(self):
        """Test _sections_add creates section successfully."""
        from apps.backend.telegram.handlers._queue._sections import _sections_add

        deps = MagicMock()
        deps.etsy_api = AsyncMock()
        deps.etsy_api.create_shop_section.return_value = {
            "shop_section_id": 456,
            "title": "New Section",
        }
        deps.memory.get_db = AsyncMock()
        db_mock = AsyncMock()
        deps.memory.get_db.return_value = db_mock

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        with patch(
            "apps.backend.core.etsy_sections_service.EtsySectionsService"
        ) as mock_ess:
            mock_ess_instance = AsyncMock()
            mock_ess.return_value = mock_ess_instance

            await _sections_add(deps, update, "New Section")

            deps.etsy_api.create_shop_section.assert_called_once_with(
                title="New Section"
            )
            mock_ess_instance.sync_sections.assert_called_once()
            update.message.reply_text.assert_called_once()
            assert "✅ Sezione creata" in update.message.reply_text.call_args[0][0]

    async def test_add_section_no_api(self):
        """Test _sections_add with no Etsy API."""
        from apps.backend.telegram.handlers._queue._sections import _sections_add

        deps = MagicMock()
        deps.etsy_api = None

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        await _sections_add(deps, update, "New Section")
        update.message.reply_text.assert_called_once()
        assert "❌ Etsy API" in update.message.reply_text.call_args[0][0]

    async def test_add_section_error(self):
        """Test _sections_add handles exceptions."""
        from apps.backend.telegram.handlers._queue._sections import _sections_add

        deps = MagicMock()
        deps.etsy_api = AsyncMock()
        deps.etsy_api.create_shop_section.side_effect = Exception("API error")

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        await _sections_add(deps, update, "New Section")
        update.message.reply_text.assert_called_once()
        assert "❌ Errore" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
class TestSectionsFlag:
    """Test _sections_flag function."""

    async def test_flag_section_not_found(self):
        """Test _sections_flag when no section matches."""
        from apps.backend.telegram.handlers._queue._sections import _sections_flag

        deps = MagicMock()
        deps.memory.get_db = AsyncMock()
        db_mock = AsyncMock()
        cursor_mock = AsyncMock()
        cursor_mock.fetchall.return_value = []
        db_mock.execute.return_value = cursor_mock
        deps.memory.get_db.return_value = db_mock

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        await _sections_flag(deps, update, "Nonexistent")
        update.message.reply_text.assert_called_once()
        assert "❌ Nessuna sezione attiva trovata" in update.message.reply_text.call_args[0][0]
        db_mock.commit.assert_not_called()

    async def test_flag_section_success(self):
        """Test _sections_flag marks section inactive."""
        from apps.backend.telegram.handlers._queue._sections import _sections_flag

        deps = MagicMock()
        deps.memory.get_db = AsyncMock()
        db_mock = AsyncMock()
        cursor_mock = AsyncMock()
        cursor_mock.fetchall.return_value = [{"section_id": 1, "section_name": "Art"}]
        db_mock.execute.return_value = cursor_mock
        deps.memory.get_db.return_value = db_mock

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        await _sections_flag(deps, update, "Art")
        assert db_mock.execute.call_count == 2  # SELECT + UPDATE
        db_mock.commit.assert_called_once()
        update.message.reply_text.assert_called_once()
        reply_text = update.message.reply_text.call_args[0][0]
        assert "🚩" in reply_text
        assert "Art" in reply_text

    async def test_flag_section_error(self):
        """Test _sections_flag handles exceptions."""
        from apps.backend.telegram.handlers._queue._sections import _sections_flag

        deps = MagicMock()
        deps.memory.get_db = AsyncMock()
        db_mock = AsyncMock()
        db_mock.execute.side_effect = Exception("DB error")
        deps.memory.get_db.return_value = db_mock

        update = MagicMock(spec=Update)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        await _sections_flag(deps, update, "Bad Section")
        update.message.reply_text.assert_called_once()
        assert "❌ Errore" in update.message.reply_text.call_args[0][0]
