# Struttura progetto

## Layout directory top-level

```
AgentPeXI/
├── src/                        ← UNICA radice del codice Python (PYTHONPATH=src)
│   ├── api/                    ← FastAPI application
│   ├── agents/                 ← Tutti gli agenti + worker Celery
│   ├── db/                     ← SQLAlchemy engine, session, modelli ORM
│   ├── orchestrator/           ← Grafo LangGraph
│   ├── tools/                  ← Tool wrapper (DB, MinIO, Gmail, Maps, PDF, Render)
│   └── mcp_servers/
│       └── gmail/              ← MCP server Gmail (processo stdio separato)
│
├── frontend/                   ← Next.js 14 App Router (fuori da src/)
│   ├── app/
│   ├── components/
│   ├── lib/
│   └── public/
│
├── scripts/
│   └── render.js               ← Node.js Puppeteer bridge (invocato da Python via subprocess)
│
├── config/
│   ├── external_apis.yaml
│   ├── pricing.yaml
│   ├── scoring.yaml
│   ├── sectors.yaml
│   ├── data/
│   │   └── ateco_codes.json    ← Codici ATECO 2007 ISTAT completi
│   └── templates/
│       ├── email/              ← Template email markdown + frontmatter
│       ├── proposal/           ← base.html proposta commerciale
│       └── artifacts/          ← Template HTML per artefatti (Puppeteer)
│
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│
├── docs/                       ← Questa directory
├── agents/                     ← CLAUDE.md per ogni agente
├── docker-compose.yml
├── pyproject.toml              ← Package config + PYTHONPATH=src
├── requirements.txt
└── .env.example
```

---

## Python src layout — import paths canonici

Il progetto usa il **src layout** standard Python. Il file `pyproject.toml` configura
`src/` come root dei package, che equivale ad aggiungere `src/` a `PYTHONPATH`.

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "agentpexi"
version = "0.1.0"
requires-python = ">=3.12"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
asyncio_mode = "auto"

[tool.ruff]
src = ["src"]
```

Con questa configurazione, tutti gli import partono dai package dentro `src/`:

```python
from db.session         import get_db_session
from db.engine          import engine, AsyncSessionFactory
from db.base            import Base
from db.models.deal     import Deal
from db.models.lead     import Lead

from agents.base        import BaseAgent
from agents.models      import AgentTask, AgentResult, ServiceType, TaskStatus, DealStatus
from agents.worker      import app as celery_app

from orchestrator.graph import build_graph
from orchestrator.state import AgentState

from tools.db_tools     import get_deal, update_deal
from tools.file_store   import upload_file, get_presigned_url
from tools.pdf_generator import render_pdf, render_pdf_to_bytes
from tools.mockup_renderer import render_to_png, render_to_pdf
from tools.google_maps  import search_businesses, get_place_details, geocode_address
from tools.gmail        import send_email, read_thread

