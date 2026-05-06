"""B-09: GET /api/pinterest/status endpoint tests."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import apps.backend.api.state as state_mod

# ---------------------------------------------------------------------------
# Schema SQL — subset dei campi necessari per i test
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pinterest_queue (
    id                    INTEGER  PRIMARY KEY AUTOINCREMENT,
    production_queue_id   INTEGER,
    pin_variant           INTEGER  NOT NULL DEFAULT 1,
    image_path            TEXT     NOT NULL DEFAULT '',
    title                 TEXT     NOT NULL DEFAULT '',
    description           TEXT     NOT NULL DEFAULT '',
    board_id              TEXT     NOT NULL DEFAULT 'board_1',
    scheduled_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at          DATETIME,
    pinterest_pin_id      TEXT,
    status                TEXT     DEFAULT 'pending',
    delivery_method       TEXT     DEFAULT 'tailwind',
    cost_image_gen        FLOAT    DEFAULT 0.0,
    cost_llm              FLOAT    DEFAULT 0.0,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pinterest_boards (
    board_id    TEXT     PRIMARY KEY,
    board_name  TEXT     NOT NULL DEFAULT '',
    section_key TEXT     NOT NULL DEFAULT '',
    board_type  TEXT     DEFAULT 'section',
    created_at  DATETIME,
    pin_count   INTEGER  DEFAULT 0,
    is_active   BOOLEAN  DEFAULT 1
);
"""

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    """App FastAPI con pinterest router registrato, auth bypassed."""
    from apps.backend.api.routers import pinterest

    _app = FastAPI()
    _app.include_router(pinterest.router)
    _app.dependency_overrides[state_mod.verify_personal_key] = lambda: None
    yield _app
    _app.dependency_overrides.clear()


