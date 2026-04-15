"""Etsy OAuth 2.0 + PKCE — Script di autenticazione one-time.

Eseguire manualmente:
    python -m apps.backend.tools.etsy_auth_setup

Flusso:
1. Genera code_verifier + code_challenge (PKCE)
2. Avvia server locale su localhost:3000
3. Apre il browser sull'authorize URL di Etsy
4. Riceve il callback con authorization code
5. Scambia code → access_token + refresh_token
6. Cifra i token con Fernet (chiave da SECRET_KEY)
7. Salva in SQLite (tabella oauth_tokens)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import sys
import webbrowser

import httpx
from aiohttp import web
from cryptography.fernet import Fernet

# Aggiungi root progetto al path per import
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from apps.backend.core.config import settings  # noqa: E402
from apps.backend.core.memory import MemoryManager  # noqa: E402

# ------------------------------------------------------------------
# PKCE helpers
# ------------------------------------------------------------------

def _generate_code_verifier() -> str:
    """Random 64 byte → base64url (senza padding)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")


def _generate_code_challenge(verifier: str) -> str:
    """SHA256(verifier) → base64url (senza padding)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ------------------------------------------------------------------
# Fernet encryption con SECRET_KEY
# ------------------------------------------------------------------

def _derive_fernet_key(secret: str) -> bytes:
    """Deriva chiave Fernet 32-byte da SECRET_KEY via SHA256."""
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _encrypt(plaintext: str, secret: str) -> str:
    key = _derive_fernet_key(secret)
    f = Fernet(key)
    return f.encrypt(plaintext.encode()).decode()


# ------------------------------------------------------------------
# Etsy OAuth URLs
# ------------------------------------------------------------------

ETSY_AUTHORIZE_URL = "https://www.etsy.com/oauth/connect"
ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
CALLBACK_URL = "http://localhost:3000/callback"
SCOPES = "listings_r listings_w shops_r shops_w transactions_r"


# ------------------------------------------------------------------
# Autenticazione
# ------------------------------------------------------------------

async def run_auth() -> None:
    api_key = settings.ETSY_API_KEY
    secret_key = settings.SECRET_KEY

    if not api_key:
        print("❌ ETSY_API_KEY non configurata nel .env")
        return
    if not secret_key:
        print("❌ SECRET_KEY non configurata nel .env")
        return

    code_verifier = _generate_code_verifier()
    code_challenge = _generate_code_challenge(code_verifier)

    # Future per ricevere il code dal callback
    loop = asyncio.get_running_loop()
    code_future: asyncio.Future[str] = loop.create_future()

    # ------------------------------------------------------------------
    # Server locale aiohttp
    # ------------------------------------------------------------------

    async def handle_root(request: web.Request) -> web.Response:
        """Redirect all'authorize URL di Etsy."""
        params = {
            "response_type": "code",
            "client_id": api_key,
            "redirect_uri": CALLBACK_URL,
            "scope": SCOPES,
            "state": secrets.token_urlsafe(16),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        url = f"{ETSY_AUTHORIZE_URL}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
        raise web.HTTPFound(url)

    async def handle_callback(request: web.Request) -> web.Response:
        """Riceve authorization code dal callback Etsy."""
        error = request.query.get("error")
        if error:
            code_future.set_exception(RuntimeError(f"Etsy auth error: {error}"))
            return web.Response(
                text="❌ Autenticazione fallita. Puoi chiudere questa finestra.",
                content_type="text/plain",
            )

        code = request.query.get("code")
        if not code:
            code_future.set_exception(RuntimeError("Nessun authorization code ricevuto"))
            return web.Response(
                text="❌ Nessun code ricevuto. Puoi chiudere questa finestra.",
                content_type="text/plain",
            )

        code_future.set_result(code)
        return web.Response(
            text="✅ Autenticazione completata! Puoi chiudere questa finestra.",
            content_type="text/plain",
        )

    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/callback", handle_callback)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 3000)
    await site.start()
    print("🌐 Server locale avviato su http://localhost:3000")

    # Apri browser
    authorize_url = f"http://localhost:3000/"
    print(f"🔗 Apertura browser per autenticazione Etsy...")
    webbrowser.open(authorize_url)
    print("⏳ In attesa del callback da Etsy...")

    # Attendi il code
    try:
        authorization_code = await asyncio.wait_for(code_future, timeout=300)
    except asyncio.TimeoutError:
        print("❌ Timeout: nessun callback ricevuto in 5 minuti")
        await runner.cleanup()
        return
    except RuntimeError as exc:
        print(f"❌ {exc}")
        await runner.cleanup()
        return

    print(f"✅ Authorization code ricevuto")

    # Ferma il server locale
    await runner.cleanup()

    # ------------------------------------------------------------------
    # Scambio code → token
    # ------------------------------------------------------------------

    print("🔄 Scambio code → token...")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            ETSY_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": api_key,
                "redirect_uri": CALLBACK_URL,
                "code": authorization_code,
                "code_verifier": code_verifier,
            },
        )

    if resp.status_code != 200:
        print(f"❌ Errore scambio token: {resp.status_code} — {resp.text}")
        return

    token_data = resp.json()
    access_token = token_data["access_token"]
    refresh_token = token_data["refresh_token"]
    expires_in = token_data.get("expires_in", 3600)

    # Calcola expires_at
    from datetime import datetime, timedelta, timezone

    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

    # ------------------------------------------------------------------
    # Cifra e salva in SQLite
    # ------------------------------------------------------------------

    print("🔐 Cifratura e salvataggio token...")
    access_enc = _encrypt(access_token, secret_key)
    refresh_enc = _encrypt(refresh_token, secret_key)

    memory = MemoryManager()
    await memory.init()

    await memory.save_oauth_tokens(
        provider="etsy",
        access_token_enc=access_enc,
        refresh_token_enc=refresh_enc,
        expires_at=expires_at,
    )

    await memory.close()

    print("✅ Token Etsy salvati e cifrati in SQLite!")
    print(f"   Scadenza: {expires_at}")
    print("   Il refresh automatico è gestito da EtsyAPI.")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_auth())
