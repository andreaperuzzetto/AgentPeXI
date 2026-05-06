"""OAuth tokens mixin for MemoryManager."""
from __future__ import annotations

import logging

logger = logging.getLogger("agentpexi.memory")


class OAuthMixin:
    # ------------------------------------------------------------------
    # OAuth tokens
    # ------------------------------------------------------------------

    async def save_oauth_tokens(
        self,
        provider: str,
        access_token_enc: str,
        refresh_token_enc: str,
        expires_at: str,
    ) -> None:
        """Cifra e salva i token OAuth. Usa UPSERT per evitare duplicati.

        `access_token_enc` e `refresh_token_enc` devono essere passati in chiaro:
        la cifratura Fernet viene applicata internamente prima della scrittura su DB.
        """
        fernet = self._fernet()
        enc_access = fernet.encrypt(access_token_enc.encode()).decode()
        enc_refresh = fernet.encrypt(refresh_token_enc.encode()).decode()
        await self._db.execute(
            """INSERT INTO oauth_tokens
               (provider, access_token_encrypted, refresh_token_encrypted, expires_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(provider) DO UPDATE SET
               access_token_encrypted = excluded.access_token_encrypted,
               refresh_token_encrypted = excluded.refresh_token_encrypted,
               expires_at = excluded.expires_at,
               updated_at = CURRENT_TIMESTAMP""",
            (provider, enc_access, enc_refresh, expires_at),
        )
        await self._db.commit()

    async def get_oauth_tokens(self, provider: str) -> dict | None:
        """Ritorna i token OAuth in chiaro per `provider`, o None se non esistono.

        Il dict restituito contiene le chiavi ``access_token`` e ``refresh_token``
        (già decifrate). Le colonne DB si chiamano ancora ``*_encrypted`` per
        compatibilità con lo schema esistente.
        """
        cursor = await self._db.execute(
            "SELECT * FROM oauth_tokens WHERE provider = ?", (provider,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        fernet = self._fernet()
        try:
            data["access_token"] = fernet.decrypt(
                data["access_token_encrypted"].encode()
            ).decode()
            data["refresh_token"] = fernet.decrypt(
                data["refresh_token_encrypted"].encode()
            ).decode()
        except Exception as exc:
            logger.error("Decifratura token OAuth fallita per %s: %s", provider, exc)
            raise
        return data

    async def update_oauth_tokens(
        self,
        provider: str,
        access_token_enc: str,
        refresh_token_enc: str,
        expires_at: str,
    ) -> None:
        """Cifra e aggiorna i token OAuth esistenti.

        `access_token_enc` e `refresh_token_enc` devono essere passati in chiaro.
        """
        fernet = self._fernet()
        enc_access = fernet.encrypt(access_token_enc.encode()).decode()
        enc_refresh = fernet.encrypt(refresh_token_enc.encode()).decode()
        await self._db.execute(
            """UPDATE oauth_tokens SET
               access_token_encrypted = ?, refresh_token_encrypted = ?,
               expires_at = ?, updated_at = CURRENT_TIMESTAMP
               WHERE provider = ?""",
            (enc_access, enc_refresh, expires_at, provider),
        )
        await self._db.commit()
