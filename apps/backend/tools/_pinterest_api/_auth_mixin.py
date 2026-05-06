"""PinterestAPI — auth lifecycle, token management e HTTP request."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger("agentpexi.pinterest_api")

PINTEREST_BASE_URL = "https://api.pinterest.com"
PINTEREST_TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"


class _AuthMixin:

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        client = getattr(self, "_client", None)
        if client and not client.is_closed:
            await client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _get_valid_token(self) -> str:
        """Legge token Pinterest da oauth_tokens, refresh se scaduto. Ritorna access_token."""
        tokens = await self.memory.get_oauth_tokens("pinterest")
        if not tokens:
            raise RuntimeError("Token Pinterest non trovati. Eseguire pinterest_auth_setup.")

        expires_at = datetime.fromisoformat(tokens["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        if now >= expires_at - timedelta(minutes=5):
            try:
                await self._refresh_token(tokens)
                tokens = await self.memory.get_oauth_tokens("pinterest")
            except Exception as exc:
                logger.error("Refresh token Pinterest fallito: %s", exc)
                raise

        return tokens["access_token"]

    async def _refresh_token(self, tokens: dict) -> None:
        """Refresh access_token usando refresh_token Pinterest."""
        from apps.backend.core.config import settings

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                PINTEREST_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": settings.PINTEREST_CLIENT_ID,
                    "client_secret": settings.PINTEREST_CLIENT_SECRET,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        new_access = data["access_token"]
        new_refresh = data.get("refresh_token", tokens["refresh_token"])
        expires_in = data.get("expires_in", 3600)
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

        await self.memory.update_oauth_tokens(
            provider="pinterest",
            access_token_enc=new_access,
            refresh_token_enc=new_refresh,
            expires_at=expires_at,
        )
        logger.info("Token Pinterest refreshed, scadenza: %s", expires_at)

    # ------------------------------------------------------------------
    # Auth status
    # ------------------------------------------------------------------

    async def check_auth_status(self) -> dict:
        """Verifica se i token Pinterest sono presenti e validi."""
        tokens = await self.memory.get_oauth_tokens("pinterest")
        if not tokens:
            return {"connected": False, "expires_at": None, "last_refresh": None}

        expires_at_str = tokens["expires_at"]
        expires_at = datetime.fromisoformat(expires_at_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        connected = datetime.now(timezone.utc) < expires_at
        return {
            "connected": connected,
            "expires_at": expires_at_str,
            "last_refresh": tokens.get("updated_at"),
        }

    # ------------------------------------------------------------------
    # HTTP request
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Esegue richiesta HTTP autenticata verso l'API Pinterest v5."""
        token = await self._get_valid_token()

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)

        url = f"{PINTEREST_BASE_URL}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        resp = await self._client.request(
            method,
            url,
            headers=headers,
            json=json_data,
            params=params,
        )
        resp.raise_for_status()

        if resp.status_code == 204:
            return {}
        return resp.json()
