from __future__ import annotations

import os

import structlog
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from api.auth import create_access_token, verify_password
from api.schemas.auth import LoginRequest

log = structlog.get_logger()

router = APIRouter(tags=["auth"])


@router.post("/auth/token")
async def login(body: LoginRequest) -> JSONResponse:
    """
    Autentica l'operatore e imposta il cookie httpOnly access_token.
    Credenziali dal file .env: OPERATOR_EMAIL e OPERATOR_PASSWORD_HASH.
    """
    expected_email = os.environ.get("OPERATOR_EMAIL", "")

    if body.email != expected_email or not verify_password(body.password):
        log.warning("api.auth.failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "invalid_credentials",
                "message": "Credenziali non valide",
                "detail": {},
            },
        )

    token = create_access_token(body.email)
    response = JSONResponse(content={"status": "ok"})
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=os.environ.get("ENVIRONMENT") == "production",
        samesite="lax",
        max_age=86400,
    )
    log.info("api.auth.login_ok")
    return response


@router.post("/auth/logout")
async def logout() -> JSONResponse:
    """Cancella il cookie access_token."""
    response = JSONResponse(content={"status": "ok"})
    response.delete_cookie(key="access_token")
    return response
