import asyncio
import logging

import numpy as np

import apps.backend.api.state as state
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger("agentpexi.api")
router = APIRouter(dependencies=[Depends(state.verify_personal_key)])


@router.get("/api/memory/stats")
async def get_memory_stats() -> dict:
    """Statistiche ChromaDB: collection count, disponibilità."""
    if not state.memory:
        return {"chroma": {"available": False, "count": 0}}
    chroma = await state.memory.get_chroma_stats()
    return {"chroma": chroma}


@router.get("/api/memory/graph")
async def get_memory_graph(
    threshold: float = Query(default=0.72, ge=0.0, le=1.0),
) -> dict:
    """Grafo semantico della memoria: nodi dai documenti ChromaDB, archi da similarità coseno.

    Restituisce:
        {
          nodes: [{id, label, collection, zone, metadata}],
          edges: [{source, target, weight}],
        }

    Params:
        threshold: soglia coseno minima per creare un arco (default 0.72).

    Le quattro collection sono fetched in parallelo:
        pepe_memory     → zone "etsy"
        screen_memory   → zone "memory" (OCR watcher)
        personal_memory → zone "personal" (structured insights Personal)
        shared_memory   → zone "shared"  (bridge cross-domain)
    """
    if not state.memory:
        return JSONResponse(status_code=503, content={"error": "MemoryManager non disponibile"})

    async def _fetch_collection(collection) -> tuple[list[str], list[str], list[dict], list[list[float]]]:
        """Fetch (ids, documents, metadatas, embeddings) da una collection ChromaDB."""
        if collection is None:
            return [], [], [], []
        try:
            result = await asyncio.to_thread(
                collection.get,
                include=["documents", "metadatas", "embeddings"],
            )
            ids = result.get("ids") or []
            docs = result.get("documents") or []
            metas = result.get("metadatas") or []
            embeds = result.get("embeddings") or []
            return ids, docs, metas, embeds
        except Exception as exc:
            logger.warning("memory graph fetch fallito: %s", exc)
            return [], [], [], []

    # Fetch parallelo tutte e quattro le collection
    (
        (etsy_ids,     etsy_docs,     etsy_metas,     etsy_embeds),
        (screen_ids,   screen_docs,   screen_metas,   screen_embeds),
        (personal_ids, personal_docs, personal_metas, personal_embeds),
        (shared_ids,   shared_docs,   shared_metas,   shared_embeds),
    ) = await asyncio.gather(
        _fetch_collection(state.memory._chroma_collection),
        _fetch_collection(state.memory._screen_memory_collection),
        _fetch_collection(state.memory._personal_memory_collection),
        _fetch_collection(state.memory._shared_memory_collection),
    )

    # Helper: aggiungi una lista di nodi alla struttura unificata
    nodes: list[dict] = []
    all_ids: list[str] = []
    all_embeds: list[list[float]] = []

    def _add_nodes(
        ids: list[str],
        docs: list[str],
        metas: list[dict],
        embeds: list,
        collection_name: str,
        default_zone: str,
    ) -> None:
        for i, doc_id in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            label = meta.get("title") or meta.get("type") or meta.get("tag") or doc_id[:40]
            # screen_memory: zone determinata dall'app_name
            if collection_name == "screen_memory":
                app_name = meta.get("app", "")
                zone = "personal" if any(
                    k in app_name.lower() for k in ("code", "terminal", "vim", "vscode")
                ) else "memory"
            else:
                zone = default_zone
            nodes.append({
                "id":         doc_id,
                "label":      label,
                "collection": collection_name,
                "zone":       zone,
                "document":   (docs[i] if i < len(docs) else "")[:300],
                "metadata":   meta,
            })
            all_ids.append(doc_id)
            all_embeds.append(embeds[i] if i < len(embeds) and embeds[i] else None)

    _add_nodes(etsy_ids,     etsy_docs,     etsy_metas,     etsy_embeds,     "pepe_memory",     "etsy")
    _add_nodes(screen_ids,   screen_docs,   screen_metas,   screen_embeds,   "screen_memory",   "memory")
    _add_nodes(personal_ids, personal_docs, personal_metas, personal_embeds, "personal_memory", "personal")
    _add_nodes(shared_ids,   shared_docs,   shared_metas,   shared_embeds,   "shared_memory",   "shared")

    # Calcola archi tramite similarità coseno (solo nodi con embedding)
    edges: list[dict] = []
    valid_idx = [i for i, e in enumerate(all_embeds) if e is not None]
    # Cap at 500 to avoid O(n²) cost on large graphs
    if len(valid_idx) > 500:
        valid_idx = valid_idx[:500]

    if len(valid_idx) >= 2:
        try:
            matrix = np.array([all_embeds[i] for i in valid_idx], dtype=np.float32)
            # Normalizza righe per ottenere vettori unitari
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            matrix = matrix / norms
            # Similarità coseno = prodotto scalare di vettori normalizzati
            sim_matrix = matrix @ matrix.T

            n = len(valid_idx)
            for a in range(n):
                for b in range(a + 1, n):
                    sim = float(sim_matrix[a, b])
                    if sim >= threshold:
                        edges.append({
                            "source": all_ids[valid_idx[a]],
                            "target": all_ids[valid_idx[b]],
                            "weight": round(sim, 4),
                        })
        except Exception as exc:
            logger.warning("Calcolo similarità coseno fallito: %s", exc)

    # Arricchisci nodi con connection_count per dimensionare i nodi nel grafico
    conn_count: dict[str, int] = {}
    for edge in edges:
        conn_count[edge["source"]] = conn_count.get(edge["source"], 0) + 1
        conn_count[edge["target"]] = conn_count.get(edge["target"], 0) + 1
    for node in nodes:
        node["connections"] = conn_count.get(node["id"], 0)

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "threshold":       threshold,
            "total_nodes":     len(nodes),
            "total_edges":     len(edges),
            "etsy_count":      len(etsy_ids),
            "screen_count":    len(screen_ids),
            "personal_count":  len(personal_ids),
            "shared_count":    len(shared_ids),
        },
    }


