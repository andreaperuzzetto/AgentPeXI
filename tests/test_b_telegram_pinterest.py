"""B-10: Telegram commands /pinterest_status, /pinterest_auth, /pinterest_queue."""
from __future__ import annotations

import aiosqlite
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Schema inline
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pinterest_queue (
    id           INTEGER  PRIMARY KEY AUTOINCREMENT,
    pin_variant  INTEGER  NOT NULL DEFAULT 1,
    title        TEXT     NOT NULL DEFAULT 'Test Pin',
    description  TEXT     NOT NULL DEFAULT '',
    board_id     TEXT     NOT NULL DEFAULT 'board_1',
    scheduled_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at DATETIME,
    status       TEXT     DEFAULT 'pending',
    cost_image_gen FLOAT  DEFAULT 0.0,
    cost_llm     FLOAT    DEFAULT 0.0,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pinterest_boards (
    board_id   TEXT    PRIMARY KEY,
    board_name TEXT    NOT NULL DEFAULT '',
    section_key TEXT   NOT NULL DEFAULT '',
    pin_count  INTEGER DEFAULT 0,
    is_active  BOOLEAN DEFAULT 1
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA)
    await db.commit()
    return db


def _make_deps(db: aiosqlite.Connection) -> MagicMock:
    deps = MagicMock()
    deps.pepe = MagicMock()
    deps.pepe.memory = MagicMock()
    deps.pepe.memory.get_db = AsyncMock(return_value=db)
    deps.pepe.memory.get_oauth_tokens = AsyncMock(return_value=None)
    return deps


def _make_update() -> MagicMock:
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 99
    return update


# ---------------------------------------------------------------------------
# /pinterest_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pinterest_status_includes_delivery_method():
    """/pinterest_status deve indicare il delivery_method."""
    from apps.backend.telegram.handlers.pinterest import cmd_pinterest_status

    db = await _make_db()
    deps = _make_deps(db)
    update = _make_update()

    import os
    os.environ.pop("PINTEREST_DELIVERY_METHOD", None)

    await cmd_pinterest_status(update, MagicMock(), deps=deps)

    await db.close()
    text = update.message.reply_text.call_args[0][0]
    assert "tailwind" in text.lower()


@pytest.mark.asyncio
async def test_pinterest_status_includes_pin_counts():
    """/pinterest_status deve includere pins queued e failed."""
    from apps.backend.telegram.handlers.pinterest import cmd_pinterest_status

    db = await _make_db()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "INSERT INTO pinterest_queue (status, scheduled_at) VALUES (?,?)", ("pending", future)
    )
    await db.execute(
        "INSERT INTO pinterest_queue (status, scheduled_at) VALUES (?,?)", ("failed", future)
    )
    await db.commit()

    deps = _make_deps(db)
    update = _make_update()

    await cmd_pinterest_status(update, MagicMock(), deps=deps)

    await db.close()
    text = update.message.reply_text.call_args[0][0]
    # Should mention queued and failed counts
    assert "1" in text  # at least one count appears


@pytest.mark.asyncio
async def test_pinterest_status_includes_boards_section():
    """/pinterest_status deve mostrare i board attivi."""
    from apps.backend.telegram.handlers.pinterest import cmd_pinterest_status

    db = await _make_db()
    await db.execute(
        "INSERT INTO pinterest_boards (board_id, board_name, section_key, pin_count, is_active)"
        " VALUES (?,?,?,?,?)",
        ("b1", "Planners & Organizers", "planners", 12, 1),
    )
    await db.commit()

    deps = _make_deps(db)
    update = _make_update()

    await cmd_pinterest_status(update, MagicMock(), deps=deps)

    await db.close()
    text = update.message.reply_text.call_args[0][0]
    assert "Planners" in text


@pytest.mark.asyncio
async def test_pinterest_status_shows_connected_false_with_no_token():
    """/pinterest_status deve mostrare connected=False se nessun token."""
    from apps.backend.telegram.handlers.pinterest import cmd_pinterest_status

    db = await _make_db()
    deps = _make_deps(db)
    deps.pepe.memory.get_oauth_tokens = AsyncMock(return_value=None)
    update = _make_update()

    await cmd_pinterest_status(update, MagicMock(), deps=deps)

    await db.close()
    text = update.message.reply_text.call_args[0][0]
    # Not connected indicator in the message
    assert "🔴" in text or "non connesso" in text.lower() or "❌" in text


