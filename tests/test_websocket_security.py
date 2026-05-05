"""L5: WebSocket auth key must travel via Sec-WebSocket-Protocol header, not URL query string."""
from __future__ import annotations

import inspect


def test_ws_auth_key_extracted_from_sec_websocket_protocol_header():
    """L5: helper reads key from header dict, not URL."""
    from apps.backend.api.main import _ws_auth_key_from_header

    assert _ws_auth_key_from_header({"sec-websocket-protocol": "my-secret"}) == "my-secret"
    assert _ws_auth_key_from_header({}) == ""


def test_ws_handlers_do_not_read_key_from_query_string():
    """L5: neither ws_voice nor ws_chat should declare `key` as a FastAPI Query param."""
    from fastapi.params import Query as QueryType
    from apps.backend.api.main import ws_voice, ws_chat

    for handler in (ws_voice, ws_chat):
        sig = inspect.signature(handler)
        for name, param in sig.parameters.items():
            if name == "key":
                assert not isinstance(param.default, QueryType), (
                    f"{handler.__name__} still reads 'key' from URL query string — "
                    "move to Sec-WebSocket-Protocol header"
                )
