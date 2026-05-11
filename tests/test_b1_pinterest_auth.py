"""B-04: verifica Pinterest OAuth stubs e GET /api/pinterest/auth-status."""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import apps.backend.api.state as state_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    """App FastAPI con pinterest router, auth bypassed."""
    from apps.backend.api.routers import pinterest

    _app = FastAPI()
    _app.include_router(pinterest.router)
    _app.dependency_overrides[state_mod.verify_personal_key] = lambda: None
    yield _app
    _app.dependency_overrides.clear()


def _make_api(memory):
    """PinterestAPI minimal instance con memory mockato."""
    import asyncio
    from apps.backend.tools.pinterest_api import PinterestAPI

    api = PinterestAPI.__new__(PinterestAPI)
    api.memory = memory
    api.pepe = None
    api._client = None
    api._token_lock = asyncio.Lock()
    return api


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


def test_pkce_verifier_is_base64url_string():
    """_generate_code_verifier() ritorna una stringa base64url ≥43 caratteri."""
    from apps.backend.tools.pinterest_auth_setup import _generate_code_verifier

    v = _generate_code_verifier()
    assert isinstance(v, str)
    assert len(v) >= 43  # RFC 7636 minimum


def test_pkce_challenge_matches_sha256_of_verifier():
    """_generate_code_challenge(v) == SHA256(v) base64url senza padding."""
    from apps.backend.tools.pinterest_auth_setup import (
        _generate_code_challenge,
        _generate_code_verifier,
    )

    v = _generate_code_verifier()
    c = _generate_code_challenge(v)
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(v.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert c == expected


# ---------------------------------------------------------------------------
# _AuthMixin — _get_valid_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_valid_token_raises_when_no_token():
    """_get_valid_token() lancia RuntimeError se nessun token Pinterest salvato."""
    memory = MagicMock()
    memory.get_oauth_tokens = AsyncMock(return_value=None)
    api = _make_api(memory)

    with pytest.raises(RuntimeError, match="[Pp]interest"):
        await api._get_valid_token()


@pytest.mark.asyncio
async def test_get_valid_token_returns_access_token_when_valid():
    """_get_valid_token() ritorna access_token quando non scaduto."""
    future_dt = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    memory = MagicMock()
    memory.get_oauth_tokens = AsyncMock(
        return_value={
            "access_token": "my_access_token",
            "refresh_token": "my_refresh_token",
            "expires_at": future_dt,
            "updated_at": None,
        }
    )
    api = _make_api(memory)

    token = await api._get_valid_token()
    assert token == "my_access_token"


# ---------------------------------------------------------------------------
# _AuthMixin — check_auth_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_auth_status_not_connected_when_no_tokens():
    """check_auth_status() ritorna connected=False quando nessun token esiste."""
    memory = MagicMock()
    memory.get_oauth_tokens = AsyncMock(return_value=None)
    api = _make_api(memory)

    status = await api.check_auth_status()

    assert status["connected"] is False
    assert status["expires_at"] is None
    assert status["last_refresh"] is None


@pytest.mark.asyncio
async def test_check_auth_status_connected_when_valid():
    """check_auth_status() ritorna connected=True e campi corretti con token valido."""
    future_dt = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    memory = MagicMock()
    memory.get_oauth_tokens = AsyncMock(
        return_value={
            "access_token": "tok",
            "refresh_token": "rtok",
            "expires_at": future_dt,
            "updated_at": "2026-05-01T10:00:00",
        }
    )
    api = _make_api(memory)

    status = await api.check_auth_status()

    assert status["connected"] is True
    assert status["expires_at"] == future_dt
    assert status["last_refresh"] == "2026-05-01T10:00:00"


@pytest.mark.asyncio
async def test_check_auth_status_not_connected_when_expired():
    """check_auth_status() ritorna connected=False quando token scaduto."""
    past_dt = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    memory = MagicMock()
    memory.get_oauth_tokens = AsyncMock(
        return_value={
            "access_token": "tok",
            "refresh_token": "rtok",
            "expires_at": past_dt,
            "updated_at": None,
        }
    )
    api = _make_api(memory)

    status = await api.check_auth_status()

    assert status["connected"] is False


# ---------------------------------------------------------------------------
# API stubs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_pin_calls_post_v5_pins():
    """create_pin() chiama _request POST /v5/pins con i parametri corretti."""
    memory = MagicMock()
    api = _make_api(memory)
    api._request = AsyncMock(return_value={"id": "pin_123"})

    result = await api.create_pin(
        board_id="board_1",
        title="Test pin",
        description="Descrizione test",
        image_url="https://example.com/img.jpg",
        link="https://etsy.com/listing/123",
    )

    api._request.assert_called_once()
    call_args = api._request.call_args
    assert call_args[0][0] == "POST"
    assert "/v5/pins" in call_args[0][1]
    assert result == {"id": "pin_123"}


@pytest.mark.asyncio
async def test_list_boards_calls_get_v5_boards():
    """list_boards() chiama _request GET /v5/boards."""
    memory = MagicMock()
    api = _make_api(memory)
    api._request = AsyncMock(return_value={"items": [], "bookmark": None})

    result = await api.list_boards()

    api._request.assert_called_once()
    call_args = api._request.call_args
    assert call_args[0][0] == "GET"
    assert "/v5/boards" in call_args[0][1]


@pytest.mark.asyncio
async def test_create_board_calls_post_v5_boards():
    """create_board() chiama _request POST /v5/boards con name e description."""
    memory = MagicMock()
    api = _make_api(memory)
    api._request = AsyncMock(return_value={"id": "board_456", "name": "Party"})

    result = await api.create_board(name="Party & Celebrations", description="Test board")

    api._request.assert_called_once()
    call_args = api._request.call_args
    assert call_args[0][0] == "POST"
    assert "/v5/boards" in call_args[0][1]
    assert result == {"id": "board_456", "name": "Party"}


# ---------------------------------------------------------------------------
# Router — GET /api/pinterest/auth-status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_status_503_when_memory_none(app):
    """GET /api/pinterest/auth-status ritorna 503 se state.memory è None."""
    original = state_mod.memory
    state_mod.memory = None
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/auth-status")
    finally:
        state_mod.memory = original

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_auth_status_not_connected_when_no_tokens(app):
    """GET /api/pinterest/auth-status ritorna connected=False se nessun token."""
    mock_memory = MagicMock()
    mock_memory.get_oauth_tokens = AsyncMock(return_value=None)
    original = state_mod.memory
    state_mod.memory = mock_memory
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/auth-status")
    finally:
        state_mod.memory = original

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["expires_at"] is None
    assert data["last_refresh"] is None


@pytest.mark.asyncio
async def test_auth_status_connected_when_valid_token(app):
    """GET /api/pinterest/auth-status ritorna connected=True con token valido."""
    future_dt = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_memory = MagicMock()
    mock_memory.get_oauth_tokens = AsyncMock(
        return_value={
            "access_token": "tok",
            "refresh_token": "rtok",
            "expires_at": future_dt,
            "updated_at": "2026-05-01T10:00:00",
        }
    )
    original = state_mod.memory
    state_mod.memory = mock_memory
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pinterest/auth-status")
    finally:
        state_mod.memory = original

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is True
    assert data["expires_at"] == future_dt
    assert data["last_refresh"] == "2026-05-01T10:00:00"