@router.get("/api/memory/node/{doc_id:path}")
async def get_memory_node(
    doc_id: str,
    collection: str = Query(default="pepe_memory"),
) -> dict:
    """Dettaglio di un singolo nodo memoria: documento completo + metadati + storia accessi.

    Params:
        doc_id:     ID documento ChromaDB.
        collection: 'pepe_memory' | 'screen_memory' | 'personal_memory' | 'shared_memory'.

    Restituisce:
        {id, document, metadata, collection, access_history: [{agent, query_text, queried_at}]}
    """
    if not state.memory:
        return JSONResponse(status_code=503, content={"error": "MemoryManager non disponibile"})

    # Fetch documento dalla collection corretta
    _col_map = {
        "pepe_memory":     state.memory._chroma_collection,
        "screen_memory":   state.memory._screen_memory_collection,
        "personal_memory": state.memory._personal_memory_collection,
        "shared_memory":   state.memory._shared_memory_collection,
    }
    chroma_col = _col_map.get(collection)
    if chroma_col is None:
        return JSONResponse(status_code=503, content={"error": f"Collection '{collection}' non disponibile"})

    try:
        result = await asyncio.to_thread(
            chroma_col.get,
            ids=[doc_id],
            include=["documents", "metadatas"],
        )
        ids = result.get("ids") or []
        if not ids or ids[0] != doc_id:
            return JSONResponse(status_code=404, content={"error": f"Nodo '{doc_id}' non trovato"})

        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        document = docs[0] if docs else ""
        metadata = metas[0] if metas else {}
    except Exception as exc:
        logger.exception("Errore fetch nodo %s: %s", doc_id, exc)
        return JSONResponse(status_code=500, content={"error": "Errore interno fetch documento"})

    # Storico accessi da SQLite
    access_history = await state.memory.get_node_access_history(doc_id, collection, limit=20)

    return {
        "id": doc_id,
        "document": document,
        "metadata": metadata,
        "collection": collection,
        "access_history": access_history,
    }
