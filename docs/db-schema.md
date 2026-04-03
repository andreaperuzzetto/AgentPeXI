# Schema database

PostgreSQL 16 + pgvector. ORM: SQLAlchemy async. Migrazioni: Alembic.

---

## Convenzioni globali

Ogni tabella ha sempre:
```sql
id          UUID PRIMARY KEY DEFAULT gen_random_uuid()
created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
deleted_at  TIMESTAMPTZ          -- soft delete: NULL = attivo
```

Indici obbligatori su: tutte le FK, `status`, `deal_id`, `client_id`.
Nessuna colonna `SERIAL` o `INTEGER` come PK — solo UUID.

---

## Schema pubblico (sistema AgentPeXI)

Tutte le tabelle di sistema vivono nello schema `public`.

---

### `leads`

Opportunità di business identificate dallo Scout Agent.

```sql
CREATE TABLE leads (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Dati Google Maps
    google_place_id         TEXT NOT NULL UNIQUE,
    business_name           TEXT NOT NULL,
    address                 TEXT,
    city                    TEXT,
    region                  TEXT,
    country                 TEXT DEFAULT 'IT',
    latitude                NUMERIC(10, 7),
    longitude               NUMERIC(10, 7),
    google_rating           NUMERIC(2, 1),
    google_review_count     INTEGER,
    google_category         TEXT,
    website_url             TEXT,
    phone                   TEXT,               -- cifrato a riposo

    -- Classificazione
    sector                  TEXT NOT NULL,       -- chiave da config/sectors.yaml
    service_gap_detected    BOOLEAN DEFAULT FALSE,  -- gap generico rilevato

    -- Servizio potenziale identificato
    suggested_service_type  TEXT,                -- "consulting"|"web_design"|"digital_maintenance"
    gap_signals             JSONB,              -- segnali di gap specifici per servizio

    -- Scoring (scritto da Market Analyst)
    lead_score              INTEGER,             -- 0-100, soglia universale >= 65
    qualified               BOOLEAN,
    disqualify_reason       TEXT,
    gap_summary             TEXT,               -- max 3 frasi
    estimated_value_eur     INTEGER,

    -- Enrichment (scritto da Lead Profiler)
    vat_number              TEXT,               -- cifrato a riposo
    ateco_code              TEXT,
    company_size            TEXT,               -- "solo"|"micro"|"small"|"medium"
    social_facebook_url     TEXT,
    social_instagram_url    TEXT,
    enrichment_confidence   NUMERIC(3, 2),      -- 0.00-1.00
    enrichment_level        TEXT,               -- "basic"|"full"

    -- Vettore per ricerca semantica (pgvector)
    embedding               VECTOR(1536),       -- embedding del gap_summary

    -- Stato
    status                  TEXT NOT NULL DEFAULT 'discovered',
                            -- "discovered"|"analyzing"|"qualified"|"disqualified"|"converted"

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

CREATE INDEX idx_leads_sector         ON leads(sector);
CREATE INDEX idx_leads_status         ON leads(status);
CREATE INDEX idx_leads_qualified      ON leads(qualified);
CREATE INDEX idx_leads_lead_score     ON leads(lead_score DESC);
CREATE INDEX idx_leads_service_type   ON leads(suggested_service_type);
CREATE INDEX idx_leads_embedding      ON leads USING ivfflat (embedding vector_cosine_ops);
```

---

### `clients`

Clienti che hanno approvato almeno una proposta. Creato dal Sales Agent
quando `deal.status` passa a `client_approved`.

```sql
CREATE TABLE clients (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id         UUID REFERENCES leads(id),

    business_name   TEXT NOT NULL,
    vat_number      TEXT,               -- cifrato
    address         TEXT,
    city            TEXT,
    region          TEXT,
    country         TEXT DEFAULT 'IT',

    -- Contatto principale (cifrati a riposo)
    contact_name    TEXT,
    contact_email   TEXT,
    contact_phone   TEXT,

    -- SLA e preferenze
    sla_response_hours  INTEGER DEFAULT 4,
    preferred_language  TEXT DEFAULT 'it',
    timezone            TEXT DEFAULT 'Europe/Rome',

    -- Schema DB isolato per questo cliente
    db_schema_name  TEXT UNIQUE,        -- "client_{id senza trattini}"

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);
```

