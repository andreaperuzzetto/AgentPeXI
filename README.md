# AgentPeXI

Sistema multi-agente per opportunità geolocalizzate (Italia).  
Servizi: consulenza, web design, manutenzione digitale.

---

## Prerequisiti

- **Python 3.12** (con `python3.12 -m venv` disponibile)
- **Node.js ≥ 20** + **pnpm** (`npm i -g pnpm`)
- **Docker Desktop** (o Docker Engine + Compose v2)
- **macOS**: se Homebrew PostgreSQL è attivo sulla porta 5432, fermarlo prima di avviare Docker:
  ```bash
  brew services stop postgresql@18   # adattare alla versione installata
  ```

---

## Setup iniziale (una volta sola)

### 1 — Variabili d'ambiente

```bash
cp .env.example .env
```

Compilare almeno:

| Variabile | Valore dev |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://agentpexi:changeme@localhost:5432/agentpexi` |
| `DATABASE_SYNC_URL` | `postgresql://agentpexi:changeme@localhost:5432/agentpexi` |
| `REDIS_URL` | `redis://localhost:6379/0` |
| `MINIO_ENDPOINT` | `localhost:9000` |
| `MINIO_ACCESS_KEY` | `minioadmin` |
| `MINIO_SECRET_KEY` | `minioadmin` |
| `MINIO_BUCKET` | `agentpexi` |
| `SECRET_KEY` | stringa random ≥ 32 caratteri |
| `PORTAL_SECRET_KEY` | stringa random ≥ 32 caratteri (diversa da `SECRET_KEY`) |
| `OPERATOR_PASSWORD_HASH` | hash bcrypt (vedi sotto) |
| `OPERATOR_EMAIL` | email operatore |
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `GOOGLE_MAPS_API_KEY` | `AIza...` |
| `CLIENT_WORKSPACE_ROOT` | percorso locale es. `/tmp/agentpexi/clients` |

**Generare hash password operatore:**
```bash
python -c "from passlib.context import CryptContext; print(CryptContext(['bcrypt']).hash('tua_password'))"
```

### 2 — Ambiente Python

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
pip install -e .
```

### 3 — Dipendenze Node (frontend e script Puppeteer)

```bash
cd frontend && pnpm install && cd ..
cd scripts && npm install && cd ..
```

### 4 — Avviare i servizi Docker

```bash
docker compose up -d
```

Attende che PostgreSQL, Redis e MinIO siano `healthy` (di solito ≤ 30 secondi).

### 5 — Migrazioni database

```bash
source .venv/bin/activate
cd backend
DATABASE_SYNC_URL=postgresql://agentpexi:changeme@127.0.0.1:5432/agentpexi \
  alembic upgrade head
cd ..
```

> Il prefisso `DATABASE_SYNC_URL=...` è necessario solo se il file `.env` non
> viene caricato automaticamente dalla shell. Con `direnv` o `dotenv` attivi
> non serve.

---

## Avvio per lo sviluppo

Aprire **quattro terminali** separati dalla root del progetto.

### Terminale 1 — API FastAPI

```bash
source .venv/bin/activate
cd backend/src
uvicorn api.main:app --reload --port 8000
```

Disponibile su: <http://localhost:8000>  
Documentazione interattiva: <http://localhost:8000/docs>

### Terminale 2 — Celery worker

```bash
source .venv/bin/activate
cd backend/src
celery -A agents.worker worker --loglevel=info --concurrency=4
```

### Terminale 3 — Celery beat (scheduler periodici)

```bash
source .venv/bin/activate
cd backend/src
celery -A agents.worker beat --loglevel=info
```

I job periodici configurati:

| Job | Intervallo | Descrizione |
|---|---|---|
| `agents.gate_poller` | 30 s | Controlla flag approvazione nei deal |
| `agents.gmail_poller` | 5 min | Rileva nuove email di supporto |

### Terminale 4 — Frontend Next.js

```bash
cd frontend
pnpm dev
```

Disponibile su: <http://localhost:3000>

---

## Porte in uso

| Servizio | Porta |
|---|---|
| FastAPI | 8000 |
| Frontend | 3000 |
| PostgreSQL | 5432 |
| Redis | 6379 |
| MinIO API | 9000 |
| MinIO Console | 9001 |

---

## Comandi utili

```bash
# Linting e formattazione
ruff check . --fix && black .

# Test
pytest tests/ -v

# Test con copertura
pytest tests/ --cov

# Nuova migrazione Alembic (dopo modifiche ai modelli ORM)
cd backend
DATABASE_SYNC_URL=postgresql://agentpexi:changeme@127.0.0.1:5432/agentpexi \
  alembic revision --autogenerate -m "descrizione"
DATABASE_SYNC_URL=postgresql://agentpexi:changeme@127.0.0.1:5432/agentpexi \
  alembic upgrade head
cd ..

# Fermare e rimuovere i container Docker (i volumi persistono)
docker compose down

# Fermare e cancellare anche i volumi (DISTRUTTIVO — azzera DB e MinIO)
docker compose down -v
```

---

## Note macOS

- Il volume `/Volumes/Progetti` (ExFAT/HFS+) genera file `._*` automaticamente.
  Un hook `pre-commit` li elimina ad ogni commit.
  Per disabilitare l'indicizzazione Spotlight sul volume:
  ```bash
  sudo mdutil -i off /Volumes/Progetti
  ```
- Se la porta 5432 è occupata da Homebrew PostgreSQL:
  ```bash
  brew services stop postgresql@18
  ```
