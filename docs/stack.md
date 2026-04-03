# Stack tecnico

## Servizi

| Layer | Tecnologia | Versione |
|-------|-----------|---------|
| Orchestrazione | LangGraph + Anthropic API | 0.2 |
| LangGraph checkpointer | PostgreSQL (`langgraph-checkpoint-postgres`) | 2.x |
| Backend API | FastAPI + asyncio | 0.115 |
| Frontend | Next.js App Router + Tailwind | 14 |
| Database | PostgreSQL + pgvector | 16 |
| Cache + queue | Redis + Celery | 7 |
| Object storage | MinIO (S3-compat) | locale |
| Render artefatti | Puppeteer headless (`scripts/render.js`) | 22 |
| PDF | WeasyPrint + Jinja2 | 62 |
| Email | Gmail API — MCP server custom stdio (`backend/src/mcp_servers/gmail/server.py`) | — |
| Mappe | Google Maps Places + Geocoding | — |
| Runtime | Python 3.12, Node.js 20 | — |
| Container | Docker Compose | solo dev |

> **Nota:** Puppeteer è usato per renderizzare artefatti visivi (mockup web design,
> presentazioni consulenza, schemi di processo) — non solo mockup software.

## Modelli Anthropic per agente

| Agente | Modello | Perché |
|--------|---------|--------|
| Orchestrator | `claude-opus-4-6` | Pianificazione, routing complesso |
| Delivery Orchestrator | `claude-opus-4-6` | Decomposizione task di erogazione |
| Tutti gli altri | `claude-sonnet-4-6` | Velocità + costo su task specializzati |

**Max tokens per chiamata:** 8192. Non superare senza motivazione esplicita nel codice.

## Porte locali (dev)

| Servizio | Porta |
|---------|-------|
| FastAPI | 8000 |
| Next.js | 3000 |
| PostgreSQL | 5432 |
| Redis | 6379 |
| MinIO API | 9000 |
| MinIO UI | 9001 |