---

### `deals`

Entità centrale. Attraversa tutte le fasi della pipeline.

```sql
CREATE TABLE deals (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id     UUID NOT NULL REFERENCES leads(id),
    client_id   UUID REFERENCES clients(id),    -- NULL fino a client_approved

    -- Stato pipeline
    status      TEXT NOT NULL DEFAULT 'lead_identified',
                -- Valori: vedere DealStatus in docs/data-models.md

    -- Tipo servizio
    service_type    TEXT NOT NULL,           -- "consulting"|"web_design"|"digital_maintenance"

    sector                  TEXT NOT NULL,
    estimated_value_eur     INTEGER,

    -- GATE 1 — Approvazione proposta dall'operatore
    proposal_human_approved     BOOLEAN NOT NULL DEFAULT FALSE,
    proposal_approved_at        TIMESTAMPTZ,
    proposal_approved_by        TEXT DEFAULT 'operator',
    proposal_rejection_count    INTEGER NOT NULL DEFAULT 0,
    proposal_rejection_notes    TEXT,

    -- GATE 2 — Kickoff erogazione confermato dall'operatore
    kickoff_confirmed       BOOLEAN NOT NULL DEFAULT FALSE,
    kickoff_confirmed_at    TIMESTAMPTZ,

    -- GATE 3 — Consegna approvata dall'operatore
    -- Per consulenza: leggere come "consulting_approved"
    delivery_approved       BOOLEAN NOT NULL DEFAULT FALSE,
    delivery_approved_at    TIMESTAMPTZ,

    -- Billing
    total_price_eur         INTEGER,            -- in centesimi
    deposit_pct             INTEGER DEFAULT 30,
    payment_terms_days      INTEGER DEFAULT 30,

    -- Metadata
    notes                   TEXT,               -- note interne operatore
    lost_reason             TEXT,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

CREATE INDEX idx_deals_lead_id      ON deals(lead_id);
CREATE INDEX idx_deals_client_id    ON deals(client_id);
CREATE INDEX idx_deals_status       ON deals(status);
CREATE INDEX idx_deals_service_type ON deals(service_type);
```

---

### `proposals`

Una per ogni versione generata dal Proposal Agent.

```sql
CREATE TABLE proposals (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id     UUID NOT NULL REFERENCES deals(id),

    version     INTEGER NOT NULL DEFAULT 1,
    pdf_path    TEXT NOT NULL,          -- path MinIO: "clients/{deal_id}/proposals/v{n}.pdf"
    page_count  INTEGER,

    -- Contenuto strutturato (per regenerazione e audit)
    gap_summary         TEXT,
    solution_summary    TEXT,
    service_type        TEXT NOT NULL,  -- "consulting"|"web_design"|"digital_maintenance"
    deliverables_json   JSONB,          -- lista deliverable proposti per servizio
    pricing_json        JSONB,          -- opzioni di prezzo (per progetto)
    timeline_weeks      INTEGER,
    roi_summary         TEXT,

    -- Artefatti collegati (mockup, presentazioni, schemi, roadmap)
    artifact_paths      TEXT[],         -- array path MinIO

    -- Invio
    sent_at             TIMESTAMPTZ,
    portal_link_token   TEXT,           -- JWT, scadenza 72h
    portal_link_expires TIMESTAMPTZ,
    client_viewed_at    TIMESTAMPTZ,

    -- Risposta cliente
    client_response     TEXT,           -- "approved"|"rejected"|"negotiating"
    client_response_at  TIMESTAMPTZ,
    client_notes        TEXT,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ,

    UNIQUE (deal_id, version)
);

CREATE INDEX idx_proposals_deal_id ON proposals(deal_id);
```

---

### `tasks`

Persistenza di ogni `AgentTask` — log completo di tutto ciò che il sistema ha fatto.

