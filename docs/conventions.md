# Convenzioni di codice

## Python

- Python 3.12, type hints su ogni firma
- `ruff` (linting) + `black` (formatting, line-length 100)
- Async/await per qualsiasi I/O
- `structlog` per logging — mai `print`, mai `logging` direttamente

```python
import structlog
log = structlog.get_logger()

# Campi obbligatori in ogni log entry
log.info("task.started", task_id=str(task.id), agent=task.agent)
log.error("task.failed", task_id=str(task.id), error=str(e))

# PII: solo ID in log, mai email/nome/P.IVA
log.info("client.contacted", client_id=str(client.id))  # OK
log.info("email.sent", to="user@example.com")            # VIETATO
```

## TypeScript / Next.js

- App Router, Server Components per fetch, Client Components per interattività
- Tailwind CSS, classe `dark` su `<html>` come default
- JetBrains Mono per dati operativi (ID, timestamp, status badge)
- `components/ui/` sono puri: zero fetch, zero side effect
- SWR per polling real-time (stato pipeline, task in-flight)
- Zero `any` in TypeScript

## Database

- Migrazioni solo via Alembic — mai DDL diretto
- Ogni tabella: `id UUID PK`, `created_at`, `updated_at`, `deleted_at`
- Indici obbligatori su FK, `status`, `deal_id`, `client_id`
- Transazioni esplicite per operazioni multi-step
- Nessun `DELETE` — solo `UPDATE deleted_at = now()`

## Git

- Branch: `feat/`, `fix/`, `agent/`, `infra/`
- Code Agent: `client/{client_id}/feat/{slug}` — mai su `main`
- Conventional commits: `feat:`, `fix:`, `chore:`, `test:`, `docs:`
- PR + review QA Agent obbligatoria prima del merge
