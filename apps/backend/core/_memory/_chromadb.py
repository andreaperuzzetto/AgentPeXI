"""ChromaDB mixin for MemoryManager."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("agentpexi.memory")


class ChromaDbMixin:
    # ------------------------------------------------------------------
    # Background task tracking — prevents GC of fire-and-forget tasks
    # ------------------------------------------------------------------

    def _fire_bg(self, coro: "asyncio.coroutines.Coroutine") -> asyncio.Task:
        """Schedule a fire-and-forget coroutine, keeping a strong reference."""
        if not hasattr(self, "_chroma_bg_tasks"):
            self._chroma_bg_tasks: set[asyncio.Task] = set()
        t = asyncio.create_task(coro)
        self._chroma_bg_tasks.add(t)
        t.add_done_callback(self._chroma_bg_tasks.discard)
        return t

    # ------------------------------------------------------------------
    # ChromaDB — insights semantici
    # ------------------------------------------------------------------

    async def store_insight(self, text: str, metadata: dict | None = None) -> str | None:
        if self._chroma_collection is None:
            return None
        import uuid

        doc_id = str(uuid.uuid4())
        await asyncio.to_thread(
            self._chroma_collection.add,
            documents=[text],
            metadatas=[metadata or {}],
            ids=[doc_id],
        )
        # Fire-and-forget: notifica il KnowledgeBridge per analisi cross-domain
        if self._bridge_callback and text:
            self._fire_bg(self._bridge_callback(text, "etsy"))
        return doc_id

    async def query_insights(self, query: str, n_results: int = 5) -> list[dict]:
        if self._chroma_collection is None:
            return []
        results = await asyncio.to_thread(
            self._chroma_collection.query,
            query_texts=[query],
            n_results=n_results,
        )
        out = []
        for i, doc in enumerate(results.get("documents", [[]])[0]):
            meta = (results.get("metadatas", [[]])[0][i]) if results.get("metadatas") else {}
            out.append({"document": doc, "metadata": meta})
        return out

    async def query_chromadb(
        self,
        query: str,
        n_results: int = 5,
        where: dict | None = None,
        agent: str = "unknown",
    ) -> list[dict]:
        """Query ChromaDB con filtro where opzionale sui metadata."""
        if self._chroma_collection is None:
            return []
        kwargs: dict = {"query_texts": [query], "n_results": n_results}
        if where:
            kwargs["where"] = where
        results = await asyncio.to_thread(lambda: self._chroma_collection.query(**kwargs))
        out = []
        accessed_ids: list[str] = []
        for i, doc in enumerate(results.get("documents", [[]])[0]):
            meta = (results.get("metadatas", [[]])[0][i]) if results.get("metadatas") else {}
            doc_id = (results.get("ids", [[]])[0][i]) if results.get("ids") else None
            if doc_id:
                accessed_ids.append(doc_id)
            out.append({"document": doc, "metadata": meta, "id": doc_id})
        # Log asincronamente — non blocca il caller
        if accessed_ids:
            self._fire_bg(
                self.log_memory_query(accessed_ids, "pepe_memory", agent=agent, query_text=query)
            )
        return out

    async def query_chromadb_recent(
        self,
        query: str,
        n_results: int = 5,
        where: dict | None = None,
        primary_days: int = 90,
        fallback_days: int = 180,
    ) -> list[dict]:
        """Come query_chromadb() ma con filtro temporale a scalini.

        1. Prova con finestra primary_days (default 90)
        2. Se vuoto, prova con finestra fallback_days (default 180)
        3. Se ancora vuoto, ritorna [] — non usare dati troppo vecchi

        I documenti ChromaDB devono avere metadata["date"] in formato YYYY-MM-DD.
        """

        def _build_where(base_where: dict | None, cutoff_date: str) -> dict:
            date_filter = {"date": {"$gte": cutoff_date}}
            if base_where:
                return {"$and": [base_where, date_filter]}
            return date_filter

        # Tentativo 1 — finestra primaria
        cutoff_primary = (
            datetime.now(timezone.utc) - timedelta(days=primary_days)
        ).strftime("%Y-%m-%d")

        try:
            results = await self.query_chromadb(
                query=query,
                n_results=n_results,
                where=_build_where(where, cutoff_primary),
            )
            if results:
                return results
        except Exception:
            logger.exception("ChromaDB recent query (primary window) failed")
        # Tentativo 2 — finestra allargata
        cutoff_fallback = (
            datetime.now(timezone.utc) - timedelta(days=fallback_days)
        ).strftime("%Y-%m-%d")

        try:
            results = await self.query_chromadb(
                query=query,
                n_results=n_results,
                where=_build_where(where, cutoff_fallback),
            )
            if results:
                logger.debug(
                    "query_chromadb_recent: dati primari vuoti, "
                    "usata finestra fallback %d giorni per query '%s'",
                    fallback_days, query[:50],
                )
                return results
        except Exception:
            logger.exception("ChromaDB recent query (fallback window) failed")
        return []

    # ------------------------------------------------------------------
    # Screen memory — ChromaDB collection separata (dominio Personal)
    # ------------------------------------------------------------------

    async def add_screen_memory(
        self,
        chunks: list[str],
        metadatas: list[dict],
        ids: list[str],
    ) -> bool:
        """Aggiunge chunks OCR alla collection screen_memory.

        Args:
            chunks:    Testi estratti (post-redaction, pre-chunked).
            metadatas: Un dict per chunk con timestamp, app_name, bundle_id, chunk_index.
            ids:       ID univoci per ogni chunk (es. f"{timestamp}_{bundle_id}_{i}").

        Returns True se l'operazione ha avuto successo, False se ChromaDB non disponibile.
        """
        if self._screen_memory_collection is None:
            return False
        try:
            await asyncio.to_thread(
                self._screen_memory_collection.add,
                documents=chunks,
                metadatas=metadatas,
                ids=ids,
            )
            return True
        except Exception as exc:
            logger.warning("add_screen_memory fallito: %s", exc)
            return False

    async def search_screen_memory(
        self,
        query: str,
        n_results: int = 10,
        where: dict | None = None,
        agent: str = "unknown",
    ) -> list[dict]:
        """Similarity search sulla collection screen_memory.

        Args:
            query:     Query in linguaggio naturale.
            n_results: Numero massimo di risultati.
            where:     Filtro ChromaDB sui metadata (es. filtro temporale).
            agent:     Nome agente chiamante (per memory_queries log).

        Returns lista di dict {document, metadata, id, distance}.
        """
        if self._screen_memory_collection is None:
            return []
        try:
            # ChromaDB richiede n_results <= count collection
            count = await asyncio.to_thread(self._screen_memory_collection.count)
            if count == 0:
                return []
            n = min(n_results, count)
            kwargs: dict = {"query_texts": [query], "n_results": n}
            if where:
                kwargs["where"] = where
            results = await asyncio.to_thread(lambda: self._screen_memory_collection.query(**kwargs))
            out = []
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0] if results.get("metadatas") else []
            ids_list = results.get("ids", [[]])[0] if results.get("ids") else []
            dists = results.get("distances", [[]])[0] if results.get("distances") else []
            accessed_ids: list[str] = []
            for i, doc in enumerate(docs):
                doc_id = ids_list[i] if i < len(ids_list) else None
                if doc_id:
                    accessed_ids.append(doc_id)
                out.append({
                    "document": doc,
                    "metadata": metas[i] if i < len(metas) else {},
                    "id": doc_id,
                    "distance": dists[i] if i < len(dists) else None,
                })
            # Log asincronamente
            if accessed_ids:
                self._fire_bg(
                    self.log_memory_query(accessed_ids, "screen_memory", agent=agent, query_text=query)
                )
            return out
        except Exception as exc:
            logger.warning("search_screen_memory fallito: %s", exc)
            return []

    async def delete_old_screen_memory(self, older_than_iso: str) -> int:
        """Elimina dalla screen_memory tutti i chunk con timestamp < older_than_iso.

        Args:
            older_than_iso: Timestamp ISO8601 (es. '2026-03-17T00:00:00').

        Returns il numero di chunk eliminati (0 se errore o ChromaDB non disponibile).
        """
        if self._screen_memory_collection is None:
            return 0
        try:
            results = self._screen_memory_collection.get(
                where={"timestamp": {"$lt": older_than_iso}},
                include=[],
            )
            ids_to_delete = results.get("ids", [])
            if not ids_to_delete:
                return 0
            self._screen_memory_collection.delete(ids=ids_to_delete)
            logger.info("screen_memory cleanup: eliminati %d chunk prima di %s", len(ids_to_delete), older_than_iso)
            return len(ids_to_delete)
        except Exception as exc:
            logger.warning("delete_old_screen_memory fallito: %s", exc)
            return 0

    async def get_screen_memory_stats(self) -> dict:
        """Statistiche collection screen_memory."""
        if self._screen_memory_collection is None:
            return {"available": False, "count": 0}
        try:
            count = self._screen_memory_collection.count()
            return {"available": True, "count": count}
        except Exception as exc:
            return {"available": False, "count": 0, "error": str(exc)}

    # ------------------------------------------------------------------
    # Personal memory — ChromaDB collection per il dominio Personal
    #
    # Separata sia da pepe_memory (Etsy knowledge base) che da
    # screen_memory (OCR raw del watcher). Contiene insight strutturati
    # prodotti da recall, research_personal, summarize (domain=personal).
    # ------------------------------------------------------------------

    async def store_personal_insight(
        self,
        text: str,
        metadata: dict | None = None,
    ) -> str | None:
        """Scrive un insight strutturato nella collection personal_memory.

        Speculare a store_insight() per pepe_memory.

        Args:
            text:     Testo dell'insight (sintesi, ricerca, riassunto personale).
            metadata: Dizionario metadata ChromaDB. Campi consigliati:
                        type, query, date (YYYY-MM-DD), created_at (ISO),
                        agent, confidence, tag.

        Returns l'ID univoco del documento, o None se ChromaDB non disponibile.
        """
        if self._personal_memory_collection is None:
            return None
        import uuid
        doc_id = str(uuid.uuid4())
        await asyncio.to_thread(
            self._personal_memory_collection.add,
            documents=[text],
            metadatas=[metadata or {}],
            ids=[doc_id],
        )
        # Fire-and-forget: notifica il KnowledgeBridge per analisi cross-domain
        if self._bridge_callback and text:
            self._fire_bg(self._bridge_callback(text, "personal"))
        return doc_id

    async def query_personal_memory(
        self,
        query: str,
        n_results: int = 5,
        where: dict | None = None,
        agent: str = "unknown",
    ) -> list[dict]:
        """Similarity search sulla collection personal_memory.

        Speculare a query_chromadb() per pepe_memory.
        Logga la query in memory_queries (col WS event per il NeuralBrain).

        Returns lista di dict {document, metadata, id}.
        """
        if self._personal_memory_collection is None:
            return []
        try:
            count = await asyncio.to_thread(self._personal_memory_collection.count)
            if count == 0:
                return []
            n = min(n_results, count)
            kwargs: dict = {"query_texts": [query], "n_results": n}
            if where:
                kwargs["where"] = where
            results = await asyncio.to_thread(
                lambda: self._personal_memory_collection.query(**kwargs)
            )
            out = []
            accessed_ids: list[str] = []
            for i, doc in enumerate(results.get("documents", [[]])[0]):
                meta = (results.get("metadatas", [[]])[0][i]) if results.get("metadatas") else {}
                doc_id = (results.get("ids", [[]])[0][i]) if results.get("ids") else None
                if doc_id:
                    accessed_ids.append(doc_id)
                out.append({"document": doc, "metadata": meta, "id": doc_id})
            if accessed_ids:
                self._fire_bg(
                    self.log_memory_query(
                        accessed_ids, "personal_memory", agent=agent, query_text=query
                    )
                )
            return out
        except Exception as exc:
            logger.warning("query_personal_memory fallito: %s", exc)
            return []

    async def query_personal_memory_recent(
        self,
        query: str,
        n_results: int = 5,
        where: dict | None = None,
        agent: str = "unknown",
        primary_days: int = 90,
        fallback_days: int = 180,
    ) -> list[dict]:
        """Come query_personal_memory() ma con filtro temporale a scalini.

        1. Prova con finestra primary_days (default 90)
        2. Se vuoto, prova con finestra fallback_days (default 180)
        3. Se ancora vuoto, ritorna [] — non usare dati troppo vecchi

        I documenti devono avere metadata["date"] in formato YYYY-MM-DD.
        """

        def _build_where(base_where: dict | None, cutoff_date: str) -> dict:
            date_filter = {"date": {"$gte": cutoff_date}}
            if base_where:
                return {"$and": [base_where, date_filter]}
            return date_filter

        cutoff_primary = (
            datetime.now(timezone.utc) - timedelta(days=primary_days)
        ).strftime("%Y-%m-%d")

        try:
            results = await self.query_personal_memory(
                query=query,
                n_results=n_results,
                where=_build_where(where, cutoff_primary),
                agent=agent,
            )
            if results:
                return results
        except Exception:
            logger.exception("Unexpected error")
        cutoff_fallback = (
            datetime.now(timezone.utc) - timedelta(days=fallback_days)
        ).strftime("%Y-%m-%d")

        try:
            results = await self.query_personal_memory(
                query=query,
                n_results=n_results,
                where=_build_where(where, cutoff_fallback),
                agent=agent,
            )
            if results:
                logger.debug(
                    "query_personal_memory_recent: dati primari vuoti, "
                    "usata finestra fallback %d giorni per query '%s'",
                    fallback_days, query[:50],
                )
                return results
        except Exception:
            logger.exception("Unexpected error")
        return []

    async def get_personal_memory_stats(self) -> dict:
        """Statistiche collection personal_memory."""
        if self._personal_memory_collection is None:
            return {"available": False, "count": 0}
        try:
            count = self._personal_memory_collection.count()
            return {"available": True, "count": count}
        except Exception as exc:
            return {"available": False, "count": 0, "error": str(exc)}

    # ------------------------------------------------------------------
    # Shared memory — bridge cross-domain (Etsy ↔ Personal)
    #
    # Contiene insight sintetizzati dal KnowledgeBridge (Fase 6) quando
    # rileva pattern semanticamente rilevanti in entrambi i domini.
    # È l'unica collection letta da agenti di entrambi i domini.
    # ------------------------------------------------------------------

    async def store_shared_insight(
        self,
        text: str,
        metadata: dict | None = None,
    ) -> str | None:
        """Scrive un insight cross-domain nella collection shared_memory.

        Chiamato esclusivamente da KnowledgeBridge dopo aver identificato
        un pattern rilevante sia in pepe_memory che in personal_memory.

        Args:
            text:     Testo sintetizzato del pattern cross-domain.
            metadata: Campi consigliati: source_etsy (list[str]),
                        source_personal (list[str]), similarity_score (float),
                        topic (str), date (YYYY-MM-DD), created_at (ISO).

        Returns l'ID univoco del documento, o None se ChromaDB non disponibile.
        """
        if self._shared_memory_collection is None:
            return None
        import uuid
        doc_id = str(uuid.uuid4())
        await asyncio.to_thread(
            self._shared_memory_collection.add,
            documents=[text],
            metadatas=[metadata or {}],
            ids=[doc_id],
        )
        return doc_id

    async def query_shared_memory(
        self,
        query: str,
        n_results: int = 3,
        where: dict | None = None,
        agent: str = "unknown",
    ) -> list[dict]:
        """Similarity search sulla collection shared_memory.

        Usata da agenti di entrambi i domini per arricchire il proprio
        contesto con insight cross-domain. n_results default basso (3)
        per non diluire il contesto domain-specific principale.

        Returns lista di dict {document, metadata, id}.
        """
        if self._shared_memory_collection is None:
            return []
        try:
            count = await asyncio.to_thread(self._shared_memory_collection.count)
            if count == 0:
                return []
            n = min(n_results, count)
            kwargs: dict = {"query_texts": [query], "n_results": n}
            if where:
                kwargs["where"] = where
            results = await asyncio.to_thread(
                lambda: self._shared_memory_collection.query(**kwargs)
            )
            out = []
            accessed_ids: list[str] = []
            for i, doc in enumerate(results.get("documents", [[]])[0]):
                meta = (results.get("metadatas", [[]])[0][i]) if results.get("metadatas") else {}
                doc_id = (results.get("ids", [[]])[0][i]) if results.get("ids") else None
                if doc_id:
                    accessed_ids.append(doc_id)
                out.append({"document": doc, "metadata": meta, "id": doc_id})
            if accessed_ids:
                self._fire_bg(
                    self.log_memory_query(
                        accessed_ids, "shared_memory", agent=agent, query_text=query
                    )
                )
            return out
        except Exception as exc:
            logger.warning("query_shared_memory fallito: %s", exc)
            return []

    async def get_shared_memory_stats(self) -> dict:
        """Statistiche collection shared_memory."""
        if self._shared_memory_collection is None:
            return {"available": False, "count": 0}
        try:
            count = self._shared_memory_collection.count()
            return {"available": True, "count": count}
        except Exception as exc:
            return {"available": False, "count": 0, "error": str(exc)}

    async def delete_stale_shared_memory(self, older_than_days: int = 90) -> int:
        """Elimina dalla shared_memory gli insight cross-domain più vecchi di N giorni.

        Usato dal job settimanale `shared_memory_decay` per evitare che insight
        obsoleti (generati da pattern Etsy/Personal non più attuali) inquinino
        il contesto cross-domain degli agenti.

        Il filtro è sul campo `date` (YYYY-MM-DD) scritto da KnowledgeBridge.
        Usa `$lt` su stringa ISO — funziona perché il formato è ordinabile lexicograficamente.

        Returns il numero di documenti eliminati (0 se collection vuota o errore).
        """
        if self._shared_memory_collection is None:
            return 0
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        cutoff = (_dt.now(_tz.utc) - _td(days=older_than_days)).strftime("%Y-%m-%d")
        try:
            results = await asyncio.to_thread(
                lambda: self._shared_memory_collection.get(
                    where={"date": {"$lt": cutoff}},
                    include=[],
                )
            )
            ids_to_delete = results.get("ids", [])
            if not ids_to_delete:
                return 0
            await asyncio.to_thread(
                self._shared_memory_collection.delete,
                ids=ids_to_delete,
            )
            logger.info(
                "shared_memory decay: eliminati %d insight anteriori al %s",
                len(ids_to_delete), cutoff,
            )
            return len(ids_to_delete)
        except Exception as exc:
            logger.warning("delete_stale_shared_memory fallito: %s", exc)
            return 0