```sql
CREATE TABLE tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type            TEXT NOT NULL,      -- "scout.discover"|"proposal.generate"|...
    agent           TEXT NOT NULL,
    deal_id         UUID REFERENCES deals(id),
    client_id       UUID REFERENCES clients(id),

    status          TEXT NOT NULL DEFAULT 'pending',
                    -- "pending"|"running"|"blocked"|"retrying"|"completed"|"failed"|"cancelled"

    payload         JSONB NOT NULL DEFAULT '{}',
    output          JSONB,
    error           TEXT,
    blocked_reason  TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT UNIQUE,

    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

CREATE INDEX idx_tasks_deal_id   ON tasks(deal_id);
CREATE INDEX idx_tasks_agent     ON tasks(agent);
CREATE INDEX idx_tasks_status    ON tasks(status);
CREATE INDEX idx_tasks_idem_key  ON tasks(idempotency_key) WHERE idempotency_key IS NOT NULL;
```

---

### `service_deliveries`

Task di erogazione creati dal Delivery Orchestrator.
Sostituisce la vecchia tabella `dev_tasks` orientata allo sviluppo software.

```sql
CREATE TABLE service_deliveries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id     UUID NOT NULL REFERENCES deals(id),
    client_id   UUID NOT NULL REFERENCES clients(id),

    service_type    TEXT NOT NULL,      -- "consulting"|"web_design"|"digital_maintenance"
    type            TEXT NOT NULL,      -- tipo deliverable (vedi sotto)
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,

    -- Tipi per servizio:
    -- Consulenza: "report"|"workshop"|"roadmap"|"process_schema"|"presentation"
    -- Web Design: "wireframe"|"mockup"|"page"|"branding"|"responsive_check"
    -- Manutenzione: "update_cycle"|"performance_audit"|"security_patch"|"monitoring_setup"

    status      TEXT NOT NULL DEFAULT 'pending',
                -- "pending"|"in_progress"|"review"|"approved"|"completed"|"failed"

    -- Milestone di riferimento
    milestone_name  TEXT,              -- es. "consulting_approved", "mockup_finale", "primo_ciclo"
    milestone_due   DATE,

    -- Dipendenze (ordinamento di esecuzione)
    depends_on  UUID[],                 -- array di service_delivery id

    -- Artefatti generati
    artifact_paths  TEXT[],            -- path MinIO dei documenti/file prodotti

    -- Esecuzione
    assigned_at     TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,

    -- Review operatore
    operator_approved       BOOLEAN,
    operator_approved_at    TIMESTAMPTZ,
    operator_notes          TEXT,

    -- Iterazioni (se operatore rifiuta)
    rejection_count     INTEGER NOT NULL DEFAULT 0,
    rejection_notes     TEXT,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

CREATE INDEX idx_svc_deliveries_deal_id      ON service_deliveries(deal_id);
CREATE INDEX idx_svc_deliveries_client_id    ON service_deliveries(client_id);
CREATE INDEX idx_svc_deliveries_status       ON service_deliveries(status);
CREATE INDEX idx_svc_deliveries_service_type ON service_deliveries(service_type);
```

---

### `email_log`

Log di ogni email inviata (Sales, Account Manager, Billing, Support).

```sql
CREATE TABLE email_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id         UUID REFERENCES deals(id),
    client_id       UUID REFERENCES clients(id),
    task_id         UUID REFERENCES tasks(id),

    direction       TEXT NOT NULL,      -- "outbound"|"inbound"
    agent           TEXT NOT NULL,      -- agente che ha inviato/ricevuto
    template_name   TEXT,
    gmail_message_id TEXT,
    gmail_thread_id  TEXT,

    -- Non loggare indirizzi email qui: usare client_id
    subject         TEXT,
    sent_at         TIMESTAMPTZ,
    opened_at       TIMESTAMPTZ,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

CREATE INDEX idx_email_log_deal_id   ON email_log(deal_id);
CREATE INDEX idx_email_log_client_id ON email_log(client_id);
CREATE INDEX idx_email_log_thread    ON email_log(gmail_thread_id);
```

---

### `tickets`

Ticket di supporto gestiti dal Support Agent.

