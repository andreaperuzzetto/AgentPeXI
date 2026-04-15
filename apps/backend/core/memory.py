"""MemoryManager — SQLite + ChromaDB per AgentPeXI.

Schema: 9 tabelle SQLite (conversations, agent_logs, agent_steps, llm_calls,
tool_calls, etsy_listings, scheduled_tasks, error_log, production_queue).
ChromaDB collection `pepe_memory` con Voyage AI voyage-3-lite embeddings.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

import aiosqlite

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.memory")

# ---------------------------------------------------------------------------
# Schema SQL
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'web',
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);

CREATE TABLE IF NOT EXISTS agent_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    task_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'running',
    input_data TEXT,
    output_data TEXT,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    total_llm_calls INTEGER NOT NULL DEFAULT 0,
    total_tool_calls INTEGER NOT NULL DEFAULT 0,
    total_steps INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES agent_logs(task_id),
    agent_name TEXT NOT NULL,
    step_number INTEGER NOT NULL,
    step_type TEXT NOT NULL,
    description TEXT,
    input_data TEXT,
    output_data TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES agent_logs(task_id),
    step_id INTEGER REFERENCES agent_steps(id),
    agent_name TEXT NOT NULL,
    model TEXT NOT NULL,
    system_prompt TEXT,
    messages TEXT,
    response TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES agent_logs(task_id),
    step_id INTEGER REFERENCES agent_steps(id),
    agent_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    action TEXT NOT NULL,
    input_params TEXT,
    output_result TEXT,
    status TEXT NOT NULL DEFAULT 'success',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL,
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS etsy_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT UNIQUE NOT NULL,
    production_queue_task_id TEXT,
    title TEXT,
    tags JSON,
    product_type TEXT,
    niche TEXT,
    template TEXT,
    color_scheme TEXT,
    size TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    ab_price_variant TEXT,
    price_eur REAL,
    views INTEGER DEFAULT 0,
    views_prev INTEGER DEFAULT 0,
    favorites INTEGER DEFAULT 0,
    sales INTEGER DEFAULT 0,
    revenue_eur REAL DEFAULT 0.0,
    file_path TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_synced_at TEXT,
    no_views_flagged_at TEXT,
    no_conversion_flagged_at TEXT,
    no_views_no_sales_flagged_at TEXT
);

CREATE TABLE IF NOT EXISTS listing_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT NOT NULL,
    analysis_type TEXT NOT NULL,
    cause TEXT NOT NULL,
    recommendations JSON NOT NULL,
    avoid_in_future TEXT NOT NULL,
    chromadb_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pending_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    payload JSON NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    cron_expression TEXT,
    agent_name TEXT,
    task_data TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run TEXT,
    next_run TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS error_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    error_type TEXT NOT NULL,
    message TEXT NOT NULL,
    task_id TEXT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS production_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL,
    product_type TEXT NOT NULL,
    niche TEXT NOT NULL,
    brief TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'planned',
    file_paths TEXT,
    etsy_listing_id TEXT,
    ab_price_variant TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_logs_task_id ON agent_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_agent_name ON agent_logs(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_steps_task_id ON agent_steps(task_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_task_id ON llm_calls(task_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_task_id ON tool_calls(task_id);
CREATE INDEX IF NOT EXISTS idx_error_log_agent_name ON error_log(agent_name);
CREATE INDEX IF NOT EXISTS idx_production_queue_status ON production_queue(status);
CREATE INDEX IF NOT EXISTS idx_el_status ON etsy_listings(status);
CREATE INDEX IF NOT EXISTS idx_el_listing_id ON etsy_listings(listing_id);
CREATE INDEX IF NOT EXISTS idx_la_listing_id ON listing_analyses(listing_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pa_type ON pending_actions(action_type);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL DEFAULT 'etsy',
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_tokens_provider ON oauth_tokens(provider);
"""


def _json_dumps(obj: Any) -> str | None:
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False, default=str)


