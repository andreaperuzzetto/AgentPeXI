"""EtsyAPI — auth, lifecycle, and HTTP request mixin."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from apps.backend.core.config import settings
from apps.backend.core.crypto import get_fernet
from apps.backend.tools._etsy_api._errors import EtsyAPIError, _is_retryable

logger = logging.getLogger("agentpexi.etsy_api")

ETSY_BASE_URL = "https://api.etsy.com/v3"
ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"


class _AuthMixin:

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def mock_mode(self) -> bool:
        return bool(getattr(self.pepe, 'mock_mode', False))

    @property
    def shop_id(self) -> str:
        """ETSY_SHOP_ID da settings."""
        return settings.ETSY_SHOP_ID

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        client = getattr(self, "_client", None)
        if client and not client.is_closed:
            await client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Encryption
    # ------------------------------------------------------------------

    def _encrypt(self, plaintext: str) -> str:
        return get_fernet().encrypt(plaintext.encode()).decode()

    def _decrypt(self, ciphertext: str) -> str:
        return get_fernet().decrypt(ciphertext.encode()).decode()

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _get_valid_token(self) -> str:
        """Decripta token, refresh se scaduto. Ritorna access_token."""
        async with self._token_lock:
            tokens = await self.memory.get_oauth_tokens("etsy")
            if not tokens:
                raise RuntimeError("Token Etsy non trovati. Eseguire etsy_auth_setup.")

            expires_at = datetime.fromisoformat(tokens["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)

            # Refresh con 5 minuti di margine
            if now >= expires_at - timedelta(minutes=5):
                try:
                    await self._refresh_token(tokens)
                    tokens = await self.memory.get_oauth_tokens("etsy")
                except Exception as exc:
                    logger.error("Refresh token fallito: %s", exc)
                    if self.pepe and hasattr(self.pepe, "notify_telegram"):
                        await self.pepe.notify_telegram(
                            "⚠️ Token Etsy scaduto, riesegui auth setup",
                            priority=True,
                        )
                    raise

            return tokens["access_token"]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def _refresh_token(self, tokens: dict) -> None:
        """Refresh access_token usando refresh_token."""
        refresh_token = tokens["refresh_token"]

        client = await self._get_client()
        resp = await client.post(
            ETSY_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.ETSY_API_KEY,
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        new_access = data["access_token"]
        new_refresh = data["refresh_token"]
        expires_in = data.get("expires_in", 3600)

        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

        await self.memory.update_oauth_tokens(
            provider="etsy",
            access_token_enc=new_access,
            refresh_token_enc=new_refresh,
            expires_at=expires_at,
        )
        logger.info("Token Etsy refreshed, scadenza: %s", expires_at)

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    async def _rate_limit(self) -> None:
        """Applica rate limiting: spacing minimo + contatore giornaliero."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            self._daily_count = 0
            self._daily_reset_date = today

        self._daily_count += 1

        # Alert + rallentamento se > 8000 call/giorno
        if self._daily_count == 8000:
            msg = f"⚠️ Etsy API: raggiunto limite 8000 chiamate/giorno"
            logger.warning(msg)
            if self.pepe and hasattr(self.pepe, "notify_telegram"):
                await self.pepe.notify_telegram(msg, priority=True)

        interval = self._min_interval
        if self._daily_count > 8000:
            interval = 0.5  # Rallentamento automatico

        loop = asyncio.get_running_loop()
        now = loop.time()
        elapsed = now - self._last_request_time
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
        self._last_request_time = loop.time()

    # ------------------------------------------------------------------
    # HTTP request con retry
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Esegue richiesta HTTP autenticata con rate limiting e retry."""
        async with self._semaphore:
            await self._rate_limit()

            token = await self._get_valid_token()
            client = await self._get_client()

            headers = {
                "Authorization": f"Bearer {token}",
                "x-api-key": settings.ETSY_API_KEY,
            }

            url = f"{ETSY_BASE_URL}{path}"

            resp = await client.request(
                method,
                url,
                headers=headers,
                json=json_data,
                data=data,
                files=files,
                params=params,
            )
            resp.raise_for_status()

            if resp.status_code == 204:
                return {}
            return resp.json()

    # ------------------------------------------------------------------
    # Status check
    # ------------------------------------------------------------------

    async def check_auth_status(self) -> dict:
        """Verifica se i token Etsy sono validi."""
        if self.mock_mode:
            return await self._mock_check_auth_status()
        tokens = await self.memory.get_oauth_tokens("etsy")
        if not tokens:
            return {"authenticated": False, "reason": "no_tokens"}

        expires_at = datetime.fromisoformat(tokens["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        expired = now >= expires_at

        return {
            "authenticated": True,
            "expired": expired,
            "expires_at": tokens["expires_at"],
            "updated_at": tokens.get("updated_at"),
        }
