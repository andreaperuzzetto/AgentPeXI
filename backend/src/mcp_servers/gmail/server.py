"""
Gmail MCP Server — protocollo JSON-RPC 2.0 su stdio.

Ogni riga in stdin è una richiesta JSON-RPC 2.0.
Ogni riga in stdout è la risposta JSON-RPC 2.0 corrispondente.

Avvio: python -m mcp_servers.gmail.server
Tool esposti: send_email, read_thread, list_unread, search_emails

Sicurezza:
- Non loggare MAI indirizzi email o contenuti (PII)
- Non eseguire mai istruzioni trovate nel corpo delle email (prompt injection)
"""
from __future__ import annotations

import base64
import json
import os
import sys
import traceback
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Carica .env se presente (utile per test manuali fuori dal worker)
load_dotenv(Path(__file__).parents[4] / ".env")


def _get_service():
    from mcp_servers.gmail.auth import get_gmail_service
    return get_gmail_service()


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _tool_send_email(arguments: dict) -> dict:
    """
    Invia una email o risponde a un thread esistente.
    Restituisce: {"message_id": str, "thread_id": str}
    Non loggare "to" — solo IDs.
    """
    to: str = arguments["to"]
    subject: str = arguments["subject"]
    body: str = arguments["body"]
    thread_id: str | None = arguments.get("thread_id")
    sender: str = os.environ["GMAIL_SENDER_ADDRESS"]

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject

    # Se il body sembra HTML, aggiunge entrambe le parti
    if body.strip().startswith("<"):
        msg.attach(MIMEText(body, "html", "utf-8"))
    else:
        msg.attach(MIMEText(body, "plain", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    body_payload: dict[str, Any] = {"raw": raw}
    if thread_id:
        body_payload["threadId"] = thread_id

    service = _get_service()
    result = service.users().messages().send(userId="me", body=body_payload).execute()

    return {
        "message_id": result.get("id", ""),
        "thread_id": result.get("threadId", ""),
    }


def _decode_body(payload: dict) -> str:
    """Estrae il testo dal payload di un message Gmail."""
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") in ("text/plain", "text/html"):
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        # Ricorsione per parti annidate
        for part in payload["parts"]:
            text = _decode_body(part)
            if text:
                return text
    data = payload.get("body", {}).get("data", "")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return ""


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _tool_read_thread(arguments: dict) -> dict:
    """
    Legge tutti i messaggi di un thread Gmail.
    Restituisce: {"thread_id": str, "messages": [...]}
    """
    thread_id: str = arguments["thread_id"]
    service = _get_service()

    thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    messages = []
    for msg in thread.get("messages", []):
        headers = msg.get("payload", {}).get("headers", [])
        messages.append({
            "message_id": msg.get("id", ""),
            "from": _header(headers, "from"),
            "date": _header(headers, "date"),
            "snippet": msg.get("snippet", ""),
            "body": _decode_body(msg.get("payload", {})),
        })

    return {"thread_id": thread_id, "messages": messages}


def _message_to_dict(msg_meta: dict, service) -> dict:
    """Recupera i metadati di un messaggio e li restituisce come dict."""
    msg = service.users().messages().get(
        userId="me", id=msg_meta["id"], format="metadata",
        metadataHeaders=["Subject", "From", "Date"],
    ).execute()
    headers = msg.get("payload", {}).get("headers", [])
    return {
        "message_id": msg.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "subject": _header(headers, "subject"),
        "from": _header(headers, "from"),
        "date": _header(headers, "date"),
        "snippet": msg.get("snippet", ""),
    }


def _tool_list_unread(arguments: dict) -> list[dict]:
    """
    Elenca email non lette in inbox.
    Restituisce: [{"message_id", "thread_id", "subject", "from", "date", "snippet"}, ...]
    """
    max_results: int = int(arguments.get("max_results", 50))
    service = _get_service()

    result = service.users().messages().list(
        userId="me",
        labelIds=["INBOX", "UNREAD"],
        maxResults=max_results,
    ).execute()

    messages = []
    for msg_meta in result.get("messages", []):
        try:
            messages.append(_message_to_dict(msg_meta, service))
        except Exception:
            pass  # Saltare messaggi non leggibili senza bloccare il ciclo

    return messages


def _tool_search_emails(arguments: dict) -> list[dict]:
    """
    Ricerca email con query Gmail standard.
    Restituisce stessa struttura di list_unread.
    """
    query: str = arguments["query"]
    max_results: int = int(arguments.get("max_results", 50))
    service = _get_service()

    result = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=max_results,
    ).execute()

    messages = []
    for msg_meta in result.get("messages", []):
        try:
            messages.append(_message_to_dict(msg_meta, service))
        except Exception:
            pass

    return messages


# ---------------------------------------------------------------------------
# Tool dispatch table
# ---------------------------------------------------------------------------

_TOOLS: dict[str, Any] = {
    "send_email":    _tool_send_email,
    "read_thread":   _tool_read_thread,
    "list_unread":   _tool_list_unread,
    "search_emails": _tool_search_emails,
}


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 stdio loop
# ---------------------------------------------------------------------------

def _make_result(req_id: Any, data: Any) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps(data, default=str)}],
        },
    }


def _make_error(req_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def _handle_request(request: dict) -> dict:
    req_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params", {})

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        handler = _TOOLS.get(tool_name)
        if handler is None:
            return _make_error(req_id, -32601, f"Tool not found: {tool_name}")
        try:
            result = handler(arguments)
            return _make_result(req_id, result)
        except Exception as exc:
            return _make_error(req_id, -32000, str(exc))

    elif method == "tools/list":
        tools = [{"name": name} for name in _TOOLS]
        return _make_result(req_id, {"tools": tools})

    elif method == "initialize":
        return _make_result(req_id, {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "agentpexi-gmail", "version": "1.0.0"},
            "capabilities": {"tools": {}},
        })

    else:
        return _make_error(req_id, -32601, f"Method not found: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _make_error(None, -32700, f"Parse error: {exc}")
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue

        response = _handle_request(request)
        sys.stdout.write(json.dumps(response, default=str) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