from api.main           import app as fastapi_app
from api.deps           import get_current_operator
from api.schemas.deal   import DealResponse
```

---

## Struttura `src/api/`

```
src/api/
├── __init__.py
├── main.py             ← FastAPI app factory + middleware + router include
├── deps.py             ← Dependencies iniettabili (get_current_operator, get_db)
├── middleware.py       ← CORS, logging middleware
├── auth.py             ← JWT creation/verification, login logic
├── routers/
│   ├── __init__.py
│   ├── auth.py         ← POST /auth/token
│   ├── runs.py         ← /runs
│   ├── leads.py        ← /leads
│   ├── deals.py        ← /deals (include gate endpoints)
│   ├── clients.py      ← /clients
│   ├── proposals.py    ← /proposals
│   ├── tasks.py        ← /tasks
│   ├── stats.py        ← /stats
│   └── webhooks.py     ← /webhooks/portal/*
└── schemas/
    ├── __init__.py
    ├── auth.py
    ├── run.py
    ├── lead.py
    ├── deal.py
    ├── client.py
    ├── proposal.py
    ├── task.py
    └── stats.py
```

Entry point: `uvicorn api.main:app --reload --port 8000`
(funziona perché `src/` è in PYTHONPATH via `pyproject.toml`)

```python
# src/api/main.py — punti chiave
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import auth, runs, leads, deals, clients, proposals, tasks, stats, webhooks
from orchestrator.graph import get_checkpointer

@asynccontextmanager
async def lifespan(app: FastAPI):
    checkpointer = get_checkpointer()
    await checkpointer.setup()   # crea tabelle LangGraph checkpoint se non esistono
    yield

app = FastAPI(title="AgentPeXI API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in [auth, runs, leads, deals, clients, proposals, tasks, stats, webhooks]:
    app.include_router(router.router)

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
```

---

## Struttura `src/agents/`

```
src/agents/
├── __init__.py
├── base.py             ← Classe BaseAgent (ABC)
├── models.py           ← AgentTask, AgentResult, ServiceType, TaskStatus, DealStatus (Pydantic)
├── worker.py           ← Celery app + registrazione tutti i task
├── gate_poller.py      ← Celery Beat task: polling runs in awaiting_gate
├── gmail_poller.py     ← Celery Beat task: polling Gmail per nuovi ticket di supporto
│
├── scout/
│   ├── __init__.py
│   ├── agent.py        ← class ScoutAgent(BaseAgent)
│   ├── CLAUDE.md
│   └── prompts/
│       └── system.md   ← Prompt di sistema (caricato a runtime)
│
├── lead_profiler/
│   ├── agent.py
│   ├── CLAUDE.md
│   └── prompts/system.md
│
├── analyst/            ← (stesso pattern per tutti i 12 agenti)
├── proposal/
├── design/
├── sales/
├── delivery_orchestrator/
├── doc_generator/
├── delivery_tracker/
├── account_manager/
├── billing/
└── support/
    ├── agent.py
    ├── CLAUDE.md
    └── prompts/system.md
```

---

## Struttura `src/db/`

```
src/db/
├── __init__.py
├── engine.py           ← AsyncEngine + AsyncSessionFactory
├── session.py          ← get_db_session() context manager
├── base.py             ← DeclarativeBase
└── models/
    ├── __init__.py     ← Importa tutti i modelli (per Alembic autogenerate)
    ├── lead.py
    ├── deal.py
    ├── client.py
    ├── proposal.py
    ├── task.py
    ├── run.py          ← stato run LangGraph
    ├── service_delivery.py
    ├── delivery_report.py
    ├── email_log.py
    ├── ticket.py
    ├── invoice.py
    └── nps_record.py
```

---

## Struttura `src/orchestrator/`

```
src/orchestrator/
├── __init__.py
├── graph.py            ← build_graph() → compilato grafo LangGraph
├── state.py            ← AgentState TypedDict
└── nodes/
    ├── __init__.py
    ├── checkpoint.py   ← verifica gate flags da DB
    ├── delegate.py     ← dispatch task Celery
    ├── router.py       ← decide_next_node() + handler specifici
    └── gates.py        ← logica attesa gate + resume
```

---

## Struttura `src/tools/`

```
src/tools/
├── __init__.py         ← AgentToolError (base exception)
├── db_tools.py         ← CRUD asincrono DB (wrap SQLAlchemy)
├── file_store.py       ← MinIO upload/download/presigned URL
├── google_maps.py      ← Places API + Geocoding con rate limiting
├── gmail.py            ← Gmail MCP client (chiama mcp_servers/gmail/)
├── pdf_generator.py    ← WeasyPrint + Jinja2 → PDF
└── mockup_renderer.py  ← Invoca scripts/render.js via subprocess
```

---

## Struttura `src/mcp_servers/gmail/`

```
src/mcp_servers/gmail/
├── __init__.py
├── server.py           ← MCP server stdio (entry point: python -m mcp_servers.gmail.server)
└── auth.py             ← Gmail OAuth2 credential builder da env vars
```

Vedi `docs/mcp-gmail.md` per specifica completa.

---

## Struttura `scripts/`

```
scripts/
└── render.js           ← Puppeteer Node.js bridge
                        ← Legge JSON da stdin, scrive PNG/PDF, exit 0
                        ← package.json: { "type": "module", puppeteer: "^22" }
```

Vedi [Puppeteer bridge — pattern invocazione](#) nel paragrafo sotto.

---

## Comandi con src layout

```bash
# Setup iniziale
python -m venv .venv
source .venv/bin/activate
pip install -e .                    # installa il package in editable mode (legge pyproject.toml)
pip install -r requirements.txt

# Alternativa senza install -e (test rapido)
PYTHONPATH=src uvicorn api.main:app --reload --port 8000

# Comandi canonici (con .venv attivo post pip install -e .)
uvicorn api.main:app --reload --port 8000
celery -A agents.worker worker --loglevel=info --concurrency=4
python -m orchestrator.graph --dev
alembic upgrade head                # alembic cerca alembic/env.py, che importa da src/

# Script Node.js render
cd scripts && npm install           # installa puppeteer
node scripts/render.js              # non eseguire direttamente — invocato da mockup_renderer.py
```

---

## Alembic con src layout

`alembic/env.py` deve importare i modelli da `src/`:

```python
# alembic/env.py
import sys
from pathlib import Path

# Aggiungi src/ al path per Alembic (che non usa pyproject.toml)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db.base import Base
from db.models import *     # importa tutti i modelli per autogenerate
# DATABASE_SYNC_URL: usa driver psycopg2 (sync), non asyncpg
import os
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_SYNC_URL"])
```

---

## Variabili d'ambiente per src layout

```bash
# .env (o esportare manualmente)
PYTHONPATH=src                      # necessario SOLO se non usi `pip install -e .`
                                    # Con pip install -e . non serve
```

Raccomandato: usare sempre `pip install -e .` — risolve tutti i path in modo pulito.

---

## Pytest con src layout

```ini
# pyproject.toml — già incluso sopra
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
asyncio_mode = "auto"
```

```bash
pytest tests/ -v                    # pytest legge pyproject.toml, aggiunge src/ al path
```

---

## Convenzioni nome file e modulo

| Tipo | Esempio path | Esempio import |
|------|-------------|----------------|
| Agente | `src/agents/scout/agent.py` | `from agents.scout.agent import ScoutAgent` |
| Modelli dati | `src/agents/models.py` | `from agents.models import AgentTask` |
| ORM model | `src/db/models/deal.py` | `from db.models.deal import Deal` |
| Tool | `src/tools/db_tools.py` | `from tools.db_tools import get_deal` |
| API router | `src/api/routers/deals.py` | `from api.routers import deals` |
| Schema Pydantic | `src/api/schemas/deal.py` | `from api.schemas.deal import DealResponse` |
