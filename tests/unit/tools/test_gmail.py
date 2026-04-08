"""Test unit per tools/gmail.py — mock del sottoprocesso MCP stdio."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.gmail import GmailSendError, list_unread, read_thread, send_email


# ---------------------------------------------------------------------------
# Helper: mock processo MCP
# ---------------------------------------------------------------------------

def _make_mcp_process(response: dict):
    """
    Simula un processo MCP che risponde con il response JSON fornito.
    """
    proc = AsyncMock()
    proc.returncode = None
    proc.stdin = AsyncMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()

    encoded = (json.dumps(response) + "\n").encode()
    proc.stdout = AsyncMock()
    proc.stdout.readline = AsyncMock(return_value=encoded)
    return proc


def _success_response(result_data: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{"type": "text", "text": json.dumps(result_data)}]
        },
    }


def _error_response(code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": code, "message": message},
    }


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_email_happy_path():
    send_result = {"message_id": "msg-001", "thread_id": "thread-001"}
    proc = _make_mcp_process(_success_response(send_result))

    with patch("tools.gmail._get_process", new_callable=AsyncMock, return_value=proc):
        result = await send_email(
            to="cliente@test.local",
            subject="La tua proposta",
            body="<p>Gentile cliente,</p>",
        )

    assert result["message_id"] == "msg-001"
    assert result["thread_id"] == "thread-001"


@pytest.mark.asyncio
async def test_send_email_mcp_error_raises_gmail_send_error():
    proc = _make_mcp_process(_error_response(-32001, "Authentication failed"))

    with patch("tools.gmail._get_process", new_callable=AsyncMock, return_value=proc):
        with pytest.raises(GmailSendError) as exc_info:
            await send_email(to="cliente@test.local", subject="Test", body="Body")

    assert exc_info.value.code == "tool_gmail_send_error"


@pytest.mark.asyncio
async def test_send_email_stdout_closed():
    proc = AsyncMock()
    proc.returncode = None
    proc.stdin = AsyncMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdout = AsyncMock()
    # Simula stdout chiuso (restituisce stringa vuota)
    proc.stdout.readline = AsyncMock(return_value=b"")

    with patch("tools.gmail._get_process", new_callable=AsyncMock, return_value=proc):
        with pytest.raises(GmailSendError):
            await send_email(to="test@test.local", subject="Test", body="B")


# ---------------------------------------------------------------------------
# read_thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_thread_happy_path():
    thread_result = {
        "thread_id": "thread-001",
        "messages": [
            {
                "message_id": "msg-001",
                "from": "mittente@test.local",
                "date": "2026-04-08",
                "snippet": "Salve...",
                "body": "Salve, sono interessato.",
            }
        ],
    }
    proc = _make_mcp_process(_success_response(thread_result))

    with patch("tools.gmail._get_process", new_callable=AsyncMock, return_value=proc):
        result = await read_thread("thread-001")

    assert result["thread_id"] == "thread-001"
    assert len(result["messages"]) == 1


# ---------------------------------------------------------------------------
# list_unread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_unread_happy_path():
    unread_result = [
        {
            "message_id": "msg-002",
            "thread_id": "thread-002",
            "subject": "Risposta proposta",
            "from": "cliente@test.local",
            "date": "2026-04-08",
            "snippet": "Accetto la proposta...",
        }
    ]
    proc = _make_mcp_process(_success_response(unread_result))

    with patch("tools.gmail._get_process", new_callable=AsyncMock, return_value=proc):
        result = await list_unread(max_results=10)

    assert len(result) == 1
    assert result[0]["message_id"] == "msg-002"


@pytest.mark.asyncio
async def test_list_unread_empty():
    proc = _make_mcp_process(_success_response([]))

    with patch("tools.gmail._get_process", new_callable=AsyncMock, return_value=proc):
        result = await list_unread()

    assert result == []
