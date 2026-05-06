"""Base class, schema, helpers and crypto for MemoryManager."""
from __future__ import annotations

import json
import logging
import os
import aiosqlite

from apps.backend.core.config import settings
from apps.backend.core.crypto import get_fernet

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
    status TEXT NOT NULL DEFAULT 'pending_design',
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
    audience_target      TEXT,                       -- buyer persona dal Research Agent [M7]
    expansion_potential  TEXT,                       -- high|medium|low — espandibilità niche [M7]
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

-- Identità di brand del negozio (PA-5 ShopIdentityService)
CREATE TABLE IF NOT EXISTS shop_identity (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    aesthetic_name    TEXT    NOT NULL,
    palette_primary   TEXT    NOT NULL,
    palette_secondary TEXT    NOT NULL,
    palette_accent    TEXT    NOT NULL,
    mockup_style      TEXT    NOT NULL,
    tone              TEXT    NOT NULL,
    logo_path         TEXT,
    banner_path       TEXT,
    approved_at       DATETIME,
    approved_by       TEXT    DEFAULT 'andrea',
    is_active         BOOLEAN DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_si_active ON shop_identity(is_active) WHERE is_active = 1;

-- Sezioni Etsy effettive (sincronizzate dall'API Etsy) — PA-6
CREATE TABLE IF NOT EXISTS etsy_sections (
    section_id       TEXT    PRIMARY KEY,
    section_name     TEXT    NOT NULL,
    created_at       DATETIME,
    listing_count    INTEGER DEFAULT 0,
    last_listing_at  DATETIME,
    is_active        BOOLEAN DEFAULT 1
);

-- Mappa niche → sezione Etsy (cuore del sistema sezioni) — PA-6
CREATE TABLE IF NOT EXISTS niche_section_map (
    niche_key        TEXT    PRIMARY KEY,
    section_id       TEXT    REFERENCES etsy_sections(section_id),
    mapped_by        TEXT,
    mapped_at        DATETIME,
    auto_confidence  FLOAT
);

-- Coda niche non mappate (richiede decisione umana) — PA-6
CREATE TABLE IF NOT EXISTS uncategorized_niches (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    niche_key            TEXT    NOT NULL,
    detected_at          DATETIME,
    listing_id           TEXT,
    status               TEXT    DEFAULT 'pending',
    suggested_section_id TEXT,
    suggested_confidence FLOAT,
    UNIQUE (niche_key, status)
);
CREATE INDEX IF NOT EXISTS idx_un_status ON uncategorized_niches(status, detected_at DESC);

-- Coda pin Pinterest (un record per variante per listing) — B-01
CREATE TABLE IF NOT EXISTS pinterest_queue (
    id                    INTEGER  PRIMARY KEY AUTOINCREMENT,
    production_queue_id   INTEGER  REFERENCES production_queue(id),
    pin_variant           INTEGER  NOT NULL,
    image_path            TEXT     NOT NULL,
    title                 TEXT     NOT NULL,
    description           TEXT     NOT NULL,
    board_id              TEXT     NOT NULL,
    scheduled_at          DATETIME NOT NULL,
    published_at          DATETIME,
    pinterest_pin_id      TEXT,
    status                TEXT     DEFAULT 'pending',
    delivery_method       TEXT     DEFAULT 'direct',
    cost_image_gen        FLOAT    DEFAULT 0.0,
    cost_llm              FLOAT    DEFAULT 0.0,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Board Pinterest configurate (keyword-rich names) — B-01
CREATE TABLE IF NOT EXISTS pinterest_boards (
    board_id    TEXT     PRIMARY KEY,
    board_name  TEXT     NOT NULL,
    section_key TEXT     NOT NULL,
    board_type  TEXT     DEFAULT 'section',
    created_at  DATETIME,
    pin_count   INTEGER  DEFAULT 0,
    is_active   BOOLEAN  DEFAULT 1
);
"""


def _json_dumps(obj: Any) -> str | None:
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False, default=str)


def _json_loads(raw: str | None) -> Any:
    if raw is None:
        return None
    return json.loads(raw)


class MemoryBase:
    """Base class with __init__, crypto helpers, init, get_db, and WS/bridge setters."""

    def __init__(self) -> None:
        self._db_path = os.path.join(settings.STORAGE_PATH, "agentpexi.db")
        self._chromadb_path = os.path.join(settings.STORAGE_PATH, "chromadb")
        self._db: aiosqlite.Connection | None = None
        self._chroma_collection = None          # pepe_memory — Etsy/knowledge base
        self._screen_memory_collection = None   # screen_memory — OCR/watcher (Personal)
        self._personal_memory_collection = None # personal_memory — Personal learning loop
        self._shared_memory_collection = None   # shared_memory — bridge cross-domain
        self._ws_broadcaster = None             # callable(event: dict) — impostato da lifespan
        self._bridge_callback = None            # callable(text, domain) — impostato da lifespan
        self.mock_mode: bool = False            # flag globale — sincronizzato da pepe.set_mock_mode()

    # ------------------------------------------------------------------
    # Crypto helpers (OAuth token encryption)
    # ------------------------------------------------------------------

    def _fernet(self):
        """Ritorna l'istanza Fernet condivisa da core.crypto (lazy, cached)."""
        return get_fernet()

    # ------------------------------------------------------------------
    # Init / shutdown
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Inizializza DB SQLite (schema) e ChromaDB collection."""
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        # WAL mode: riduce lock contention con coroutine concorrenti
        # (AutopilotLoop + Scheduler + API scrivono in parallelo)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")  # WAL-safe, più veloce di FULL
        await self._db.commit()
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
            "ALTER TABLE production_queue ADD COLUMN ads_paused INTEGER DEFAULT 0",         # [FE-0.1] tracciamento pausa esplicita
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
            # niche_intelligence — research metadata columns [M7]
            "ALTER TABLE niche_intelligence ADD COLUMN audience_target TEXT",
            "ALTER TABLE niche_intelligence ADD COLUMN expansion_potential TEXT",
            # revenue_events — fee listing separata dal design cost [B4]
            "ALTER TABLE revenue_events ADD COLUMN listing_fee_eur REAL DEFAULT 0.18",
            # production_queue — timestamp base (mancanti nei DB creati prima del DDL aggiornato)
            "ALTER TABLE production_queue ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP",
            "ALTER TABLE production_queue ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP",
            # production_queue — error_message per items 'failed' (L1: skip_reason è riservato a codici skip)
            "ALTER TABLE production_queue ADD COLUMN error_message TEXT",
            # llm_calls — created_at mancante nei DB precedenti (indici idx_llm_calls_*)
            # NB: SQLite non accetta CURRENT_TIMESTAMP in ALTER TABLE ADD COLUMN → NULL per i record storici
            "ALTER TABLE llm_calls ADD COLUMN created_at TEXT DEFAULT NULL",
            # --- PA-1: normalize status DEFAULT ---
            "UPDATE production_queue SET status='pending_design' WHERE status='planned'",
            # --- A.0: product_tier preparatory column (full ladder logic in C.1) ---
            "ALTER TABLE production_queue ADD COLUMN product_tier TEXT DEFAULT 'core'",
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
            # --- Blocco 4: indici per BudgetManager, Ladder System, LearningLoop ---
            # BudgetManager.today_llm_cost() filtra su created_at — senza indice = full scan
            "CREATE INDEX IF NOT EXISTS idx_llm_calls_created_at ON llm_calls(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_llm_calls_agent_created ON llm_calls(agent_name, created_at)",
            # Ladder: ORDER BY snapshot_at DESC LIMIT 1 per production_queue_id
            "CREATE INDEX IF NOT EXISTS idx_lp_pq_snapshot ON listing_performance(production_queue_id, snapshot_at DESC)",
            # Ladder: queries per livello diagnostico
            "CREATE INDEX IF NOT EXISTS idx_lp_ladder ON listing_performance(ladder_level, snapshot_at DESC)",
            # LearningLoop: GROUP BY / WHERE su niche + product_type
            "CREATE INDEX IF NOT EXISTS idx_ni_niche ON niche_intelligence(niche, product_type)",
            # FinanceTracker: queries revenue per listing nel tempo
            "CREATE INDEX IF NOT EXISTS idx_re_listing ON revenue_events(listing_id, created_at)",
            # poll_listing_performance: listing published_at (filtra per status + data)
            "CREATE INDEX IF NOT EXISTS idx_pq_published ON production_queue(status, published_at)",
            "CREATE INDEX IF NOT EXISTS idx_pq_product_tier ON production_queue(product_tier)",
            # --- B-01: pinterest_queue indexes ---
            "CREATE INDEX IF NOT EXISTS idx_pq_status_scheduled ON pinterest_queue(status, scheduled_at)",
            "CREATE INDEX IF NOT EXISTS idx_pq_board_id ON pinterest_queue(board_id, scheduled_at DESC)",
        ]
        for idx_sql in _new_indexes:
            try:
                await self._db.execute(idx_sql)
                await self._db.commit()
            except Exception as exc:
                # Gli indici sono ottimizzazioni — un fallimento non deve bloccare l'avvio.
                # Cause comuni: colonna non ancora presente (verrà aggiunta alla prossima migration),
                # oppure indice già esistente con definizione diversa.
                logger.warning("Creazione indice ignorata: %s — %s", idx_sql, exc)

        # ChromaDB + Voyage AI (lazy: fallisce silenziosamente se non disponibile)
        try:
            import chromadb
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
        except Exception as exc:
            logger.warning("MemoryManager.init: cleanup agent_logs 'running' fallito: %s", exc)
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
