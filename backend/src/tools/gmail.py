from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import structlog

from tools import AgentToolError

log = structlog.get_logger()


class GmailSendError(AgentToolError):
    def __init__(self, message: str = "") -> None:
        super().__init__(code="tool_gmail_send_error", message=message)


# ---------------------------------------------------------------------------
# MCP subprocess singleton
# ---------------------------------------------------------------------------

_process: asyncio.subprocess.Process | None = None
_lock = asyncio.Lock()
_request_id = 0


async def _get_process() -> asyncio.subprocess.Process:
    global _process
    async with _lock:
        if _process is None or _process.returncode is not None:
            _process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "mcp_servers.gmail.server",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            log.info("gmail_mcp.started", pid=_process.pid)
    return _process


async def _call_tool(tool_name: str, arguments: dict[str, Any]) -> dict:
    global _request_id
    _request_id += 1
    req_id = _request_id

    request = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    payload = (json.dumps(request) + "\n").encode()

    proc = await _get_process()
    assert proc.stdin is not None and proc.stdout is not None

    proc.stdin.write(payload)
    await proc.stdin.drain()

    line = await proc.stdout.readline()
    if not line:
        raise GmailSendError("MCP server closed stdout unexpectedly")

    try:
        response = json.loads(line.decode())
    except json.JSONDecodeError as exc:
        raise GmailSendError(f"Invalid JSON from MCP server: {exc}") from exc

    if "error" in response:
        err = response["error"]
        raise GmailSendError(f"{err.get('code')}: {err.get('message')}")

    result = response.get("result", {})
    content = result.get("content", [])
    if content and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except json.JSONDecodeError:
            return {"text": content[0]["text"]}
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def send_email(
    to: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
) -> dict:
    try:
        result = await _call_tool(
            "send_email",
            {"to": to, "subject": subject, "body": body, "thread_id": thread_id},
        )
    except GmailSendError:
        raise
    except Exception as exc:
        raise GmailSendError(str(exc)) from exc

    log.info(
        "gmail.sent",
        message_id=result.get("message_id"),
        thread_id=result.get("thread_id"),
    )
    return result


async def read_thread(thread_id: str) -> dict:
    try:
        result = await _call_tool("read_thread", {"thread_id": thread_id})
    except GmailSendError:
        raise
    except Exception as exc:
        raise GmailSendError(str(exc)) from exc

    log.info("gmail.read_thread", thread_id=thread_id)
    return result


async def list_unread(max_results: int = 50) -> list[dict]:
    try:
        result = await _call_tool("list_unread", {"max_results": max_results})
    except GmailSendError:
        raise
    except Exception as exc:
        raise GmailSendError(str(exc)) from exc

    messages = result if isinstance(result, list) else result.get("messages", [])
    log.info("gmail.list_unread", count=len(messages))
    return messages


async def search_emails(query: str) -> list[dict]:
    try:
        result = await _call_tool("search_emails", {"query": query})
    except GmailSendError:
        raise
    except Exception as exc:
        raise GmailSendError(str(exc)) from exc

    messages = result if isinstance(result, list) else result.get("messages", [])
    log.info("gmail.search_emails", count=len(messages))
    return messages