async def _make_db() -> aiosqlite.Connection:
    """Crea un DB in-memory con lo schema pinterest."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA)
    await db.commit()
    return db


def _make_memory(db: aiosqlite.Connection) -> MagicMock:
    """Stub MemoryManager che usa il DB fornito."""
    mem = MagicMock()
    mem.get_db = AsyncMock(return_value=db)
    mem.get_oauth_tokens = AsyncMock(return_value=None)
    return mem


# ---------------------------------------------------------------------------
# 1. 503 quando state.memory è None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_503_when_memory_none(app):
    """GET /api/pinterest/status ritorna 503 se state.memory è None."""
    with patch.object(state_mod, "memory", None):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/status")

    assert resp.status_code == 503
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# 2. delivery_method viene letto dall'env
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_returns_delivery_method_tailwind(app):
    """delivery_method deve essere 'tailwind' quando env non impostato."""
    db = await _make_db()
    mem = _make_memory(db)

    with patch.object(state_mod, "memory", mem), \
         patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PINTEREST_DELIVERY_METHOD", None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/status")

    await db.close()
    assert resp.status_code == 200
    assert resp.json()["delivery_method"] == "tailwind"


@pytest.mark.asyncio
async def test_status_returns_delivery_method_direct(app):
    """delivery_method deve essere 'direct' quando env è 'direct'."""
    db = await _make_db()
    mem = _make_memory(db)

    with patch.object(state_mod, "memory", mem), \
         patch.dict(os.environ, {"PINTEREST_DELIVERY_METHOD": "direct"}):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/status")

    await db.close()
    assert resp.status_code == 200
    assert resp.json()["delivery_method"] == "direct"


# ---------------------------------------------------------------------------
# 3. Conteggio pin_today (status='published', published_at=oggi)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_pins_today_counts_published_today(app):
    """pins_today deve contare solo i pin con status='published' e published_at=oggi."""
    db = await _make_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "INSERT INTO pinterest_queue (status, published_at, scheduled_at) VALUES (?,?,?)",
        ("published", today, today),
    )
    await db.execute(
        "INSERT INTO pinterest_queue (status, published_at, scheduled_at) VALUES (?,?,?)",
        ("published", today, today),
    )
    await db.execute(
        # publicato ieri — non deve essere contato
        "INSERT INTO pinterest_queue (status, published_at, scheduled_at) VALUES (?,?,?)",
        ("published", yesterday, yesterday),
    )
    await db.commit()

    mem = _make_memory(db)

    with patch.object(state_mod, "memory", mem):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/status")

    await db.close()
    assert resp.status_code == 200
    assert resp.json()["pins_today"] == 2


# ---------------------------------------------------------------------------
# 4. Conteggio pins_queued (status='pending')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_pins_queued_counts_pending(app):
    """pins_queued deve contare solo i pin con status='pending'."""
    db = await _make_db()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    for _ in range(3):
        await db.execute(
            "INSERT INTO pinterest_queue (status, scheduled_at) VALUES (?,?)",
            ("pending", future),
        )
    await db.execute(
        "INSERT INTO pinterest_queue (status, scheduled_at) VALUES (?,?)",
        ("published", future),
    )
    await db.commit()

    mem = _make_memory(db)

    with patch.object(state_mod, "memory", mem):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/status")

    await db.close()
    assert resp.status_code == 200
    assert resp.json()["pins_queued"] == 3


# ---------------------------------------------------------------------------
# 5. Conteggio pins_failed (status='failed')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_pins_failed_counts_failed(app):
    """pins_failed deve contare solo i pin con status='failed'."""
    db = await _make_db()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    await db.execute(
        "INSERT INTO pinterest_queue (status, scheduled_at) VALUES (?,?)",
        ("failed", future),
    )
    await db.execute(
        "INSERT INTO pinterest_queue (status, scheduled_at) VALUES (?,?)",
        ("pending", future),
    )
    await db.commit()

    mem = _make_memory(db)

    with patch.object(state_mod, "memory", mem):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/status")

    await db.close()
    assert resp.status_code == 200
    assert resp.json()["pins_failed"] == 1


# ---------------------------------------------------------------------------
# 6. Lista boards (solo is_active=1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_boards_returns_active_boards_only(app):
    """boards deve restituire solo board attive con i campi corretti."""
    db = await _make_db()
    await db.execute(
        "INSERT INTO pinterest_boards (board_id, board_name, section_key, pin_count, is_active) "
        "VALUES (?,?,?,?,?)",
        ("board_1", "Planners & Organizers", "planners", 42, 1),
    )
    await db.execute(
        "INSERT INTO pinterest_boards (board_id, board_name, section_key, pin_count, is_active) "
        "VALUES (?,?,?,?,?)",
        ("board_2", "Inactive Board", "other", 0, 0),
    )
    await db.commit()

    mem = _make_memory(db)

    with patch.object(state_mod, "memory", mem):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/status")

    await db.close()
    assert resp.status_code == 200
    boards = resp.json()["boards"]
    assert len(boards) == 1
    assert boards[0]["board_id"] == "board_1"
    assert boards[0]["board_name"] == "Planners & Organizers"
    assert boards[0]["pin_count"] == 42


# ---------------------------------------------------------------------------
# 7. cost_today_usd — somma dei costi di oggi
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_cost_today_usd_sums_todays_costs(app):
    """cost_today_usd deve sommare cost_image_gen + cost_llm dei pin creati oggi."""
    db = await _make_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    await db.execute(
        "INSERT INTO pinterest_queue (status, scheduled_at, cost_image_gen, cost_llm, created_at) "
        "VALUES (?,?,?,?,?)",
        ("published", today, 0.02, 0.01, today),
    )
    await db.execute(
        "INSERT INTO pinterest_queue (status, scheduled_at, cost_image_gen, cost_llm, created_at) "
        "VALUES (?,?,?,?,?)",
        ("published", today, 0.015, 0.005, today),
    )
    await db.execute(
        # ieri — non conta
        "INSERT INTO pinterest_queue (status, scheduled_at, cost_image_gen, cost_llm, created_at) "
        "VALUES (?,?,?,?,?)",
        ("published", yesterday, 1.0, 1.0, yesterday),
    )
    await db.commit()

    mem = _make_memory(db)

    with patch.object(state_mod, "memory", mem):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/status")

    await db.close()
    assert resp.status_code == 200
    cost = resp.json()["cost_today_usd"]
    assert abs(cost - 0.05) < 0.001  # 0.02+0.01 + 0.015+0.005 = 0.05


# ---------------------------------------------------------------------------
# 8. next_pin_at — prossimo pin schedulato (pending)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_next_pin_at_returns_earliest_pending(app):
    """next_pin_at deve essere il MIN scheduled_at tra i pin pending."""
    db = await _make_db()
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    later = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    await db.execute(
        "INSERT INTO pinterest_queue (status, scheduled_at) VALUES (?,?)",
        ("pending", later),
    )
    await db.execute(
        "INSERT INTO pinterest_queue (status, scheduled_at) VALUES (?,?)",
        ("pending", soon),
    )
    await db.commit()

    mem = _make_memory(db)

    with patch.object(state_mod, "memory", mem):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/status")

    await db.close()
    assert resp.status_code == 200
    next_pin = resp.json()["next_pin_at"]
    assert next_pin is not None
    assert soon[:16] in next_pin  # same minute prefix


@pytest.mark.asyncio
async def test_status_next_pin_at_is_none_when_no_pending(app):
    """next_pin_at deve essere None quando non ci sono pin pending."""
    db = await _make_db()
    mem = _make_memory(db)

    with patch.object(state_mod, "memory", mem):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/status")

    await db.close()
    assert resp.status_code == 200
    assert resp.json()["next_pin_at"] is None


# ---------------------------------------------------------------------------
# 9. connected — da OAuth tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_connected_true_when_valid_token(app):
    """connected deve essere True quando c'è un token Pinterest valido."""
    db = await _make_db()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mem = _make_memory(db)
    mem.get_oauth_tokens = AsyncMock(return_value={
        "access_token_encrypted": "tok",
        "expires_at": future,
    })

    with patch.object(state_mod, "memory", mem):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/status")

    await db.close()
    assert resp.status_code == 200
    assert resp.json()["connected"] is True


@pytest.mark.asyncio
async def test_status_connected_false_when_no_token(app):
    """connected deve essere False quando non ci sono token Pinterest."""
    db = await _make_db()
    mem = _make_memory(db)
    mem.get_oauth_tokens = AsyncMock(return_value=None)

    with patch.object(state_mod, "memory", mem):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/status")

    await db.close()
    assert resp.status_code == 200
    assert resp.json()["connected"] is False