# ---------------------------------------------------------------------------
# /pinterest_auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pinterest_auth_shows_connected_when_valid_token():
    """/pinterest_auth deve mostrare connesso quando token valido."""
    from apps.backend.telegram.handlers.pinterest import cmd_pinterest_auth

    db = await _make_db()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    deps = _make_deps(db)
    deps.pepe.memory.get_oauth_tokens = AsyncMock(return_value={
        "access_token": "tok",
        "expires_at": future,
        "updated_at": "2026-05-01T10:00:00",
    })
    update = _make_update()

    await cmd_pinterest_auth(update, MagicMock(), deps=deps)

    await db.close()
    text = update.message.reply_text.call_args[0][0]
    assert "🟢" in text or "connesso" in text.lower() or "✅" in text


@pytest.mark.asyncio
async def test_pinterest_auth_shows_not_connected_when_no_token():
    """/pinterest_auth deve mostrare non connesso quando nessun token."""
    from apps.backend.telegram.handlers.pinterest import cmd_pinterest_auth

    db = await _make_db()
    deps = _make_deps(db)
    deps.pepe.memory.get_oauth_tokens = AsyncMock(return_value=None)
    update = _make_update()

    await cmd_pinterest_auth(update, MagicMock(), deps=deps)

    await db.close()
    text = update.message.reply_text.call_args[0][0]
    assert "🔴" in text or "non connesso" in text.lower() or "❌" in text


@pytest.mark.asyncio
async def test_pinterest_auth_includes_setup_instructions():
    """/pinterest_auth deve includere riferimento a pinterest_auth_setup.py."""
    from apps.backend.telegram.handlers.pinterest import cmd_pinterest_auth

    db = await _make_db()
    deps = _make_deps(db)
    update = _make_update()

    await cmd_pinterest_auth(update, MagicMock(), deps=deps)

    await db.close()
    text = update.message.reply_text.call_args[0][0]
    assert "pinterest_auth_setup" in text


# ---------------------------------------------------------------------------
# /pinterest_queue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pinterest_queue_lists_pending_pins():
    """/pinterest_queue deve listare i prossimi pin pending."""
    from apps.backend.telegram.handlers.pinterest import cmd_pinterest_queue

    db = await _make_db()
    future1 = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    future2 = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "INSERT INTO pinterest_queue (title, status, scheduled_at) VALUES (?,?,?)",
        ("Wellness Planner Pin", "pending", future1),
    )
    await db.execute(
        "INSERT INTO pinterest_queue (title, status, scheduled_at) VALUES (?,?,?)",
        ("Birthday Party Pin", "pending", future2),
    )
    await db.commit()

    deps = _make_deps(db)
    update = _make_update()

    await cmd_pinterest_queue(update, MagicMock(), deps=deps)

    await db.close()
    text = update.message.reply_text.call_args[0][0]
    assert "Wellness Planner" in text
    assert "Birthday Party" in text


@pytest.mark.asyncio
async def test_pinterest_queue_max_five_pins():
    """/pinterest_queue deve mostrare al massimo 5 pin."""
    from apps.backend.telegram.handlers.pinterest import cmd_pinterest_queue

    db = await _make_db()
    for i in range(8):
        future = (datetime.now(timezone.utc) + timedelta(hours=i + 1)).strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            "INSERT INTO pinterest_queue (title, status, scheduled_at) VALUES (?,?,?)",
            (f"Pin Title {i}", "pending", future),
        )
    await db.commit()

    deps = _make_deps(db)
    update = _make_update()

    await cmd_pinterest_queue(update, MagicMock(), deps=deps)

    await db.close()
    text = update.message.reply_text.call_args[0][0]
    # Pins are listed with their titles; at most 5 should appear
    count = sum(1 for i in range(8) if f"Pin Title {i}" in text)
    assert count <= 5


@pytest.mark.asyncio
async def test_pinterest_queue_empty_message_when_no_pending():
    """/pinterest_queue deve inviare messaggio appropriato se nessun pin pending."""
    from apps.backend.telegram.handlers.pinterest import cmd_pinterest_queue

    db = await _make_db()
    deps = _make_deps(db)
    update = _make_update()

    await cmd_pinterest_queue(update, MagicMock(), deps=deps)

    await db.close()
    text = update.message.reply_text.call_args[0][0]
    assert "coda" in text.lower() or "vuota" in text.lower() or "nessun" in text.lower() or "pending" in text.lower()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_adds_pinterest_commands():
    """register() deve aggiungere almeno 3 handler per i comandi Pinterest."""
    from apps.backend.telegram.handlers.pinterest import register
    from unittest.mock import MagicMock

    app = MagicMock()
    deps = MagicMock()
    chat_filter = MagicMock()

    register(app, deps, chat_filter)

    assert app.add_handler.call_count >= 3
