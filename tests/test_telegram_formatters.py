"""Tests for pure functions in apps.backend.telegram.formatters."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from apps.backend.telegram.formatters import md_escape, reply_chunked, send_chunked, TG_LIMIT


# ---------------------------------------------------------------------------
# md_escape — basic character escaping
# ---------------------------------------------------------------------------

def test_md_escape_underscore():
    assert md_escape("hello_world") == r"hello\_world"


def test_md_escape_asterisk():
    assert md_escape("bold*text") == r"bold\*text"


def test_md_escape_backtick():
    assert md_escape("code`block") == r"code\`block"


def test_md_escape_open_bracket():
    assert md_escape("[link]") == r"\[link\]"


def test_md_escape_no_special_chars():
    assert md_escape("plain text") == "plain text"


def test_md_escape_empty_string():
    assert md_escape("") == ""


def test_md_escape_multiple_underscores():
    assert md_escape("file_name_here") == r"file\_name\_here"


def test_md_escape_all_special_chars():
    result = md_escape("_*`[")
    assert result == r"\_\*\`\["


def test_md_escape_all_mdv2_special_chars():
    """Test that all 18 MarkdownV2 special chars are escaped."""
    text = "_*[]()~`>#+-=|{}.!"
    result = md_escape(text)
    # Each char should be preceded by backslash
    assert result == r"\_\*\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!"


def test_md_escape_does_not_modify_unrelated_chars():
    text = "hello world @$%^&;:',/?"
    assert md_escape(text) == text


def test_md_escape_mixed():
    result = md_escape("niche_name [best] *seller*")
    assert "\\_" in result
    assert "\\[" in result
    assert "\\*" in result


def test_tg_limit_constant():
    assert TG_LIMIT == 4000
    assert isinstance(TG_LIMIT, int)


# ---------------------------------------------------------------------------
# reply_chunked — short message (single call)
# ---------------------------------------------------------------------------

async def test_reply_chunked_short_sends_once():
    message = MagicMock()
    message.reply_text = AsyncMock()
    await reply_chunked(message, "Hello world")
    message.reply_text.assert_awaited_once_with("Hello world")


async def test_reply_chunked_exactly_at_limit_sends_once():
    message = MagicMock()
    message.reply_text = AsyncMock()
    text = "x" * TG_LIMIT
    await reply_chunked(message, text)
    message.reply_text.assert_awaited_once_with(text)


async def test_reply_chunked_long_text_splits():
    message = MagicMock()
    message.reply_text = AsyncMock()
    line = "A" * 100 + "\n"
    text = line * 50  # 5050 chars > TG_LIMIT
    await reply_chunked(message, text)
    assert message.reply_text.await_count >= 2


async def test_reply_chunked_empty_trailing_chunk_not_sent():
    message = MagicMock()
    message.reply_text = AsyncMock()
    # First line exceeds TG_LIMIT, second is bare newline → trailing chunk is whitespace-only
    text = "A" * 4001 + "\n\n"
    await reply_chunked(message, text)
    message.reply_text.assert_awaited_once()


# ---------------------------------------------------------------------------
# send_chunked — short message (single call)
# ---------------------------------------------------------------------------

async def test_send_chunked_short_sends_once():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    await send_chunked(bot, 12345, "Hello!")
    bot.send_message.assert_awaited_once_with(chat_id=12345, text="Hello!")


async def test_send_chunked_long_text_splits():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    line = "B" * 100 + "\n"
    text = line * 50  # 5050 chars > TG_LIMIT
    await send_chunked(bot, 99, text)
    assert bot.send_message.await_count >= 2


async def test_send_chunked_empty_trailing_not_sent():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    text = "B" * 4001 + "\n\n"
    await send_chunked(bot, 1, text)
    bot.send_message.assert_awaited_once()
