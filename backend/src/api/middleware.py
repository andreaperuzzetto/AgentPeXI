from __future__ import annotations

import time

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

log = structlog.get_logger()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Loga ogni request/response senza PII."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)

        # Non loggare il corpo per evitare PII — solo metodo, path e status
        log.info(
            "api.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
        )
        return response


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Converte eccezioni non gestite nello schema errore standard."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001
            log.error("api.unhandled_error", path=request.url.path, error=str(exc))
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal_server_error",
                    "message": "Errore interno del server",
                    "detail": {},
                },
            )
