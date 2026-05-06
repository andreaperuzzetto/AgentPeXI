"""Pinterest OAuth 2.0 + PKCE — Script di autenticazione one-time.

Eseguire manualmente quando Pinterest Standard Access è approvato:
    python -m apps.backend.tools.pinterest_auth_setup

Flusso:
1. Genera code_verifier + code_challenge (PKCE)
2. Avvia server locale su localhost:3001
3. Apre il browser sull'authorize URL di Pinterest
4. Riceve il callback con authorization code
5. Scambia code → access_token + refresh_token
6. Salva in SQLite (tabella oauth_tokens, provider='pinterest')
   (la cifratura Fernet è gestita internamente da MemoryManager)

NOTA: Questo script è uno stub — non va eseguito finché Pinterest Standard
Access non è approvato. Il delivery avviene via Tailwind (PINTEREST_DELIVERY_METHOD=tailwind).
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

# Aggiungi root progetto al path per esecuzione standalone
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from apps.backend.core.config import settings  # noqa: E402
from apps.backend.core.memory import MemoryManager  # noqa: E402

# ------------------------------------------------------------------
# PKCE helpers
# ------------------------------------------------------------------


def _generate_code_verifier() -> str:
    """Random 64 byte → base64url (senza padding), RFC 7636."""
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")


def _generate_code_challenge(verifier: str) -> str:
    """SHA256(verifier) → base64url (senza padding), metodo S256."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ------------------------------------------------------------------
# Pinterest OAuth URLs
# ------------------------------------------------------------------

PINTEREST_AUTHORIZE_URL = "https://www.pinterest.com/oauth/"
PINTEREST_TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"
CALLBACK_PORT = 3001
CALLBACK_URL = f"http://localhost:{CALLBACK_PORT}/callback"
SCOPES = "pins:read pins:write boards:read boards:write user_accounts:read"


# ------------------------------------------------------------------
# Autenticazione
# ------------------------------------------------------------------


async def run_auth() -> None:
    """Esegue il flusso OAuth PKCE Pinterest e salva i token in SQLite."""
    client_id = getattr(settings, "PINTEREST_CLIENT_ID", "")
    client_secret = getattr(settings, "PINTEREST_CLIENT_SECRET", "")

    if not client_id:
        print("❌ PINTEREST_CLIENT_ID non configurata nel .env")
        return
    if not client_secret:
        print("❌ PINTEREST_CLIENT_SECRET non configurata nel .env")
        return

    code_verifier = _generate_code_verifier()
    code_challenge = _generate_code_challenge(code_verifier)
    oauth_state = secrets.token_urlsafe(16)

    loop = asyncio.get_running_loop()
    code_future: asyncio.Future[str] = loop.create_future()

    # ------------------------------------------------------------------
    # Server locale aiohttp per ricevere il callback
    # ------------------------------------------------------------------

    async def handle_callback(request: web.Request) -> web.Response:
        error = request.query.get("error")
        if error:
            if not code_future.done():
                code_future.set_exception(RuntimeError(f"Pinterest auth error: {error}"))
            return web.Response(
                text="❌ Autenticazione fallita. Puoi chiudere questa finestra.",
                content_type="text/plain",
            )

        code = request.query.get("code")
        if not code:
            if not code_future.done():
                code_future.set_exception(RuntimeError("Nessun authorization code ricevuto"))
            return web.Response(
                text="❌ Nessun code ricevuto. Puoi chiudere questa finestra.",
                content_type="text/plain",
            )

        returned_state = request.query.get("state", "")
        if not secrets.compare_digest(returned_state, oauth_state):
            if not code_future.done():
                code_future.set_exception(
                    RuntimeError("OAuth state mismatch — possibile attacco CSRF")
                )
            return web.Response(
                text="❌ State non valido. Puoi chiudere questa finestra.",
                content_type="text/plain",
            )

        if not code_future.done():
            code_future.set_result(code)
        return web.Response(
            text="✅ Autenticazione completata! Puoi chiudere questa finestra.",
            content_type="text/plain",
        )

    aio_app = web.Application()
    aio_app.router.add_get("/callback", handle_callback)

    runner = web.AppRunner(aio_app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", CALLBACK_PORT)
    await site.start()
    print(f"🌐 Server locale avviato su http://localhost:{CALLBACK_PORT}")

    # Costruisci authorize URL e apri browser
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": CALLBACK_URL,
        "scope": SCOPES,
        "state": oauth_state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = (
        f"{PINTEREST_AUTHORIZE_URL}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    )
    print("🔗 Apertura browser per autenticazione Pinterest...")
    webbrowser.open(authorize_url)
    print("⏳ In attesa del callback da Pinterest...")

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

    print("✅ Authorization code ricevuto")
    await runner.cleanup()

    # ------------------------------------------------------------------
    # Scambio code → token
    # ------------------------------------------------------------------

    print("🔄 Scambio code → token...")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            PINTEREST_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": CALLBACK_URL,
                "code_verifier": code_verifier,
            },
            auth=(client_id, client_secret),
        )

    if resp.status_code != 200:
        print(f"❌ Errore scambio token: {resp.status_code} — {resp.text}")
        return

    token_data = resp.json()
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)

    from datetime import datetime, timedelta, timezone

    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

    # ------------------------------------------------------------------
    # Salva in SQLite (cifratura gestita da MemoryManager)
    # ------------------------------------------------------------------

    print("🔐 Salvataggio token...")
    memory = MemoryManager()
    await memory.init()

    await memory.save_oauth_tokens(
        provider="pinterest",
        access_token_enc=access_token,
        refresh_token_enc=refresh_token,
        expires_at=expires_at,
    )

    await memory.close()

    print("✅ Token Pinterest salvati in SQLite!")
    print(f"   Scadenza: {expires_at}")
    print("   Imposta PINTEREST_DELIVERY_METHOD=direct nel .env per attivare l'API diretta.")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_auth())
