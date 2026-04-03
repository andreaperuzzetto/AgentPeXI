# Stack tecnico

## Servizi

| Layer | Tecnologia | Versione |
|-------|-----------|---------|
| Orchestrazione | LangGraph + Anthropic API | 0.2 |
| Backend API | FastAPI + asyncio | 0.115 |
| Frontend | Next.js App Router + Tailwind | 14 |
| Database | PostgreSQL + pgvector | 16 |
| Cache + queue | Redis + Celery | 7 |
| Object storage | MinIO (S3-compat) | locale |
| Mockup render | Puppeteer headless | 22 |
| PDF | WeasyPrint + Jinja2 | 62 |
| Email | Gmail API via MCP | — |
| Mappe | Google Maps Places + Geocoding | — |
| Runtime | Python 3.12, Node.js 20 | — |
| Container | Docker Compose | solo dev |

## Modelli Anthropic per agente

| Agente | Modello | Perché |
|--------|---------|--------|
| Orchestrator | `claude-opus-4-6` | Pianificazione, routing complesso |
| Dev Orchestrator | `claude-opus-4-6` | Decomposizione task di sviluppo |
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
