"""EtsyAPI — Wrapper async per Etsy v3 API con rate limiting, retry e token management."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from cryptography.fernet import Fernet
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from apps.backend.core.config import settings
from apps.backend.core.memory import MemoryManager

logger = logging.getLogger("agentpexi.etsy_api")

ETSY_BASE_URL = "https://api.etsy.com/v3"
ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"


class EtsyAPI:
    """Client async per Etsy v3 API."""

    def __init__(self, memory: MemoryManager, pepe: Any = None) -> None:
        self.memory = memory
        self.pepe = pepe

        # Rate limiting: max 10 req/sec
        self._semaphore = asyncio.Semaphore(10)
        self._last_request_time: float = 0.0
        self._min_interval: float = 0.1  # 100ms tra chiamate

        # Contatore giornaliero API calls (in memoria, reset a mezzanotte)
        self._daily_count: int = 0
        self._daily_reset_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # HTTP client (lazy init)
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    def shop_id(self) -> str:
        """ETSY_SHOP_ID da settings."""
        return settings.ETSY_SHOP_ID

    # ------------------------------------------------------------------
    # Encryption
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_fernet_key(secret: str) -> bytes:
        digest = hashlib.sha256(secret.encode()).digest()
        return base64.urlsafe_b64encode(digest)

    def _encrypt(self, plaintext: str) -> str:
        key = self._derive_fernet_key(settings.SECRET_KEY)
        return Fernet(key).encrypt(plaintext.encode()).decode()

    def _decrypt(self, ciphertext: str) -> str:
        key = self._derive_fernet_key(settings.SECRET_KEY)
        return Fernet(key).decrypt(ciphertext.encode()).decode()

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _get_valid_token(self) -> str:
        """Decripta token, refresh se scaduto. Ritorna access_token."""
        tokens = await self.memory.get_oauth_tokens("etsy")
        if not tokens:
            raise RuntimeError("Token Etsy non trovati. Eseguire etsy_auth_setup.")

        expires_at = datetime.fromisoformat(tokens["expires_at"])
        # Se manca timezone, assume UTC
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

        return self._decrypt(tokens["access_token_encrypted"])

    async def _refresh_token(self, tokens: dict) -> None:
        """Refresh access_token usando refresh_token."""
        refresh_token = self._decrypt(tokens["refresh_token_encrypted"])

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
            access_token_enc=self._encrypt(new_access),
            refresh_token_enc=self._encrypt(new_refresh),
            expires_at=expires_at,
        )
        logger.info("Token Etsy refreshed, scadenza: %s", expires_at)

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    async def _rate_limit(self) -> None:
        """Applica rate limiting: spacing minimo + contatore giornaliero."""
        # Reset contatore giornaliero a mezzanotte
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

        # Spacing minimo tra chiamate
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
        retry=retry_if_exception_type(httpx.HTTPStatusError),
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
    # Metodi pubblici — Listings
    # ------------------------------------------------------------------

    async def create_listing(
        self,
        title: str,
        description: str,
        price: float,
        tags: list[str],
        taxonomy_id: int,
        quantity: int = 999,
        who_made: str = "i_did",
        when_made: str = "2020_2025",
        is_supply: bool = False,
        is_digital: bool = True,
        **kwargs: Any,
    ) -> dict:
        shop_id = settings.ETSY_SHOP_ID
        payload = {
            "title": title,
            "description": description,
            "price": price,
            "quantity": quantity,
            "tags": tags,
            "taxonomy_id": taxonomy_id,
            "who_made": who_made,
            "when_made": when_made,
            "is_supply": is_supply,
            "is_digital": is_digital,
            "type": "download",
            **kwargs,
        }
        return await self._request("POST", f"/application/shops/{shop_id}/listings", json_data=payload)

    async def upload_file(self, listing_id: int, file_path: str, name: str) -> dict:
        shop_id = settings.ETSY_SHOP_ID
        with open(file_path, "rb") as f:
            files = {"file": (name, f, "application/octet-stream")}
            return await self._request(
                "POST",
                f"/application/shops/{shop_id}/listings/{listing_id}/files",
                files=files,
                data={"name": name},
            )

    async def get_listing(self, listing_id: int) -> dict:
        return await self._request("GET", f"/application/listings/{listing_id}")

    async def update_listing(self, listing_id: int, **kwargs: Any) -> dict:
        shop_id = settings.ETSY_SHOP_ID
        return await self._request(
            "PATCH",
            f"/application/shops/{shop_id}/listings/{listing_id}",
            json_data=kwargs,
        )

    async def get_listings(self, shop_id: str | None = None, limit: int = 100) -> list[dict]:
        sid = shop_id or settings.ETSY_SHOP_ID
        result = await self._request(
            "GET",
            f"/application/shops/{sid}/listings",
            params={"limit": limit},
        )
        return result.get("results", [])

    # ------------------------------------------------------------------
    # Metodi pubblici — Messaggi
    # ------------------------------------------------------------------

    async def get_messages(self, shop_id: str | None = None) -> list[dict]:
        raise NotImplementedError("Etsy v3 non espone un endpoint messaggi pubblico")

    async def reply_message(
        self, shop_id: str, conversation_id: str, message: str
    ) -> dict:
        raise NotImplementedError("Etsy v3 non espone un endpoint messaggi pubblico")

    # ------------------------------------------------------------------
    # Metodi pubblici — Shop & Stats
    # ------------------------------------------------------------------

    async def get_shop(self, shop_id: str | None = None) -> dict:
        sid = shop_id or settings.ETSY_SHOP_ID
        return await self._request("GET", f"/application/shops/{sid}")

    async def get_shop_stats(self, shop_id: str | None = None) -> dict:
        """Info shop (Etsy v3 non ha endpoint stats dedicato, usa shop info)."""
        return await self.get_shop(shop_id)

    async def get_shop_receipts(
        self, shop_id: str | None = None, min_created: int | None = None
    ) -> list[dict]:
        sid = shop_id or settings.ETSY_SHOP_ID
        params: dict[str, Any] = {"limit": 100}
        if min_created is not None:
            params["min_created"] = min_created
        result = await self._request(
            "GET",
            f"/application/shops/{sid}/receipts",
            params=params,
        )
        return result.get("results", [])

    async def get_shop_transactions(
        self, shop_id: str | None = None, listing_id: int | None = None,
    ) -> dict:
        """Transazioni per un listing specifico o per tutto lo shop."""
        sid = shop_id or settings.ETSY_SHOP_ID
        if listing_id is not None:
            return await self._request(
                "GET",
                f"/application/shops/{sid}/listings/{listing_id}/transactions",
                params={"limit": 100},
            )
        return await self._request(
            "GET",
            f"/application/shops/{sid}/transactions",
            params={"limit": 100},
        )

    # ------------------------------------------------------------------
    # Status check (per endpoint API)
    # ------------------------------------------------------------------

    async def check_auth_status(self) -> dict:
        """Verifica se i token Etsy sono validi."""
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