```sql
CREATE TABLE tickets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id   UUID NOT NULL REFERENCES clients(id),
    deal_id     UUID REFERENCES deals(id),

    -- Classificazione
    type        TEXT,                   -- "service_request"|"update_request"|"how_to"|"billing"|"spam"
    severity    TEXT,                   -- "low"|"medium"|"high"|"critical"
    status      TEXT NOT NULL DEFAULT 'open',
                -- "open"|"in_progress"|"waiting_client"|"resolved"|"closed"

    title       TEXT,
    description TEXT,                   -- contenuto non fidarsi (prompt injection risk)

    -- Comunicazione
    gmail_thread_id TEXT,
    first_response_at   TIMESTAMPTZ,    -- per SLA tracking
    resolved_at         TIMESTAMPTZ,

    -- Collegamento a delivery
    service_delivery_id UUID REFERENCES service_deliveries(id),

    -- Escalation
    escalated       BOOLEAN NOT NULL DEFAULT FALSE,
    escalated_at    TIMESTAMPTZ,
    escalation_reason TEXT,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

CREATE INDEX idx_tickets_client_id ON tickets(client_id);
CREATE INDEX idx_tickets_status    ON tickets(status);
CREATE INDEX idx_tickets_severity  ON tickets(severity);
```

---

### `invoices`

Fatture generate dal Billing Agent.

```sql
CREATE TABLE invoices (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id     UUID NOT NULL REFERENCES deals(id),
    client_id   UUID NOT NULL REFERENCES clients(id),

    -- Identificazione
    invoice_number  TEXT UNIQUE,        -- es. "2025-001"
    milestone       TEXT NOT NULL,      -- "deposit"|"delivery"|"trailing"|"monthly"|"custom"
                                        -- Milestone per servizio:
                                        -- Consulenza: "deposit" (kickoff), "delivery" (consulting_approved)
                                        -- Web Design: "deposit" (kickoff), "delivery" (mockup finale approvato)
                                        -- Manutenzione: "deposit" (kickoff), "monthly" (cicli ricorrenti)

    -- Importi (in centesimi EUR)
    amount_cents    INTEGER NOT NULL,
    tax_rate_pct    NUMERIC(4,2) DEFAULT 22.00,
    tax_cents       INTEGER,
    total_cents     INTEGER,

    -- Stato pagamento
    status          TEXT NOT NULL DEFAULT 'draft',
                    -- "draft"|"sent"|"paid"|"overdue"|"disputed"|"cancelled"
    due_date        DATE NOT NULL,
    paid_at         TIMESTAMPTZ,
    payment_method  TEXT,

    -- Dispute
    billing_dispute         BOOLEAN NOT NULL DEFAULT FALSE,
    billing_dispute_notes   TEXT,

    -- File
    pdf_path        TEXT,               -- path MinIO

    -- Reminder inviati
    reminder_count          INTEGER NOT NULL DEFAULT 0,
    last_reminder_at        TIMESTAMPTZ,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

CREATE INDEX idx_invoices_deal_id   ON invoices(deal_id);
CREATE INDEX idx_invoices_client_id ON invoices(client_id);
CREATE INDEX idx_invoices_status    ON invoices(status);
CREATE INDEX idx_invoices_due_date  ON invoices(due_date);
```

---

### `nps_records`

Survey NPS inviati e ricevuti dall'Account Manager Agent.

```sql
CREATE TABLE nps_records (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id   UUID NOT NULL REFERENCES clients(id),
    deal_id     UUID REFERENCES deals(id),

    trigger     TEXT NOT NULL,          -- "30d"|"90d"|"180d"|"manual"
    score       INTEGER,                -- 0-10, NULL finché non risponde
    comment     TEXT,                   -- risposta libera cliente

    sent_at         TIMESTAMPTZ NOT NULL,
    responded_at    TIMESTAMPTZ,

    -- Azione intrapresa dall'Account Manager
    followup_action TEXT,               -- "upsell_identified"|"escalated"|"noted"

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

CREATE INDEX idx_nps_client_id ON nps_records(client_id);
```

---

### `delivery_reports`

Report prodotti dal Delivery Tracker dopo ogni review di un deliverable.

