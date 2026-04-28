"""MemoryManager — SQLite + ChromaDB per AgentPeXI.

Schema: 15 tabelle SQLite (conversations, agent_logs, agent_steps, llm_calls,
tool_calls, etsy_listings, scheduled_tasks, error_log, production_queue,
config, autopilot_state, market_signals, listing_performance,
niche_intelligence, revenue_events).
ChromaDB collection `pepe_memory` con Voyage AI voyage-3-lite embeddings.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite
from cryptography.fernet import Fernet

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
    domain TEXT NOT NULL DEFAULT 'etsy',
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
    domain TEXT NOT NULL DEFAULT 'etsy',
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
    provider TEXT NOT NULL DEFAULT 'anthropic',
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

CREATE TABLE IF NOT EXISTS reminders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    text            TEXT    NOT NULL,
    trigger_at      TEXT    NOT NULL,
    recurring_rule  TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending',
    notion_page_id  TEXT,
    telegram_msg_id INTEGER,
    acknowledged_at TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reminders_trigger ON reminders (trigger_at, status);
CREATE INDEX IF NOT EXISTS idx_reminders_msg     ON reminders (telegram_msg_id);

CREATE TABLE IF NOT EXISTS personal_learning (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent         TEXT    NOT NULL,
    pattern_type  TEXT    NOT NULL,
    pattern_value TEXT    NOT NULL,
    signal_type   TEXT    NOT NULL,
    weight        REAL    NOT NULL DEFAULT 0.5,
    occurrences   INTEGER NOT NULL DEFAULT 1,
    last_seen     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (agent, pattern_type, pattern_value)
);
CREATE INDEX IF NOT EXISTS idx_pl_agent ON personal_learning (agent, pattern_type);
CREATE INDEX IF NOT EXISTS idx_pl_seen  ON personal_learning (last_seen);

CREATE TABLE IF NOT EXISTS learning_evaluations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id      TEXT NOT NULL,
    signal_type     TEXT NOT NULL,
    metric_type     TEXT NOT NULL,
    baseline_value  REAL NOT NULL,
    post_value      REAL NOT NULL,
    delta           REAL NOT NULL,
    accepted        INTEGER NOT NULL,
    evaluated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_le_pattern ON learning_evaluations(pattern_id);
CREATE INDEX IF NOT EXISTS idx_le_signal  ON learning_evaluations(signal_type);

CREATE TABLE IF NOT EXISTS memory_queries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent         TEXT    NOT NULL DEFAULT 'unknown',
    collection    TEXT    NOT NULL,
    doc_ids       TEXT    NOT NULL,
    query_text    TEXT,
    queried_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mq_collection ON memory_queries(collection);
CREATE INDEX IF NOT EXISTS idx_mq_agent      ON memory_queries(agent);
CREATE INDEX IF NOT EXISTS idx_mq_queried_at ON memory_queries(queried_at);

-- ---------------------------------------------------------------------------
-- Blocco 1-4: nuove tabelle refactoring
-- ---------------------------------------------------------------------------

-- Configurazione chiave-valore (budget, policy, system flags)
CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL DEFAULT (unixepoch())
);

-- Stato persistente AutopilotLoop (sopravvive ai restart)
CREATE TABLE IF NOT EXISTS autopilot_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL DEFAULT (unixepoch())
);

-- Cache dati di mercato raccolti da MarketDataAgent (Tier 1-2)
CREATE TABLE IF NOT EXISTS market_signals (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    niche                TEXT    NOT NULL,
    product_type         TEXT,
    -- Tier 1: Etsy scraping
    etsy_result_count    INTEGER,
    avg_reviews          REAL,
    avg_price_eur        REAL,
    autocomplete_hits    INTEGER,
    -- Tier 2: Google Trends / eRank
    google_trend_score   REAL,
    erank_search_volume  INTEGER,
    -- Scoring calcolato
    entry_score          REAL    DEFAULT 0.0,
    seasonal_boost       REAL    DEFAULT 1.0,
    -- Meta
    tier                 INTEGER DEFAULT 1,
    collected_at         REAL    NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_ms_niche       ON market_signals(niche, collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_ms_score       ON market_signals(entry_score DESC);

-- Snapshot periodici performance listing pubblicati (AnalyticsAgent)
CREATE TABLE IF NOT EXISTS listing_performance (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    etsy_listing_id     TEXT    NOT NULL,
    production_queue_id INTEGER REFERENCES production_queue(id),
    niche               TEXT    NOT NULL,
    product_type        TEXT    NOT NULL,
    template            TEXT,                        -- dal DesignAgent result [B4]
    color_scheme        TEXT,                        -- dal DesignAgent result [B4]
    views               INTEGER DEFAULT 0,
    clicks              INTEGER DEFAULT 0,           -- per calcolo CTR [B4]
    favorites           INTEGER DEFAULT 0,
    orders              INTEGER DEFAULT 0,
    revenue_eur         REAL    DEFAULT 0.0,
    ctr                 REAL    DEFAULT 0.0,         -- clicks / views [B4]
    conversion_rate     REAL    DEFAULT 0.0,         -- orders / clicks
    favorite_rate       REAL    DEFAULT 0.0,
    ladder_level        TEXT,                        -- NULL | views_low | ctr_low | conv_low | ok [B4]
    last_diagnostic_at  REAL,                        -- ts ultimo Ladder check [B4]
    days_live           INTEGER DEFAULT 0,
    snapshot_at         REAL    NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_lp_listing ON listing_performance(etsy_listing_id);
CREATE INDEX IF NOT EXISTS idx_lp_niche   ON listing_performance(niche, snapshot_at DESC);

-- Intelligenza aggregata per niche+product_type (LearningLoop → scoring)
CREATE TABLE IF NOT EXISTS niche_intelligence (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    niche                TEXT    NOT NULL,
    product_type         TEXT    NOT NULL,
    total_listings       INTEGER DEFAULT 0,
    total_orders         INTEGER DEFAULT 0,
    total_revenue_eur    REAL    DEFAULT 0.0,
    avg_ctr              REAL    DEFAULT 0.0,        -- media CTR listing niche [B4]
    avg_conversion_rate  REAL    DEFAULT 0.0,
    avg_days_to_sale     REAL,                       -- media giorni dalla publish alla prima vendita [B4]
    avg_favorite_rate    REAL    DEFAULT 0.0,
    performance_score    REAL    DEFAULT 0.5,
    confidence_level     TEXT    DEFAULT 'low',
    last_sale_at         REAL,
    last_updated_at      REAL    NOT NULL DEFAULT (unixepoch()),
    UNIQUE(niche, product_type)
);
CREATE INDEX IF NOT EXISTS idx_ni_score ON niche_intelligence(performance_score DESC);
CREATE INDEX IF NOT EXISTS idx_ni_niche ON niche_intelligence(niche);

-- Singoli eventi di vendita (FinanceTracker)
CREATE TABLE IF NOT EXISTS revenue_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    etsy_listing_id TEXT    NOT NULL,
    order_id        TEXT    UNIQUE,
    niche           TEXT,
    product_type    TEXT,
    gross_eur       REAL    NOT NULL,
    etsy_fee_eur    REAL    NOT NULL,
    net_eur         REAL    NOT NULL,
    design_cost_eur REAL    DEFAULT 0.0,
    listing_fee_eur REAL    DEFAULT 0.18,            -- $0.20 al tasso cambio corrente [B4]
    sold_at         REAL    NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_re_sold_at ON revenue_events(sold_at DESC);
CREATE INDEX IF NOT EXISTS idx_re_listing ON revenue_events(etsy_listing_id);
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
        self._chroma_collection = None          # pepe_memory — Etsy/knowledge base
        self._screen_memory_collection = None   # screen_memory — OCR/watcher (Personal)
        self._personal_memory_collection = None # personal_memory — Personal learning loop
        self._shared_memory_collection = None   # shared_memory — bridge cross-domain
        self.__fernet: Fernet | None = None     # lazy-init in _fernet()
        self._ws_broadcaster = None             # callable(event: dict) — impostato da lifespan
        self._bridge_callback = None            # callable(text, domain) — impostato da lifespan

    # ------------------------------------------------------------------
    # Crypto helpers (OAuth token encryption)
    # ------------------------------------------------------------------

    def _fernet(self) -> Fernet:
        """Ritorna un'istanza Fernet derivata da SECRET_KEY (lazy, cached)."""
        if self.__fernet is None:
            digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
            key = base64.urlsafe_b64encode(digest)
            self.__fernet = Fernet(key)
        return self.__fernet

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
        _migrations = [
            # --- migrazioni storiche ---
            "ALTER TABLE etsy_listings ADD COLUMN views_prev INTEGER DEFAULT 0",
            "ALTER TABLE conversations ADD COLUMN domain TEXT NOT NULL DEFAULT 'etsy'",
            "ALTER TABLE agent_logs ADD COLUMN domain TEXT NOT NULL DEFAULT 'etsy'",
            "ALTER TABLE llm_calls ADD COLUMN provider TEXT NOT NULL DEFAULT 'anthropic'",
            "ALTER TABLE pending_actions ADD COLUMN task_id TEXT",
            # --- Blocco 1-2: estensione production_queue ---
            # Dati di input
            "ALTER TABLE production_queue ADD COLUMN keywords TEXT",
            "ALTER TABLE production_queue ADD COLUMN entry_score REAL DEFAULT 0.0",
            # Design output
            "ALTER TABLE production_queue ADD COLUMN design_prompt TEXT",
            "ALTER TABLE production_queue ADD COLUMN image_url TEXT",
            "ALTER TABLE production_queue ADD COLUMN thumbnail_path TEXT",
            "ALTER TABLE production_queue ADD COLUMN listing_title TEXT",
            "ALTER TABLE production_queue ADD COLUMN listing_description TEXT",
            "ALTER TABLE production_queue ADD COLUMN listing_tags TEXT",
            "ALTER TABLE production_queue ADD COLUMN listing_price REAL",
            # Approvazione
            "ALTER TABLE production_queue ADD COLUMN approval_sent_at REAL",
            "ALTER TABLE production_queue ADD COLUMN approval_message_id INTEGER",
            "ALTER TABLE production_queue ADD COLUMN approval_chat_id INTEGER",
            "ALTER TABLE production_queue ADD COLUMN skip_reason TEXT",
            "ALTER TABLE production_queue ADD COLUMN skip_count_user INTEGER DEFAULT 0",
            "ALTER TABLE production_queue ADD COLUMN skip_count_timeout INTEGER DEFAULT 0",
            # Scheduling / pubblicazione
            "ALTER TABLE production_queue ADD COLUMN scheduled_publish_at REAL",
            "ALTER TABLE production_queue ADD COLUMN published_at REAL",
            # Costi
            "ALTER TABLE production_queue ADD COLUMN llm_cost_usd REAL DEFAULT 0.0",
            "ALTER TABLE production_queue ADD COLUMN image_cost_usd REAL DEFAULT 0.0",
            "ALTER TABLE production_queue ADD COLUMN listing_fee_usd REAL DEFAULT 0.20",   # 🔴 [B2/video]
            "ALTER TABLE production_queue ADD COLUMN ads_activated INTEGER DEFAULT 0",      # 🔴 [B2/video]
            # Tracciabilità loop
            "ALTER TABLE production_queue ADD COLUMN loop_run_id TEXT",
            # --- Blocco 4: listing_performance + niche_intelligence + revenue_events ---
            # listing_performance — template/color_scheme per CTR attribution
            "ALTER TABLE listing_performance ADD COLUMN template TEXT",
            "ALTER TABLE listing_performance ADD COLUMN color_scheme TEXT",
            # listing_performance — click tracking e CTR [B4]
            "ALTER TABLE listing_performance ADD COLUMN clicks INTEGER DEFAULT 0",
            "ALTER TABLE listing_performance ADD COLUMN ctr REAL DEFAULT 0.0",
            # listing_performance — Ladder System diagnostico [B4]
            "ALTER TABLE listing_performance ADD COLUMN ladder_level TEXT",
            "ALTER TABLE listing_performance ADD COLUMN last_diagnostic_at REAL",
            # niche_intelligence — CTR aggregato e velocità vendita [B4]
            "ALTER TABLE niche_intelligence ADD COLUMN avg_ctr REAL DEFAULT 0.0",
            "ALTER TABLE niche_intelligence ADD COLUMN avg_days_to_sale REAL",
            # revenue_events — fee listing separata dal design cost [B4]
            "ALTER TABLE revenue_events ADD COLUMN listing_fee_eur REAL DEFAULT 0.18",
        ]
        for migration_sql in _migrations:
            try:
                await self._db.execute(migration_sql)
                await self._db.commit()
            except Exception as exc:
                if "duplicate column name" in str(exc).lower():
                    pass  # Colonna già esistente — ignorato
                else:
                    logger.error("Migrazione DB fallita: %s — %s", migration_sql, exc)
                    raise

        # Indici per nuove colonne (idempotenti)
        _new_indexes = [
            # --- indici storici ---
            "CREATE INDEX IF NOT EXISTS idx_conv_domain ON conversations(domain)",
            "CREATE INDEX IF NOT EXISTS idx_agent_logs_domain ON agent_logs(domain)",
            "CREATE INDEX IF NOT EXISTS idx_pa_task ON pending_actions(task_id)",
            # --- Blocco 2: nuovi indici production_queue ---
            "CREATE INDEX IF NOT EXISTS idx_pq_niche ON production_queue(niche, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_pq_scheduled ON production_queue(scheduled_publish_at) WHERE scheduled_publish_at IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_pq_loop_run ON production_queue(loop_run_id)",
        ]
        for idx_sql in _new_indexes:
            try:
                await self._db.execute(idx_sql)
                await self._db.commit()
            except Exception as exc:
                logger.error("Creazione indice DB fallita: %s — %s", idx_sql, exc)
                raise

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
            # screen_memory: collection separata per OCR/watcher (dominio Personal)
            # Stessa embedding function, path ChromaDB condiviso
            self._screen_memory_collection = chroma_client.get_or_create_collection(
                name="screen_memory",
                embedding_function=voyage_ef,
            )
            # personal_memory: knowledge base strutturata del dominio Personal
            # Separata da screen_memory (OCR raw) e da pepe_memory (Etsy)
            self._personal_memory_collection = chroma_client.get_or_create_collection(
                name="personal_memory",
                embedding_function=voyage_ef,
            )
            # shared_memory: insight cross-domain sintetizzati dal bridge
            # Contiene pattern che emergono dall'incrocio tra Etsy e Personal.
            # Letta da entrambi i domini per arricchire il contesto LLM.
            self._shared_memory_collection = chroma_client.get_or_create_collection(
                name="shared_memory",
                embedding_function=voyage_ef,
            )
        except Exception:
            # ChromaDB/Voyage non disponibile — continua solo con SQLite
            self._chroma_collection = None
            self._screen_memory_collection = None
            self._personal_memory_collection = None
            self._shared_memory_collection = None

        # Cleanup: chiudi agent_logs rimasti in 'running' da sessioni precedenti
        try:
            await self._db.execute(
                "UPDATE agent_logs SET status='failed' WHERE status='running'"
            )
            await self._db.commit()
        except Exception:
            pass

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def get_db(self):
        """
        Ritorna la connessione aiosqlite raw.
        Usato da service layer (ProductionQueueService, MarketDataAgent, etc.)
        che gestiscono le proprie query senza passare per metodi MemoryManager.
        La connessione è garantita aperta dopo initialize().
        """
        return self._db

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
                   AND status != 'input_required'
                   ORDER BY updated_at DESC LIMIT 1""",
                (agent_name,),
            )
        else:
            cursor = await self._db.execute(
                """SELECT * FROM agent_logs
                   WHERE status = 'failed' AND status != 'input_required'
                   ORDER BY updated_at DESC LIMIT 1"""
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
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
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
        provider: str = "anthropic",
    ) -> int:
        """Logga una chiamata LLM nel DB.

        Args:
            provider: 'anthropic' o 'ollama'. Default 'anthropic' per backward compat.
        """
        cursor = await self._db.execute(
            """INSERT INTO llm_calls
               (task_id, step_id, agent_name, model, provider, system_prompt, messages,
                response, input_tokens, output_tokens, cache_read_tokens,
                cache_write_tokens, cost_usd, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                step_id,
                agent_name,
                model,
                provider,
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

        # Escludi step_type 'tool_call' e 'llm_call': i dati sono già nelle
        # tabelle dedicate (tool_calls, llm_calls) con info più ricche.
        cursor = await self._db.execute(
            "SELECT * FROM agent_steps WHERE task_id = ? AND step_type NOT IN ('tool_call', 'llm_call')",
            (task_id,),
        )
        for row in await cursor.fetchall():
            d = dict(row)
            d["type"] = "agent_step"
            d["input_data"] = _json_loads(d.get("input_data"))
            d["output_data"] = _json_loads(d.get("output_data"))
            results.append(d)

        cursor = await self._db.execute(
            "SELECT * FROM llm_calls WHERE task_id = ?",
            (task_id,),
        )
        for row in await cursor.fetchall():
            d = dict(row)
            d["type"] = "llm_call"
            d["messages"] = _json_loads(d.get("messages"))
            results.append(d)

        cursor = await self._db.execute(
            "SELECT * FROM tool_calls WHERE task_id = ?",
            (task_id,),
        )
        for row in await cursor.fetchall():
            d = dict(row)
            d["type"] = "tool_call"
            d["input_params"] = _json_loads(d.get("input_params"))
            d["output_result"] = _json_loads(d.get("output_result"))
            results.append(d)

        results.sort(key=lambda x: x.get("timestamp", ""))
        return results

    async def get_cost_breakdown(self, period_days: int = 30) -> dict:
        """Cost breakdown per agente, per tool, per giorno, e totale."""
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime("%Y-%m-%d %H:%M:%S")

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

        # Token per giorno (input + output + cache_read per giorno)
        cursor = await self._db.execute(
            """SELECT DATE(timestamp) as day,
                      COALESCE(SUM(input_tokens), 0)       AS input,
                      COALESCE(SUM(output_tokens), 0)      AS output,
                      COALESCE(SUM(cache_read_tokens), 0)  AS cache_read
               FROM llm_calls WHERE timestamp >= ?
               GROUP BY DATE(timestamp) ORDER BY day""",
            (since,),
        )
        tokens_per_day = {
            row["day"]: {
                "input":      int(row["input"]),
                "output":     int(row["output"]),
                "cache_read": int(row["cache_read"]),
            }
            for row in await cursor.fetchall()
        }

        # Totale
        total = sum(per_agent.values())

        # Cache savings — per ogni modello calcola quanto si è risparmiato
        # rispetto a pagare il full input price al posto del cache_read price.
        # Formula: savings = cache_read_tokens × (input_price - cache_read_price) / 1_000_000
        cursor = await self._db.execute(
            """SELECT model,
                      SUM(cache_read_tokens)  AS total_cache_read,
                      SUM(cache_write_tokens) AS total_cache_write,
                      SUM(input_tokens)       AS total_input,
                      SUM(output_tokens)      AS total_output
               FROM llm_calls WHERE timestamp >= ?
               GROUP BY model""",
            (since,),
        )
        rows = await cursor.fetchall()

        total_cache_read: int = 0
        total_cache_write: int = 0
        total_input: int = 0
        total_output: int = 0
        savings_usd: float = 0.0

        for row in rows:
            model: str = row["model"] or ""
            cr: int = row["total_cache_read"] or 0
            cw: int = row["total_cache_write"] or 0
            inp: int = row["total_input"] or 0
            out: int = row["total_output"] or 0
            total_output += out

            # Identifica tier pricing dal nome modello
            if "haiku" in model.lower():
                in_price = settings.LLM_HAIKU_INPUT_PRICE
                cr_price = settings.LLM_HAIKU_CACHE_READ_PRICE
            else:  # sonnet o altro modello non-haiku
                in_price = settings.LLM_SONNET_INPUT_PRICE
                cr_price = settings.LLM_SONNET_CACHE_READ_PRICE

            savings_usd += cr * (in_price - cr_price) / 1_000_000

            total_cache_read += cr
            total_cache_write += cw
            total_input += inp

        # Efficienza cache: % dei token di input serviti da cache vs pagati full
        denominator = total_cache_read + total_input
        efficiency_pct = round(total_cache_read / denominator * 100, 1) if denominator > 0 else 0.0

        cache = {
            "read_tokens": total_cache_read,
            "write_tokens": total_cache_write,
            "savings_usd": round(savings_usd, 6),
            "efficiency_pct": efficiency_pct,
        }

        tokens = {
            "input": total_input,
            "output": total_output,
            "total": total_input + total_output,
        }

        return {
            "per_agent": per_agent,
            "per_tool": per_tool,
            "per_day": per_day,
            "tokens_per_day": tokens_per_day,
            "total": total,
            "cache": cache,
            "tokens": tokens,
        }

    async def get_agent_logs_summary(self, period_days: int = 14) -> dict:
        """Aggregati task da agent_logs per il frontend Analytics.

        Ritorna:
          total, completed, failed, running, by_status,
          per_day (YYYY-MM-DD → {status: count}),
          per_agent (agent_name → {total, completed, failed, cost}),
          production_queue stats.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime("%Y-%m-%d %H:%M:%S")

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
    # Memory query tracking (neural brain)
    # ------------------------------------------------------------------

    def set_ws_broadcaster(self, broadcaster) -> None:
        """Inietta il broadcaster WebSocket (callable async).

        Chiamato da lifespan in main.py dopo la creazione di ws_manager.
        Permette a MemoryManager di emettere eventi memory_query sul WS
        senza dipendere direttamente da main.py (no circular import).
        """
        self._ws_broadcaster = broadcaster

    def set_bridge_callback(self, callback) -> None:
        """Inietta il callback del KnowledgeBridge (callable async).

        Firma attesa: async def callback(text: str, source_domain: str) -> None

        Chiamato da lifespan in main.py dopo l'inizializzazione del bridge.
        Ogni store_insight / store_personal_insight triggera il bridge in modo
        fire-and-forget (asyncio.create_task) senza bloccare la pipeline principale.
        """
        self._bridge_callback = callback

    async def log_memory_query(
        self,
        doc_ids: list[str],
        collection: str,
        agent: str = "unknown",
        query_text: str | None = None,
    ) -> None:
        """Registra una query ChromaDB nella tabella memory_queries e invia WS event.

        Chiamata internamente da query_chromadb() e search_screen_memory().
        Silente in caso di errore — non deve bloccare il flusso principale.
        """
        if not doc_ids:
            return
        try:
            await self._db.execute(
                """INSERT INTO memory_queries (agent, collection, doc_ids, query_text)
                   VALUES (?, ?, ?, ?)""",
                (agent, collection, json.dumps(doc_ids), query_text),
            )
            await self._db.commit()
        except Exception as exc:
            logger.warning("log_memory_query fallito: %s", exc)
            return

        # Broadcast WS event per il neural brain (live node activation)
        if self._ws_broadcaster is not None:
            try:
                await self._ws_broadcaster({
                    "type": "memory_query",
                    "agent": agent,
                    "collection": collection,
                    "ids": doc_ids,
                    "query": query_text,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
            except Exception as exc:
                logger.warning("log_memory_query WS broadcast fallito: %s", exc)

    async def get_node_access_history(
        self,
        doc_id: str,
        collection: str,
        limit: int = 20,
    ) -> list[dict]:
        """Restituisce le ultime `limit` query che hanno acceduto al doc_id specificato.

        Filtra memory_queries dove doc_ids JSON contiene doc_id.
        """
        try:
            cursor = await self._db.execute(
                """SELECT agent, collection, doc_ids, query_text, queried_at
                   FROM memory_queries
                   WHERE collection = ?
                     AND doc_ids LIKE ?
                   ORDER BY queried_at DESC
                   LIMIT ?""",
                (collection, f'%"{doc_id}"%', limit),
            )
            rows = await cursor.fetchall()
            out = []
            for row in rows:
                ids = _json_loads(row["doc_ids"]) or []
                if doc_id in ids:
                    out.append({
                        "agent": row["agent"],
                        "collection": row["collection"],
                        "query_text": row["query_text"],
                        "queried_at": row["queried_at"],
                    })
            return out
        except Exception as exc:
            logger.warning("get_node_access_history fallito: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Finance data helpers
    # ------------------------------------------------------------------

    async def get_revenue_stats(self, period_days: int = 30) -> dict:
        """Revenue aggregata dal DB locale (etsy_listings).

        Ritorna: total_revenue_eur, total_sales, active_count, draft_count,
        avg_price_eur, avg_revenue_per_listing.
        Nessuna chiamata Etsy — dati locali last_synced_at o created_at.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        cursor = await self._db.execute(
            """SELECT
               COALESCE(SUM(revenue_eur), 0.0)  AS total_revenue_eur,
               COALESCE(SUM(sales), 0)           AS total_sales,
               COUNT(*)                           AS total_listings,
               SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_count,
               SUM(CASE WHEN status = 'draft'  THEN 1 ELSE 0 END) AS draft_count,
               COALESCE(AVG(price_eur), 0.0)     AS avg_price_eur
               FROM etsy_listings
               WHERE created_at >= ? OR last_synced_at >= ?""",
            (since, since),
        )
        row = await cursor.fetchone()
        if not row:
            return {
                "total_revenue_eur": 0.0,
                "total_sales": 0,
                "total_listings": 0,
                "active_count": 0,
                "draft_count": 0,
                "avg_price_eur": 0.0,
                "avg_revenue_per_listing": 0.0,
            }
        d = dict(row)
        total_listings = d.get("total_listings") or 0
        d["avg_revenue_per_listing"] = (
            d["total_revenue_eur"] / total_listings if total_listings else 0.0
        )
        return d

    async def get_revenue_by_niche(self, period_days: int = 30) -> list[dict]:
        """Revenue, vendite, listing count per nicchia nel periodo."""
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = await self._db.execute(
            """SELECT niche,
               COUNT(*)                          AS listing_count,
               COALESCE(SUM(sales), 0)           AS total_sales,
               COALESCE(SUM(revenue_eur), 0.0)   AS total_revenue_eur,
               COALESCE(AVG(price_eur), 0.0)     AS avg_price_eur
               FROM etsy_listings
               WHERE (created_at >= ? OR last_synced_at >= ?)
               AND niche IS NOT NULL AND niche != ''
               GROUP BY niche
               ORDER BY total_revenue_eur DESC""",
            (since, since),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_revenue_by_product_type(self, period_days: int = 30) -> list[dict]:
        """Revenue, vendite per product_type nel periodo."""
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = await self._db.execute(
            """SELECT product_type,
               COUNT(*)                          AS listing_count,
               COALESCE(SUM(sales), 0)           AS total_sales,
               COALESCE(SUM(revenue_eur), 0.0)   AS total_revenue_eur,
               COALESCE(AVG(price_eur), 0.0)     AS avg_price_eur
               FROM etsy_listings
               WHERE (created_at >= ? OR last_synced_at >= ?)
               AND product_type IS NOT NULL AND product_type != ''
               GROUP BY product_type
               ORDER BY total_revenue_eur DESC""",
            (since, since),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_model_cost_breakdown(self, period_days: int = 30) -> list[dict]:
        """Costo, token totali, chiamate per modello LLM nel periodo."""
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = await self._db.execute(
            """SELECT model,
               COUNT(*)                          AS call_count,
               COALESCE(SUM(input_tokens), 0)    AS total_input_tokens,
               COALESCE(SUM(output_tokens), 0)   AS total_output_tokens,
               COALESCE(SUM(cost_usd), 0.0)      AS total_cost_usd
               FROM llm_calls
               WHERE timestamp >= ?
               GROUP BY model
               ORDER BY total_cost_usd DESC""",
            (since,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_daily_revenue_trend(self, period_days: int = 30) -> list[dict]:
        """Revenue giornaliera cumulativa da etsy_listings (per trend chart)."""
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = await self._db.execute(
            """SELECT DATE(last_synced_at) AS day,
               COALESCE(SUM(revenue_eur), 0.0) AS daily_revenue_eur,
               COALESCE(SUM(sales), 0)          AS daily_sales
               FROM etsy_listings
               WHERE last_synced_at >= ?
               GROUP BY DATE(last_synced_at)
               ORDER BY day""",
            (since,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

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
        # Aggiornamento atomico: views_prev e stats nella stessa transazione.
        # BEGIN IMMEDIATE blocca writer concorrenti — nessuna coroutine può
        # leggere uno stato parziale (views_prev aggiornato, views vecchio).
        await self._db.execute("BEGIN IMMEDIATE")
        try:
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
        except Exception:
            await self._db.rollback()
            raise

    async def get_etsy_listings(self, status: str | None = None, limit: int | None = None) -> list[dict]:
        limit_clause = f" LIMIT {int(limit)}" if limit else ""
        if status:
            cursor = await self._db.execute(
                f"SELECT * FROM etsy_listings WHERE status = ? ORDER BY created_at DESC{limit_clause}",
                (status,),
            )
        else:
            cursor = await self._db.execute(
                f"SELECT * FROM etsy_listings ORDER BY created_at DESC{limit_clause}"
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
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
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
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
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
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
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
        task_id: str | None = None,
    ) -> None:
        """INSERT OR REPLACE — sovrascrive pending_action precedente dello stesso tipo."""
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=expires_hours)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        await self._db.execute(
            """INSERT OR REPLACE INTO pending_actions
               (action_type, payload, expires_at, task_id)
               VALUES (?, ?, ?, ?)""",
            (action_type, _json_dumps(payload), expires_at, task_id),
        )
        await self._db.commit()

    async def get_pending_action(self, action_type: str) -> dict | None:
        """Ritorna None se assente o scaduto."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
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

    async def get_pending_input_for_task(self, task_id: str) -> dict | None:
        """Recupera pending_action collegata a un task specifico."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """SELECT * FROM pending_actions
               WHERE task_id = ? AND expires_at > ?""",
            (task_id, now),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["payload"] = _json_loads(d.get("payload"))
        return d

    async def resolve_pending_input(self, task_id: str) -> None:
        """Marca come risolta la pending_action di un task (dopo risposta utente)."""
        await self._db.execute(
            "DELETE FROM pending_actions WHERE task_id = ?",
            (task_id,),
        )
        await self._db.commit()

    async def get_pending_input_tasks(self) -> list[dict]:
        """Lista task in stato INPUT_REQUIRED (pending_actions con action_type=clarification, non scadute)."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """SELECT * FROM pending_actions
               WHERE action_type = 'clarification' AND expires_at > ?
               ORDER BY rowid DESC""",
            (now,),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["payload"] = _json_loads(d.get("payload"))
            result.append(d)
        return result

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

        I valori `access_token_encrypted` / `refresh_token_encrypted` nel dict
        restituito sono già decifrati — i nomi dei campi restano per compatibilità.
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
            data["access_token_encrypted"] = fernet.decrypt(
                data["access_token_encrypted"].encode()
            ).decode()
            data["refresh_token_encrypted"] = fernet.decrypt(
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
            asyncio.create_task(self._bridge_callback(text, "etsy"))
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
            asyncio.create_task(
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
            pass

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
            pass

        # Nessun dato recente disponibile
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
                asyncio.create_task(
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
            asyncio.create_task(self._bridge_callback(text, "personal"))
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
                asyncio.create_task(
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
            pass

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
            pass

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
                asyncio.create_task(
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

    # ------------------------------------------------------------------
    # Query pubbliche esposte da main.py
    # ------------------------------------------------------------------

    async def get_scheduled_tasks(self) -> list[dict]:
        """Task schedulati dal DB, ordinati per prossima esecuzione."""
        cursor = await self._db.execute("SELECT * FROM scheduled_tasks ORDER BY next_run")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_recent_agent_steps(self, limit: int = 50, agent_name: str | None = None) -> list[dict]:
        """Ultimi N step (opzionalmente filtrati per agente), in ordine cronologico crescente."""
        if agent_name:
            cursor = await self._db.execute(
                """SELECT id, task_id, agent_name, step_number, step_type,
                          description, duration_ms, timestamp
                   FROM agent_steps
                   WHERE agent_name = ?
                   ORDER BY id DESC LIMIT ?""",
                (agent_name, limit),
            )
        else:
            cursor = await self._db.execute(
                """SELECT id, task_id, agent_name, step_number, step_type,
                          description, duration_ms, timestamp
                   FROM agent_steps
                   ORDER BY id DESC LIMIT ?""",
                (limit,),
            )
        rows = await cursor.fetchall()
        return list(reversed([dict(r) for r in rows]))

    async def get_personal_recalls(self, limit: int = 10) -> list[dict]:
        """Ultimi N recall completati dall'agente recall/personal con risposta troncata."""
        cursor = await self._db.execute(
            """SELECT task_id, input_data, output_data, created_at, status
               FROM agent_logs
               WHERE agent_name = 'recall' AND domain = 'personal'
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            try:
                inp = json.loads(r["input_data"] or "{}")
                out = json.loads(r["output_data"] or "{}")
            except Exception:
                inp, out = {}, {}
            response_raw = out.get("response") or out.get("answer") or ""
            result.append({
                "task_id": r["task_id"],
                "query": inp.get("query", ""),
                "response": response_raw[:200] + ("…" if len(response_raw) > 200 else ""),
                "status": r["status"],
                "timestamp": r["created_at"],
            })
        return result

    async def get_domain_agent_stats(self, domain: str = "personal", days: int = 14) -> dict[str, dict]:
        """Aggregati completati/falliti per agente in un dominio, ultimi N giorni."""
        since = f"-{days} days"
        cursor = await self._db.execute(
            """SELECT agent_name, status, COUNT(*) as cnt
               FROM agent_logs
               WHERE domain = ? AND created_at >= datetime('now', ?)
               GROUP BY agent_name, status
               ORDER BY agent_name, status""",
            (domain, since),
        )
        rows = await cursor.fetchall()
        stats: dict[str, dict] = {}
        for r in rows:
            name = r["agent_name"]
            if name not in stats:
                stats[name] = {"completed": 0, "failed": 0, "running": 0}
            key = r["status"] if r["status"] in stats[name] else "running"
            stats[name][key] = r["cnt"]
        return stats

    # ------------------------------------------------------------------
    # Reminders
    # ------------------------------------------------------------------

    async def add_reminder(
        self,
        text: str,
        trigger_at: str,
        recurring_rule: str | None = None,
    ) -> int:
        """Inserisce un reminder. Restituisce l'id generato."""
        async with self._db.execute(
            """INSERT INTO reminders (text, trigger_at, recurring_rule)
               VALUES (?, ?, ?)""",
            (text, trigger_at, recurring_rule),
        ) as cur:
            await self._db.commit()
            return cur.lastrowid

    async def get_due_reminders(self) -> list[dict]:
        """Reminder con trigger_at <= now() e status pending."""
        async with self._db.execute(
            """SELECT * FROM reminders
               WHERE trigger_at <= datetime('now')
               AND status = 'pending'
               ORDER BY trigger_at ASC"""
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def mark_reminder_sent(self, reminder_id: int, telegram_msg_id: int = 0) -> None:
        await self._db.execute(
            "UPDATE reminders SET status='sent', telegram_msg_id=? WHERE id=?",
            (telegram_msg_id, reminder_id),
        )
        await self._db.commit()

    async def acknowledge_reminder(self, telegram_msg_id: int) -> bool:
        """Marca come acknowledged via message_id della reply. Restituisce True se trovato."""
        async with self._db.execute(
            "SELECT id FROM reminders WHERE telegram_msg_id=? AND status='sent'",
            (telegram_msg_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return False
        await self._db.execute(
            "UPDATE reminders SET status='acknowledged', acknowledged_at=datetime('now') WHERE id=?",
            (row["id"],),
        )
        await self._db.commit()
        return True

    async def get_reminder_notion_id(self, telegram_msg_id: int) -> str | None:
        """Restituisce notion_page_id per un reminder dato il telegram_msg_id."""
        async with self._db.execute(
            "SELECT notion_page_id FROM reminders WHERE telegram_msg_id=?",
            (telegram_msg_id,),
        ) as cur:
            row = await cur.fetchone()
        return row["notion_page_id"] if row else None

    async def get_reminder_notion_id_by_id(self, reminder_id: int) -> str | None:
        """Restituisce notion_page_id per un reminder dato il suo id (per cancel)."""
        async with self._db.execute(
            "SELECT notion_page_id FROM reminders WHERE id=?",
            (reminder_id,),
        ) as cur:
            row = await cur.fetchone()
        return row["notion_page_id"] if row else None

    async def cancel_reminder(self, reminder_id: int) -> None:
        await self._db.execute(
            "UPDATE reminders SET status='cancelled' WHERE id=?",
            (reminder_id,),
        )
        await self._db.commit()

    async def get_pending_reminders(self) -> list[dict]:
        """Tutti i reminder pending con trigger futuri, ordinati per trigger_at."""
        async with self._db.execute(
            """SELECT * FROM reminders
               WHERE status = 'pending'
               AND trigger_at > datetime('now')
               ORDER BY trigger_at ASC"""
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_sent_unacknowledged(self, hours: int = 4) -> list[dict]:
        """Reminder inviati ma non acknowledged da più di N ore."""
        async with self._db.execute(
            """SELECT * FROM reminders
               WHERE status = 'sent'
               AND acknowledged_at IS NULL
               AND trigger_at <= datetime('now', ?)
               ORDER BY trigger_at ASC""",
            (f"-{hours} hours",),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def reschedule_recurring(self, reminder_id: int) -> None:
        """Calcola il prossimo trigger_at da recurring_rule e resetta lo status a pending."""
        from datetime import datetime, timedelta

        async with self._db.execute(
            "SELECT trigger_at, recurring_rule FROM reminders WHERE id=?",
            (reminder_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row or not row["recurring_rule"]:
            return

        try:
            current = datetime.fromisoformat(row["trigger_at"])
        except ValueError:
            return

        rule: str = row["recurring_rule"]
        next_dt: datetime | None = None

        if rule == "daily":
            next_dt = current + timedelta(days=1)
        elif rule == "weekdays":
            next_dt = current + timedelta(days=1)
            while next_dt.weekday() >= 5:
                next_dt += timedelta(days=1)
        elif rule.startswith("weekly:"):
            day_names = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
            days_str = rule.split(":", 1)[1].split(",")
            target_days = sorted(day_names[d.strip()] for d in days_str if d.strip() in day_names)
            if target_days:
                candidate = current + timedelta(days=1)
                for _ in range(8):
                    if candidate.weekday() in target_days:
                        next_dt = candidate
                        break
                    candidate += timedelta(days=1)
        elif rule.startswith("monthly:"):
            try:
                day_num = int(rule.split(":", 1)[1])
                month = current.month + 1
                year = current.year + (month - 1) // 12
                month = (month - 1) % 12 + 1
                import calendar
                max_day = calendar.monthrange(year, month)[1]
                next_dt = current.replace(year=year, month=month, day=min(day_num, max_day))
            except (ValueError, IndexError):
                pass

        if next_dt:
            await self._db.execute(
                """UPDATE reminders
                   SET trigger_at=?, status='pending', telegram_msg_id=NULL, acknowledged_at=NULL
                   WHERE id=?""",
                (next_dt.isoformat(), reminder_id),
            )
            await self._db.commit()

    async def update_reminder_notion_id(self, reminder_id: int, notion_page_id: str) -> None:
        await self._db.execute(
            "UPDATE reminders SET notion_page_id=? WHERE id=?",
            (notion_page_id, reminder_id),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Personal Learning
    # ------------------------------------------------------------------

    _WEIGHT_MIN = 0.1
    _WEIGHT_MAX = 0.9
    _ACCEPTANCE_THRESHOLD = 0.02  # |weight_delta| minimo per modificare il peso

    async def upsert_learning(
        self,
        agent: str,
        pattern_type: str,
        pattern_value: str,
        signal_type: str,
        weight_delta: float,
    ) -> None:
        """INSERT OR UPDATE con UNIQUE(agent, pattern_type, pattern_value).

        Weight clampato a [_WEIGHT_MIN, _WEIGHT_MAX].

        Gate di accettazione (UPDATE path only):
        se |weight_delta| < _ACCEPTANCE_THRESHOLD il peso non viene modificato —
        il segnale è troppo debole per essere considerato apprendimento reale.
        occurrences e last_seen vengono sempre aggiornati (il pattern è stato visto).
        Ogni valutazione (accettata o no) viene registrata in learning_evaluations
        tramite save_learning_evaluation(), per permettere analisi future
        via get_pattern_acceptance_rate().

        INSERT path: sempre accettato — nessuna baseline disponibile alla prima osservazione.
        """
        async with self._db.execute(
            "SELECT id, weight, occurrences FROM personal_learning WHERE agent=? AND pattern_type=? AND pattern_value=?",
            (agent, pattern_type, pattern_value),
        ) as cur:
            row = await cur.fetchone()

        if row:
            accepted = abs(weight_delta) >= self._ACCEPTANCE_THRESHOLD
            new_weight = max(self._WEIGHT_MIN, min(self._WEIGHT_MAX, row["weight"] + weight_delta))

            if accepted:
                await self._db.execute(
                    """UPDATE personal_learning
                       SET weight=?, occurrences=?, last_seen=datetime('now'), signal_type=?
                       WHERE id=?""",
                    (new_weight, row["occurrences"] + 1, signal_type, row["id"]),
                )
            else:
                # Segnale troppo debole: aggiorna occurrences e last_seen, peso invariato
                new_weight = row["weight"]
                await self._db.execute(
                    """UPDATE personal_learning
                       SET occurrences=?, last_seen=datetime('now')
                       WHERE id=?""",
                    (row["occurrences"] + 1, row["id"]),
                )

            await self._db.commit()

            # Registra la valutazione — fail-safe, non blocca mai
            try:
                await self.save_learning_evaluation(
                    pattern_id=str(row["id"]),
                    signal_type=signal_type,
                    metric_type=pattern_type,
                    baseline_value=row["weight"],
                    post_value=new_weight,
                    accepted=accepted,
                )
            except Exception as exc:
                logger.debug("save_learning_evaluation fallito (fail-safe): %s", exc)
        else:
            # Prima osservazione: INSERT sempre accettato (nessuna baseline disponibile)
            initial = max(self._WEIGHT_MIN, min(self._WEIGHT_MAX, 0.5 + weight_delta))
            await self._db.execute(
                """INSERT INTO personal_learning
                   (agent, pattern_type, pattern_value, signal_type, weight)
                   VALUES (?, ?, ?, ?, ?)""",
                (agent, pattern_type, pattern_value, signal_type, initial),
            )
            await self._db.commit()

    async def get_learning_patterns(
        self,
        agent: str,
        pattern_type: str | None = None,
        min_weight: float = 0.0,
    ) -> list[dict]:
        if pattern_type:
            async with self._db.execute(
                "SELECT * FROM personal_learning WHERE agent=? AND pattern_type=? AND weight>=? ORDER BY weight DESC",
                (agent, pattern_type, min_weight),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with self._db.execute(
                "SELECT * FROM personal_learning WHERE agent=? AND weight>=? ORDER BY weight DESC",
                (agent, min_weight),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def save_learning_evaluation(
        self,
        pattern_id: str,
        signal_type: str,
        metric_type: str,
        baseline_value: float,
        post_value: float,
        accepted: bool,
    ) -> None:
        """Registra la valutazione di un pattern (accettato o rifiutato) nella tabella learning_evaluations."""
        delta = post_value - baseline_value
        evaluated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await self._db.execute(
            """INSERT INTO learning_evaluations
               (pattern_id, signal_type, metric_type, baseline_value, post_value, delta, accepted, evaluated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (pattern_id, signal_type, metric_type, baseline_value, post_value, delta, int(accepted), evaluated_at),
        )
        await self._db.commit()

    async def get_pattern_acceptance_rate(self, signal_type: str, last_n: int = 20) -> float:
        """Ritorna il tasso di accettazione degli ultimi N pattern per questo signal_type.
        Usato per decidere se il sistema sta imparando cose utili o rumore."""
        async with self._db.execute(
            """SELECT AVG(accepted) FROM (
                 SELECT accepted FROM learning_evaluations
                 WHERE signal_type = ?
                 ORDER BY id DESC LIMIT ?
               )""",
            (signal_type, last_n),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row[0] is None:
            return 0.0
        return float(row[0])

    async def get_baseline_metric(self, metric_type: str, window: int = 10) -> float | None:
        """Calcola il valore baseline della metrica nelle ultime `window` occorrenze.
        Ritorna None se dati insufficienti."""
        async with self._db.execute(
            """SELECT AVG(post_value) FROM (
                 SELECT post_value FROM learning_evaluations
                 WHERE metric_type = ?
                 ORDER BY id DESC LIMIT ?
               )""",
            (metric_type, window),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0])

    async def decay_old_patterns(self, days: int = 7, factor: float = 0.98) -> int:
        """Applica decay ai pattern non visti da più di N giorni. Restituisce numero di righe aggiornate."""
        cursor = await self._db.execute(
            f"""UPDATE personal_learning
               SET weight = MAX({self._WEIGHT_MIN}, weight * ?)
               WHERE last_seen < datetime('now', ?)""",
            (factor, f"-{days} days"),
        )
        updated = cursor.rowcount
        await self._db.commit()
        return updated if updated is not None else 0

    async def detect_watcher_habits(self, days: int = 7, min_days: int = 5) -> list[dict]:
        """Rileva pattern abitudinali Watcher: stessa app in stesso slot orario per min_days+.
        Slot orario = ora arrotondata al multiplo di 2 (0,2,4,...,22)."""
        async with self._db.execute(
            """SELECT
                 json_extract(description, '$.app_name') AS app_name,
                 (CAST(strftime('%H', timestamp) AS INTEGER) / 2 * 2) AS hour_slot,
                 COUNT(DISTINCT date(timestamp)) AS day_count
               FROM agent_steps
               WHERE agent_name = 'watcher'
               AND timestamp >= datetime('now', ?)
               AND json_valid(description)
               GROUP BY app_name, hour_slot
               HAVING day_count >= ?""",
            (f"-{days} days", min_days),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "pattern": f"{r['app_name']}_slot{r['hour_slot']:02d}",
                "app_name": r["app_name"],
                "hour_slot": r["hour_slot"],
                "day_count": r["day_count"],
            }
            for r in rows
            if r["app_name"]
        ]

    async def get_frequent_queries(self, days: int = 7, min_occurrences: int = 3) -> list[str]:
        """Pattern_value di tipo 'topic' con occurrences >= min e last_seen recente."""
        async with self._db.execute(
            """SELECT pattern_value FROM personal_learning
               WHERE pattern_type = 'topic'
               AND last_seen >= datetime('now', ?)
               AND occurrences >= ?
               ORDER BY occurrences DESC""",
            (f"-{days} days", min_occurrences),
        ) as cur:
            rows = await cur.fetchall()
        return [r["pattern_value"] for r in rows]

    async def get_agent_steps_count(self, agent: str = "*", hours: int = 24) -> int:
        """Conta gli step registrati nelle ultime N ore. agent='*' per tutti gli agenti."""
        if agent == "*":
            async with self._db.execute(
                "SELECT COUNT(*) FROM agent_steps WHERE timestamp >= datetime('now', ?)",
                (f"-{hours} hours",),
            ) as cur:
                row = await cur.fetchone()
        else:
            async with self._db.execute(
                "SELECT COUNT(*) FROM agent_steps WHERE agent_name=? AND timestamp >= datetime('now', ?)",
                (agent, f"-{hours} hours"),
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Scheduled Tasks
    # ------------------------------------------------------------------

    async def get_enabled_scheduled_tasks(self) -> list[dict]:
        """Restituisce tutti i task schedulati abilitati."""
        async with self._db.execute(
            "SELECT id, name, cron_expression, agent_name, task_data, enabled "
            "FROM scheduled_tasks WHERE enabled = 1"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_task_last_run(self, task_id: int, last_run_iso: str) -> None:
        """Aggiorna il campo last_run di un task schedulato."""
        await self._db.execute(
            "UPDATE scheduled_tasks SET last_run = ? WHERE id = ?",
            (last_run_iso, task_id),
        )
        await self._db.commit()

    async def get_stale_listings_without_sales(
        self, min_views: int = 50, days_old: int = 30, limit: int = 20
    ) -> list[dict]:
        """Restituisce listing con 0 vendite ma molte views, creati da almeno `days_old` giorni."""
        async with self._db.execute(
            """
            SELECT niche, price_eur, views, sales
            FROM etsy_listings
            WHERE sales = 0 AND views > ?
            AND created_at < datetime('now', ? || ' days')
            LIMIT ?
            """,
            (min_views, f"-{days_old}", limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]


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