def _json_loads(raw: str | None) -> Any:
    if raw is None:
        return None
    return json.loads(raw)


class MemoryManager:
    """Gestore unificato SQLite + ChromaDB."""

    def __init__(self) -> None:
        self._db_path = os.path.join(settings.STORAGE_PATH, "agentpexi.db")
        self._chromadb_path = os.path.join(settings.STORAGE_PATH, "chromadb")
        self._db: aiosqlite.Connection | None = None
        self._chroma_collection = None

    # ------------------------------------------------------------------
    # Init / shutdown
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Inizializza DB SQLite (schema) e ChromaDB collection."""
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

        # Migrazioni schema (colonne aggiunte dopo la creazione iniziale)
        try:
            await self._db.execute(
                "ALTER TABLE etsy_listings ADD COLUMN views_prev INTEGER DEFAULT 0"
            )
            await self._db.commit()
        except Exception:
            pass  # Colonna già esistente

        # ChromaDB + Voyage AI (lazy: fallisce silenziosamente se non disponibile)
        try:
            import chromadb
            from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

            import voyageai  # noqa: F401 — verifica disponibilità

            chroma_client = chromadb.PersistentClient(path=self._chromadb_path)

            # Voyage AI embedding function tramite wrapper compatibile
            voyage_ef = _VoyageEmbeddingFunction(
                api_key=settings.VOYAGE_API_KEY,
                model="voyage-3-lite",
            )
            self._chroma_collection = chroma_client.get_or_create_collection(
                name="pepe_memory",
                embedding_function=voyage_ef,
            )
        except Exception:
            # ChromaDB/Voyage non disponibile — continua solo con SQLite
            self._chroma_collection = None

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

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
        self, session_id: str, role: str, content: str, source: str = "web"
    ) -> None:
        """Salva messaggio in una sessione specifica."""
        await self._db.execute(
            "INSERT INTO conversations (session_id, role, content, source) VALUES (?, ?, ?, ?)",
            (session_id, role, content, source),
        )
        await self._db.commit()

    async def get_conversation_history(
        self, session_id: str, limit: int = 20
    ) -> list[dict]:
        """Ultimi N messaggi della sessione, ordinati ASC (dal più vecchio al più recente)."""
        cursor = await self._db.execute(
            "SELECT role, content, timestamp FROM conversations "
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

    # ------------------------------------------------------------------
    # Agent logs
    # ------------------------------------------------------------------

    async def log_agent_task(
        self,
        agent_name: str,
        task_id: str,
        status: str = "running",
        input_data: Any = None,
        output_data: Any = None,
        tokens: int = 0,
        cost: float = 0.0,
    ) -> None:
        await self._db.execute(
            """INSERT INTO agent_logs
               (agent_name, task_id, status, input_data, output_data, tokens_used, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                agent_name,
                task_id,
                status,
                _json_dumps(input_data),
                _json_dumps(output_data),
                tokens,
                cost,
            ),
        )
        await self._db.commit()

    async def finalize_agent_task(
        self,
        task_id: str,
        status: str = "completed",
        output_data: Any = None,
        tokens_used: int = 0,
        cost_usd: float = 0.0,
        total_llm_calls: int = 0,
        total_tool_calls: int = 0,
        total_steps: int = 0,
        total_cost_usd: float = 0.0,
    ) -> None:
        await self._db.execute(
            """UPDATE agent_logs SET
               status = ?, output_data = ?, tokens_used = ?, cost_usd = ?,
               total_llm_calls = ?, total_tool_calls = ?, total_steps = ?,
               total_cost_usd = ?, updated_at = datetime('now')
               WHERE task_id = ?""",
            (
                status,
                _json_dumps(output_data),
                tokens_used,
                cost_usd,
                total_llm_calls,
                total_tool_calls,
                total_steps,
                total_cost_usd,
                task_id,
            ),
        )
        await self._db.commit()

    async def get_task_by_id(self, task_id: str) -> dict | None:
        cursor = await self._db.execute(
            "SELECT * FROM agent_logs WHERE task_id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["input_data"] = _json_loads(d.get("input_data"))
        d["output_data"] = _json_loads(d.get("output_data"))
        return d

    async def get_last_failed_task(self, agent_name: str | None = None) -> dict | None:
        if agent_name:
            cursor = await self._db.execute(
                """SELECT * FROM agent_logs
                   WHERE status = 'failed' AND agent_name = ?
                   ORDER BY updated_at DESC LIMIT 1""",
                (agent_name,),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM agent_logs WHERE status = 'failed' ORDER BY updated_at DESC LIMIT 1"
            )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["input_data"] = _json_loads(d.get("input_data"))
        d["output_data"] = _json_loads(d.get("output_data"))
        return d

    # ------------------------------------------------------------------
    # Error log
    # ------------------------------------------------------------------

    async def log_error(
        self,
        agent_name: str,
        error_type: str,
        message: str,
        task_id: str | None = None,
    ) -> None:
        await self._db.execute(
            "INSERT INTO error_log (agent_name, error_type, message, task_id) VALUES (?, ?, ?, ?)",
            (agent_name, error_type, message, task_id),
        )
        await self._db.commit()

    async def get_agent_error_count(self, agent_name: str, hours: int = 1) -> int:
        since = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM error_log WHERE agent_name = ? AND timestamp >= ?",
            (agent_name, since),
        )
        row = await cursor.fetchone()
        return row[0]

    # ------------------------------------------------------------------
    # Observability — agent_steps, llm_calls, tool_calls
    # ------------------------------------------------------------------

    async def log_step(
        self,
        task_id: str,
        agent_name: str,
        step_number: int,
        step_type: str,
        description: str | None,
        input_data: Any = None,
        output_data: Any = None,
        duration_ms: int = 0,
    ) -> int:
        cursor = await self._db.execute(
            """INSERT INTO agent_steps
               (task_id, agent_name, step_number, step_type, description,
                input_data, output_data, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                agent_name,
                step_number,
                step_type,
                description,
                _json_dumps(input_data),
                _json_dumps(output_data),
                duration_ms,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def log_llm_call(
        self,
        task_id: str,
        step_id: int | None,
        agent_name: str,
        model: str,
        system_prompt: str | None,
        messages: Any,
        response: str | None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
    ) -> int:
        cursor = await self._db.execute(
            """INSERT INTO llm_calls
               (task_id, step_id, agent_name, model, system_prompt, messages,
                response, input_tokens, output_tokens, cache_read_tokens,
                cache_write_tokens, cost_usd, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                step_id,
                agent_name,
                model,
                system_prompt,
                _json_dumps(messages),
                response,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
                cost_usd,
                duration_ms,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def log_tool_call(
        self,
        task_id: str,
        step_id: int | None,
        agent_name: str,
        tool_name: str,
        action: str,
        input_params: Any = None,
        output_result: Any = None,
        status: str = "success",
        duration_ms: int = 0,
        cost_usd: float | None = None,
    ) -> int:
        cursor = await self._db.execute(
            """INSERT INTO tool_calls
               (task_id, step_id, agent_name, tool_name, action,
                input_params, output_result, status, duration_ms, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                step_id,
                agent_name,
                tool_name,
                action,
                _json_dumps(input_params),
                _json_dumps(output_result),
                status,
                duration_ms,
                cost_usd,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_task_timeline(self, task_id: str) -> list[dict]:
        """Restituisce tutti gli step + llm_calls + tool_calls per un task, ordinati per timestamp."""
        results: list[dict] = []

        cursor = await self._db.execute(
            "SELECT *, 'step' as _type FROM agent_steps WHERE task_id = ?",
            (task_id,),
        )
        for row in await cursor.fetchall():
            d = dict(row)
            d["input_data"] = _json_loads(d.get("input_data"))
            d["output_data"] = _json_loads(d.get("output_data"))
            results.append(d)

        cursor = await self._db.execute(
            "SELECT *, 'llm_call' as _type FROM llm_calls WHERE task_id = ?",
            (task_id,),
        )
        for row in await cursor.fetchall():
            d = dict(row)
            d["messages"] = _json_loads(d.get("messages"))
            results.append(d)

        cursor = await self._db.execute(
            "SELECT *, 'tool_call' as _type FROM tool_calls WHERE task_id = ?",
            (task_id,),
        )
        for row in await cursor.fetchall():
            d = dict(row)
            d["input_params"] = _json_loads(d.get("input_params"))
            d["output_result"] = _json_loads(d.get("output_result"))
            results.append(d)

        results.sort(key=lambda x: x.get("timestamp", ""))
        return results

    async def get_cost_breakdown(self, period_days: int = 30) -> dict:
        """Cost breakdown per agente, per tool, per giorno, e totale."""
        since = (datetime.utcnow() - timedelta(days=period_days)).strftime("%Y-%m-%d %H:%M:%S")

        # Per agente (da agent_logs)
        cursor = await self._db.execute(
            """SELECT agent_name, SUM(total_cost_usd) as cost
               FROM agent_logs WHERE updated_at >= ? AND status = 'completed'
               GROUP BY agent_name""",
            (since,),
        )
        per_agent = {row["agent_name"]: row["cost"] or 0.0 for row in await cursor.fetchall()}

        # Per tool (da tool_calls)
        cursor = await self._db.execute(
            """SELECT tool_name, SUM(cost_usd) as cost
               FROM tool_calls WHERE timestamp >= ? AND cost_usd IS NOT NULL
               GROUP BY tool_name""",
            (since,),
        )
        per_tool = {row["tool_name"]: row["cost"] or 0.0 for row in await cursor.fetchall()}

        # Per giorno (da llm_calls — costo LLM è la componente principale)
        cursor = await self._db.execute(
            """SELECT DATE(timestamp) as day, SUM(cost_usd) as cost
               FROM llm_calls WHERE timestamp >= ?
               GROUP BY DATE(timestamp) ORDER BY day""",
            (since,),
        )
        per_day = {row["day"]: row["cost"] or 0.0 for row in await cursor.fetchall()}

        # Totale
        total = sum(per_agent.values())

        return {
            "per_agent": per_agent,
            "per_tool": per_tool,
            "per_day": per_day,
            "total": total,
        }

    async def get_agent_logs_summary(self, period_days: int = 14) -> dict:
        """Aggregati task da agent_logs per il frontend Analytics.

        Ritorna:
          total, completed, failed, running, by_status,
          per_day (YYYY-MM-DD → {status: count}),
          per_agent (agent_name → {total, completed, failed, cost}),
          production_queue stats.
        """
        since = (datetime.utcnow() - timedelta(days=period_days)).strftime("%Y-%m-%d %H:%M:%S")

        # Conteggi per status
        cursor = await self._db.execute(
            "SELECT status, COUNT(*) as cnt FROM agent_logs "
            "WHERE created_at >= ? GROUP BY status",
            (since,),
        )
        by_status: dict[str, int] = {r["status"]: r["cnt"] for r in await cursor.fetchall()}

        # Per giorno × status (per grafico)
        cursor = await self._db.execute(
            """SELECT DATE(created_at) as day, status, COUNT(*) as cnt
               FROM agent_logs WHERE created_at >= ?
               GROUP BY day, status ORDER BY day""",
            (since,),
        )
        per_day: dict[str, dict[str, int]] = {}
        for r in await cursor.fetchall():
            day = r["day"]
            if day not in per_day:
                per_day[day] = {}
            per_day[day][r["status"]] = r["cnt"]

        # Per agente (totale task + costo)
        cursor = await self._db.execute(
            """SELECT agent_name,
                      COUNT(*) as total,
                      SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                      SUM(CASE WHEN status = 'failed'    THEN 1 ELSE 0 END) as failed,
                      SUM(total_cost_usd) as cost
               FROM agent_logs WHERE created_at >= ?
               GROUP BY agent_name""",
            (since,),
        )
        per_agent: dict[str, dict] = {}
        for r in await cursor.fetchall():
            per_agent[r["agent_name"]] = {
                "total":     r["total"],
                "completed": r["completed"],
                "failed":    r["failed"],
                "cost":      r["cost"] or 0.0,
            }

        total     = sum(by_status.values())
        completed = by_status.get("completed", 0)
        failed    = by_status.get("failed", 0)
        running   = by_status.get("running", 0)

        pq_stats  = await self.get_production_queue_stats()

        return {
            "days":             period_days,
            "total":            total,
            "completed":        completed,
            "failed":           failed,
            "running":          running,
            "by_status":        by_status,
            "per_day":          per_day,
            "per_agent":        per_agent,
            "production_queue": pq_stats,
        }

    async def get_chroma_stats(self) -> dict:
        """Conta le entry nella collection ChromaDB."""
        if self._chroma_collection is None:
            return {"available": False, "count": 0}
        try:
            count = self._chroma_collection.count()
            return {"available": True, "count": count}
        except Exception as exc:
            return {"available": False, "count": 0, "error": str(exc)}

    # ------------------------------------------------------------------
    # Production queue (deduplicazione pipeline)
    # ------------------------------------------------------------------

    async def add_to_production_queue(
        self,
        task_id: str,
        product_type: str,
        niche: str,
        brief: dict,
    ) -> int:
        """Inserisce un nuovo item nella coda. Ritorna l'id row."""
        cursor = await self._db.execute(
            """INSERT INTO production_queue (task_id, product_type, niche, brief)
               VALUES (?, ?, ?, ?)""",
            (task_id, product_type, niche, _json_dumps(brief)),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_production_queue_item(self, task_id: str) -> dict | None:
        """Ritorna item per task_id, None se non esiste."""
        cursor = await self._db.execute(
            "SELECT * FROM production_queue WHERE task_id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["brief"] = _json_loads(d.get("brief"))
        d["file_paths"] = _json_loads(d.get("file_paths"))
        return d

    async def update_production_queue_status(
        self,
        task_id: str,
        status: str,
        file_paths: list[str] | None = None,
    ) -> None:
        """Aggiorna status e opzionalmente file_paths. Setta updated_at = now."""
        if file_paths is not None:
            await self._db.execute(
                """UPDATE production_queue SET status = ?, file_paths = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE task_id = ?""",
                (status, _json_dumps(file_paths), task_id),
            )
        else:
            await self._db.execute(
                """UPDATE production_queue SET status = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE task_id = ?""",
                (status, task_id),
            )
        await self._db.commit()

    async def get_production_queue(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Lista items, filtrabili per status. Ordinati per created_at DESC."""
        if status:
            cursor = await self._db.execute(
                "SELECT * FROM production_queue WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM production_queue ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["brief"] = _json_loads(d.get("brief"))
            d["file_paths"] = _json_loads(d.get("file_paths"))
            result.append(d)
        return result

    async def is_duplicate_product(self, niche: str, product_type: str) -> bool:
        """True se esiste già un item completed o in_progress con stessa niche+product_type."""
        cursor = await self._db.execute(
            """SELECT 1 FROM production_queue
               WHERE niche = ? AND product_type = ?
               AND status IN ('completed', 'in_progress') LIMIT 1""",
            (niche, product_type),
        )
        if await cursor.fetchone():
            return True
        cursor = await self._db.execute(
            """SELECT 1 FROM etsy_listings
               WHERE niche = ? AND product_type = ? LIMIT 1""",
            (niche, product_type),
        )
        return (await cursor.fetchone()) is not None

    async def get_production_queue_stats(self) -> dict:
        """Statistiche aggregate production_queue."""
        from datetime import date as _date

        today = _date.today().isoformat()
        stats: dict[str, int] = {}
        for status in ("planned", "in_progress", "completed", "skipped"):
            cursor = await self._db.execute(
                "SELECT COUNT(*) as cnt FROM production_queue WHERE status = ?",
                (status,),
            )
            row = await cursor.fetchone()
            stats[status] = row["cnt"] if row else 0
        cursor = await self._db.execute(
            "SELECT COUNT(*) as cnt FROM production_queue "
            "WHERE status = 'completed' AND date(created_at) = ?",
            (today,),
        )
        row = await cursor.fetchone()
        stats["completed_today"] = row["cnt"] if row else 0
        return stats

    async def get_analytics_summary(self, days: int = 7) -> dict:
        """Statistiche aggregate etsy_listings per periodo."""
        cursor = await self._db.execute(
            """SELECT
               COALESCE(SUM(views), 0) as total_views,
               COALESCE(SUM(sales), 0) as total_sales,
               COALESCE(SUM(revenue_eur), 0) as revenue
               FROM etsy_listings
               WHERE last_synced_at >= datetime('now', ?)""",
            (f"-{days} days",),
        )
        row = await cursor.fetchone()
        return dict(row) if row else {}

    async def get_listings_by_niche(self, niche: str, limit: int = 10) -> list[dict]:
        """Ritorna listing per una nicchia specifica."""
        cursor = await self._db.execute(
            "SELECT * FROM etsy_listings WHERE niche = ? ORDER BY created_at DESC LIMIT ?",
            (niche, limit),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["tags"] = _json_loads(d.get("tags"))
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Etsy listings (expanded)
    # ------------------------------------------------------------------

    async def add_etsy_listing(
        self,
        listing_id: str,
        production_queue_task_id: str | None,
        title: str,
        tags: list[str],
        product_type: str,
        niche: str,
        template: str,
        color_scheme: str,
        size: str,
        ab_price_variant: str,
        price_eur: float,
        file_path: str,
    ) -> None:
        await self._db.execute(
            """INSERT INTO etsy_listings
               (listing_id, production_queue_task_id, title, tags,
                product_type, niche, template, color_scheme, size,
                ab_price_variant, price_eur, file_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                listing_id,
                production_queue_task_id,
                title,
                _json_dumps(tags),
                product_type,
                niche,
                template,
                color_scheme,
                size,
                ab_price_variant,
                price_eur,
                file_path,
            ),
        )
        await self._db.commit()

    async def update_etsy_listing_stats(
        self,
        listing_id: str,
        views: int,
        favorites: int,
        sales: int,
        revenue_eur: float,
        status: str,
        last_synced_at: str,
    ) -> None:
        # Salva views correnti come views_prev prima dell'aggiornamento
        await self._db.execute(
            "UPDATE etsy_listings SET views_prev = views WHERE listing_id = ?",
            (listing_id,),
        )
        await self._db.execute(
            """UPDATE etsy_listings SET
               views = ?, favorites = ?, sales = ?,
               revenue_eur = ?, status = ?, last_synced_at = ?
               WHERE listing_id = ?""",
            (views, favorites, sales, revenue_eur, status, last_synced_at, listing_id),
        )
        await self._db.commit()

    async def get_etsy_listings(self, status: str | None = None) -> list[dict]:
        if status:
            cursor = await self._db.execute(
                "SELECT * FROM etsy_listings WHERE status = ? ORDER BY created_at DESC",
                (status,),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM etsy_listings ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["tags"] = _json_loads(d.get("tags"))
            result.append(d)
        return result

    async def get_etsy_listings_count(self) -> int:
        """Conta totale listing in etsy_listings (qualsiasi status)."""
        cursor = await self._db.execute("SELECT COUNT(*) FROM etsy_listings")
        row = await cursor.fetchone()
        return row[0]

    async def get_listings_no_views(self, days: int = 7) -> list[dict]:
        """views == 0, active, created_at < now - days, no_views_flagged_at IS NULL."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """SELECT * FROM etsy_listings
               WHERE views = 0 AND status = 'active'
               AND created_at < ? AND no_views_flagged_at IS NULL""",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_listings_no_conversion(self, days: int = 45) -> list[dict]:
        """views > 0, sales == 0, active, created_at < now - days, no_conversion_flagged_at IS NULL."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """SELECT * FROM etsy_listings
               WHERE views > 0 AND sales = 0 AND status = 'active'
               AND created_at < ? AND no_conversion_flagged_at IS NULL""",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_listings_no_views_no_sales(self, days: int = 45) -> list[dict]:
        """views == 0, sales == 0, active, created_at < now - days, no_views_no_sales_flagged_at IS NULL."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """SELECT * FROM etsy_listings
               WHERE views = 0 AND sales = 0 AND status = 'active'
               AND created_at < ? AND no_views_no_sales_flagged_at IS NULL""",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def flag_no_views(self, listing_id: str) -> None:
        await self._db.execute(
            "UPDATE etsy_listings SET no_views_flagged_at = CURRENT_TIMESTAMP WHERE listing_id = ?",
            (listing_id,),
        )
        await self._db.commit()

    async def flag_no_conversion(self, listing_id: str) -> None:
        await self._db.execute(
            "UPDATE etsy_listings SET no_conversion_flagged_at = CURRENT_TIMESTAMP WHERE listing_id = ?",
            (listing_id,),
        )
        await self._db.commit()

    async def flag_no_views_no_sales(self, listing_id: str) -> None:
        await self._db.execute(
            "UPDATE etsy_listings SET no_views_no_sales_flagged_at = CURRENT_TIMESTAMP WHERE listing_id = ?",
            (listing_id,),
        )
        await self._db.commit()

    async def get_listing_prev_views(self, listing_id: str) -> int | None:
        """Ritorna views_prev prima dell'ultimo update_etsy_listing_stats()."""
        cursor = await self._db.execute(
            "SELECT views_prev FROM etsy_listings WHERE listing_id = ?",
            (listing_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return row[0]

    # ------------------------------------------------------------------
    # Listing analyses
    # ------------------------------------------------------------------

    async def save_listing_analysis(
        self,
        listing_id: str,
        analysis_type: str,
        cause: str,
        recommendations: list[str],
        avoid_in_future: str,
        chromadb_id: str | None = None,
    ) -> None:
        await self._db.execute(
            """INSERT INTO listing_analyses
               (listing_id, analysis_type, cause, recommendations,
                avoid_in_future, chromadb_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                listing_id,
                analysis_type,
                cause,
                _json_dumps(recommendations),
                avoid_in_future,
                chromadb_id,
            ),
        )
        await self._db.commit()

    async def get_listing_analyses(self, listing_id: str) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM listing_analyses WHERE listing_id = ? ORDER BY created_at DESC",
            (listing_id,),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["recommendations"] = _json_loads(d.get("recommendations"))
            result.append(d)
        return result

    async def get_all_listing_analyses(self, limit: int = 20) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM listing_analyses ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["recommendations"] = _json_loads(d.get("recommendations"))
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Pending actions
    # ------------------------------------------------------------------

    async def save_pending_action(
        self,
        action_type: str,
        payload: dict,
        expires_hours: int = 24,
    ) -> None:
        """INSERT OR REPLACE — sovrascrive pending_action precedente dello stesso tipo."""
        expires_at = (datetime.utcnow() + timedelta(hours=expires_hours)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        await self._db.execute(
            """INSERT OR REPLACE INTO pending_actions
               (action_type, payload, expires_at)
               VALUES (?, ?, ?)""",
            (action_type, _json_dumps(payload), expires_at),
        )
        await self._db.commit()

    async def get_pending_action(self, action_type: str) -> dict | None:
        """Ritorna None se assente o scaduto."""
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """SELECT * FROM pending_actions
               WHERE action_type = ? AND expires_at > ?""",
            (action_type, now),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["payload"] = _json_loads(d.get("payload"))
        return d

    async def delete_pending_action(self, action_type: str) -> None:
        await self._db.execute(
            "DELETE FROM pending_actions WHERE action_type = ?",
            (action_type,),
        )
        await self._db.commit()

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
        """Salva token cifrati. Usa UPSERT per evitare duplicati."""
        await self._db.execute(
            """INSERT INTO oauth_tokens
               (provider, access_token_encrypted, refresh_token_encrypted, expires_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(provider) DO UPDATE SET
               access_token_encrypted = excluded.access_token_encrypted,
               refresh_token_encrypted = excluded.refresh_token_encrypted,
               expires_at = excluded.expires_at,
               updated_at = CURRENT_TIMESTAMP""",
            (provider, access_token_enc, refresh_token_enc, expires_at),
        )
        await self._db.commit()

    async def get_oauth_tokens(self, provider: str) -> dict | None:
        """Ritorna token cifrati per provider, o None se non esistono."""
        cursor = await self._db.execute(
            "SELECT * FROM oauth_tokens WHERE provider = ?", (provider,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_oauth_tokens(
        self,
        provider: str,
        access_token_enc: str,
        refresh_token_enc: str,
        expires_at: str,
    ) -> None:
        """Aggiorna token cifrati esistenti."""
        await self._db.execute(
            """UPDATE oauth_tokens SET
               access_token_encrypted = ?, refresh_token_encrypted = ?,
               expires_at = ?, updated_at = CURRENT_TIMESTAMP
               WHERE provider = ?""",
            (access_token_enc, refresh_token_enc, expires_at, provider),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # ChromaDB — insights semantici
    # ------------------------------------------------------------------

    async def store_insight(self, text: str, metadata: dict | None = None) -> str | None:
        if self._chroma_collection is None:
            return None
        import uuid

        doc_id = str(uuid.uuid4())
        self._chroma_collection.add(
            documents=[text],
            metadatas=[metadata or {}],
            ids=[doc_id],
        )
        return doc_id

    async def query_insights(self, query: str, n_results: int = 5) -> list[dict]:
        if self._chroma_collection is None:
            return []
        results = self._chroma_collection.query(
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
    ) -> list[dict]:
        """Query ChromaDB con filtro where opzionale sui metadata."""
        if self._chroma_collection is None:
            return []
        kwargs: dict = {"query_texts": [query], "n_results": n_results}
        if where:
            kwargs["where"] = where
        results = self._chroma_collection.query(**kwargs)
        out = []
        for i, doc in enumerate(results.get("documents", [[]])[0]):
            meta = (results.get("metadatas", [[]])[0][i]) if results.get("metadatas") else {}
            doc_id = (results.get("ids", [[]])[0][i]) if results.get("ids") else None
            out.append({"document": doc, "metadata": meta, "id": doc_id})
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
            datetime.utcnow() - timedelta(days=primary_days)
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
            pass

        # Tentativo 2 — finestra allargata
        cutoff_fallback = (
            datetime.utcnow() - timedelta(days=fallback_days)
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
            pass

        # Nessun dato recente disponibile
        return []


# ---------------------------------------------------------------------------
# Voyage AI embedding function per ChromaDB
# ---------------------------------------------------------------------------

class _VoyageEmbeddingFunction:
    """Wrapper Voyage AI compatibile con l'interfaccia EmbeddingFunction di ChromaDB."""

    def __init__(self, api_key: str, model: str = "voyage-3-lite") -> None:
        self._api_key = api_key
        self._model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import voyageai
            self._client = voyageai.Client(api_key=self._api_key)
        return self._client

    def __call__(self, input: list[str]) -> list[list[float]]:
        client = self._get_client()
        result = client.embed(input, model=self._model)
        return result.embeddings