```sql
CREATE TABLE delivery_reports (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service_delivery_id UUID NOT NULL REFERENCES service_deliveries(id),
    client_id           UUID NOT NULL REFERENCES clients(id),

    approved            BOOLEAN NOT NULL,
    completeness_pct    NUMERIC(5, 2),      -- % completamento deliverable
    blocking_issues     TEXT[],
    notes               TEXT[],

    -- Path del report completo su MinIO
    report_path         TEXT,               -- "clients/{client_id}/delivery/{service_delivery_id}.md"

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

CREATE INDEX idx_delivery_reports_svc_id ON delivery_reports(service_delivery_id);
```

---

### `runs`

Tracciamento dei run LangGraph attivi. Una riga per ogni `graph.invoke()` avviato dall'API.
Usata dal gate poller (Celery Beat, ogni 30 s) per trovare run in attesa di approvazione umana.

```sql
CREATE TABLE runs (
    run_id      TEXT PRIMARY KEY,       -- thread_id LangGraph (UUID come stringa)
    deal_id     UUID REFERENCES deals(id),

    status      TEXT NOT NULL DEFAULT 'running',
                -- "running"|"awaiting_gate"|"completed"|"failed"|"cancelled"

    current_phase   TEXT,               -- "discovery"|"proposal"|"delivery"|"post_sale"
    current_agent   TEXT,

    -- Gate attivo (valorizzato solo quando status = "awaiting_gate")
    gate_type           TEXT,           -- "proposal_review"|"kickoff"|"delivery"
    awaiting_gate_since TIMESTAMPTZ,

    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

CREATE INDEX idx_runs_deal_id ON runs(deal_id);
CREATE INDEX idx_runs_status  ON runs(status);
```

> Il `run_id` coincide con il `thread_id` del Redis checkpointer LangGraph.
> Il gate poller interroga `WHERE status = 'awaiting_gate'` ogni 30 s, verifica il
> flag nel deal corrispondente (`proposal_human_approved`, `kickoff_confirmed`,
> `delivery_approved`), e riprende il run chiamando:
> `graph.invoke(None, config={"configurable": {"thread_id": run_id}})`.

---

## Schema per-cliente (multi-tenancy)

Ogni cliente ha uno schema PostgreSQL dedicato: `client_{id_senza_trattini}`.

### Creazione schema nuovo cliente

Eseguita automaticamente dal Sales Agent quando `deal.status → client_approved`:

```sql
-- Eseguire con permessi superuser
CREATE SCHEMA IF NOT EXISTS client_{id_senza_trattini};

-- Tabelle minime presenti in ogni schema cliente:
-- Dipendono dal servizio erogato.
-- Gli agenti le generano in base al service_type del deal.
-- Non condividono struttura con lo schema public.
```

### Gestione con SQLAlchemy + Alembic

```python
# Connessione a schema specifico per agenti che operano nel workspace cliente
engine = create_async_engine(
    DATABASE_URL,
    connect_args={"options": f"-csearch_path=client_{client_id_clean}"}
)

# I Code Agent usano SEMPRE questo engine quando lavorano su un progetto cliente
# Mai usare il motore dell'applicazione principale (schema public)
```

### Naming convention

```python
def client_schema_name(client_id: UUID) -> str:
    return f"client_{str(client_id).replace('-', '')}"
    # es. UUID "550e8400-e29b-41d4-a716-446655440000"
    #     → "client_550e8400e29b41d4a716446655440000"
```

---

## Dipendenze pgvector

```sql
-- Abilitare l'estensione (una volta sola, eseguire come superuser)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

In `docker-compose.yml` usare l'immagine `pgvector/pgvector:pg16`
(già inclusa) che ha l'estensione pre-installata.

---

## Alembic — note operative

```bash
# Creare migrazione
alembic revision --autogenerate -m "descrizione"

# Applicare
alembic upgrade head

# Rollback di una versione
alembic downgrade -1
```

Ogni migrazione deve includere `upgrade()` e `downgrade()`.
Non modificare migrazioni già applicate — crearne sempre una nuova.
