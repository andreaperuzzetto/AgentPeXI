from __future__ import annotations

import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


def build_credentials() -> Credentials:
    """
    Costruisce credenziali OAuth2 dalle variabili d'ambiente.
    Non salva mai token su disco. Ogni avvio si autentica da env.
    """
    creds = Credentials(
        token=os.environ.get("GMAIL_ACCESS_TOKEN") or None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    # Rinnova automaticamente se scaduto
    if not creds.valid:
        creds.refresh(Request())
    return creds


def get_gmail_service():
    """Restituisce il client Gmail API autenticato."""
    creds = build_credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)
