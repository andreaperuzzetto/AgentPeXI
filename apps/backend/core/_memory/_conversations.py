"""Conversations mixin for MemoryManager."""
from __future__ import annotations

import logging

logger = logging.getLogger("agentpexi.memory")


class ConversationsMixin:
    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def save_conversation(self, role: str, content: str) -> None:
        """Legacy — salva senza session_id (usa 'default')."""
        await self.save_message("default", role, content, "web")

    async def get_recent_conversations(self, limit: int = 20) -> list[dict]:
        """Legacy — ultime N conversazioni globali."""
        cursor = await self._db.execute(
            "SELECT role, content, timestamp FROM conversations ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in reversed(rows)]

    async def save_message(
        self, session_id: str, role: str, content: str, source: str = "web",
        domain: str = "etsy",
    ) -> None:
        """Salva messaggio in una sessione specifica.

        Args:
            domain: Dominio attivo al momento del salvataggio ('etsy' o 'personal').
                    Usato per separare cronologia Etsy da Personal nella stessa sessione.
        """
        await self._db.execute(
            "INSERT INTO conversations (session_id, role, content, source, domain) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, source, domain),
        )
        await self._db.commit()

    async def get_conversation_history(
        self, session_id: str, limit: int = 20, domain: str | None = None
    ) -> list[dict]:
        """Ultimi N messaggi della sessione, ordinati ASC (dal più vecchio al più recente).

        Args:
            domain: Se specificato, filtra per dominio ('etsy' o 'personal').
                    Se None, restituisce tutti i messaggi della sessione indipendentemente
                    dal dominio (comportamento legacy).
        """
        if domain is not None:
            cursor = await self._db.execute(
                "SELECT id, role, content, timestamp, domain FROM conversations "
                "WHERE session_id = ? AND domain = ? ORDER BY id DESC LIMIT ?",
                (session_id, domain, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT id, role, content, timestamp, domain FROM conversations "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in reversed(rows)]

    async def clear_session(self, session_id: str) -> None:
        """Cancella tutti i messaggi di una sessione."""
        await self._db.execute(
            "DELETE FROM conversations WHERE session_id = ?",
            (session_id,),
        )
        await self._db.commit()

    async def get_sessions(self, limit: int = 20) -> list[dict]:
        """Lista sessioni con ultimo messaggio e timestamp, ordinate per recenza."""
        cursor = await self._db.execute(
            "SELECT session_id, content AS last_message, timestamp "
            "FROM conversations c1 WHERE id = ("
            "  SELECT MAX(id) FROM conversations c2 WHERE c2.session_id = c1.session_id"
            ") ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
