"""NotionCalendar — wrapper Notion API per reminder del dominio Personal.

Se NOTION_API_TOKEN non configurato o Notion non raggiungibile,
tutti i metodi ritornano None silenziosamente senza propagare eccezioni.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger("agentpexi.notion_calendar")

# Proprietà della database Notion per i reminder
_DB_NAME = "Pepe Reminders"
_DB_PROPERTIES = {
    "Name": {"title": {}},
    "Date": {"date": {}},
    "Status": {
        "select": {
            "options": [
                {"name": "Pending", "color": "yellow"},
                {"name": "Done",    "color": "green"},
                {"name": "Cancelled", "color": "red"},
            ]
        }
    },
    "Recurring": {"rich_text": {}},
    "Notes":     {"rich_text": {}},
}


class NotionCalendar:
    """Gestisce un database Notion come calendario reminder per Pepe."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._client: Any = None
        self._db_id: str | None = None
        self._available = False

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    async def ensure_database(self) -> str | None:
        """Inizializza il client e trova/crea la database Notion.

        Idempotente: se la DB esiste già la riusa, altrimenti la crea.
        Fail-safe: se qualsiasi errore → self._available = False, ritorna None.
        """
        if not self._token:
            logger.info("Notion: NOTION_API_TOKEN non configurato, skip")
            return None

        try:
            from notion_client import AsyncClient
            self._client = AsyncClient(auth=self._token)

            # Controlla se l'ID è già in settings
            from apps.backend.core.config import settings
            if getattr(settings, "NOTION_REMINDERS_DB_ID", ""):
                self._db_id = settings.NOTION_REMINDERS_DB_ID
                self._available = True
                logger.info("Notion: database esistente rilevata (id=%s)", self._db_id[:8])
                return self._db_id

            # Cerca la DB per nome nelle pagine recenti
            db_id = await self._find_database()
            if db_id:
                self._db_id = db_id
                self._available = True
                logger.info("Notion: database trovata (id=%s)", db_id[:8])
                return db_id

            # Crea la DB ex-novo
            db_id = await self._create_database()
            if db_id:
                self._db_id = db_id
                self._available = True
                logger.info("Notion: database '%s' creata (id=%s)", _DB_NAME, db_id[:8])
                return db_id

        except Exception as exc:
            logger.warning("Notion: ensure_database fallito — %s", exc)

        self._available = False
        return None

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_reminder(
        self,
        text: str,
        trigger_at: datetime,
        recurring_rule: str | None = None,
    ) -> str | None:
        """Crea una pagina reminder nella DB. Restituisce il page_id o None."""
        if not self._available or not self._client or not self._db_id:
            return None
        try:
            props: dict = {
                "Name": {"title": [{"text": {"content": text[:2000]}}]},
                "Date": {"date": {"start": trigger_at.isoformat()}},
                "Status": {"select": {"name": "Pending"}},
            }
            if recurring_rule:
                props["Recurring"] = {
                    "rich_text": [{"text": {"content": recurring_rule}}]
                }

            response = await self._client.pages.create(
                parent={"database_id": self._db_id},
                properties=props,
            )
            return response["id"]
        except Exception as exc:
            logger.warning("Notion: create_reminder fallito — %s", exc)
            return None

    async def update_status(self, page_id: str, status: str) -> None:
        """Aggiorna lo Status di una pagina (Pending / Done / Cancelled)."""
        if not self._available or not self._client:
            return
        valid = {"Pending", "Done", "Cancelled"}
        if status not in valid:
            logger.warning("Notion: status '%s' non valido, skip", status)
            return
        try:
            await self._client.pages.update(
                page_id=page_id,
                properties={"Status": {"select": {"name": status}}},
            )
        except Exception as exc:
            logger.warning("Notion: update_status fallito — %s", exc)

    # ------------------------------------------------------------------
    # Helpers privati
    # ------------------------------------------------------------------

    async def _find_database(self) -> str | None:
        """Cerca la DB per titolo nelle risorse Notion accessibili."""
        try:
            response = await self._client.search(
                query=_DB_NAME,
                filter={"value": "database", "property": "object"},
            )
            for result in response.get("results", []):
                title_parts = result.get("title", [])
                title = "".join(t.get("plain_text", "") for t in title_parts)
                if title.strip() == _DB_NAME:
                    return result["id"]
        except Exception as exc:
            logger.warning("Notion: _find_database fallito — %s", exc)
        return None

    async def _create_database(self) -> str | None:
        """Crea la database nella pagina root dell'integrazione."""
        try:
            # Trova la prima pagina accessibile come parent
            response = await self._client.search(
                filter={"value": "page", "property": "object"},
            )
            results = response.get("results", [])
            if not results:
                logger.warning("Notion: nessuna pagina accessibile come parent")
                return None

            parent_id = results[0]["id"]
            db = await self._client.databases.create(
                parent={"type": "page_id", "page_id": parent_id},
                title=[{"type": "text", "text": {"content": _DB_NAME}}],
                properties=_DB_PROPERTIES,
            )
            return db["id"]
        except Exception as exc:
            logger.warning("Notion: _create_database fallito — %s", exc)
            return None
