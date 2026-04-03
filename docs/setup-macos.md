# Setup locale — Mac Mini M4

Istruzioni per avere l'ambiente di sviluppo completamente funzionante su macOS ARM (Apple Silicon).
Seguire nell'ordine — le dipendenze di sistema devono precedere quelle Python/Node.

---

## Prerequisiti di sistema

### Homebrew

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Dopo l'installazione, aggiungere brew al PATH (l'installer lo dice):
```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

---

### Dipendenze di sistema

```bash
# Librerie per WeasyPrint (PDF)
brew install cairo pango gdk-pixbuf libffi

# Librerie per Puppeteer (rendering mockup)
brew install chromium

# Strumenti sviluppo
brew install git postgresql-client redis
```

> **Importante — Chromium su ARM:** non usare il Chromium scaricato da Puppeteer.
> Su Apple Silicon scarica una build x86 emulata che è lenta e spesso crashata.
> Usare quello installato da brew (nativo ARM).

---

### Python 3.12

```bash
brew install python@3.12
```

Verifica:
```bash
python3.12 --version   # Python 3.12.x
```

Crea virtualenv nella root del progetto:
```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

Installa dipendenze Python:
```bash
pip install -r requirements.txt
```

---

### Node.js 20

```bash
brew install node@20
echo 'export PATH="/opt/homebrew/opt/node@20/bin:$PATH"' >> ~/.zprofile
source ~/.zprofile
node --version   # v20.x.x
npm --version
```

Installa dipendenze frontend:
```bash
cd frontend && npm install
```

---

### Docker Desktop

Scarica da [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/).
Scegli la versione **Apple Silicon**.

Dopo l'installazione, avvia Docker Desktop e abilita il supporto Rosetta
(Impostazioni → General → "Use Rosetta for x86/amd64 emulation") — non serve per questo progetto ma evita problemi con immagini di terze parti.

Avvia i servizi:
```bash
docker-compose up -d
```

Verifica:
```bash
docker-compose ps   # tutti i servizi "Up"
```

---

## Configurazione Puppeteer per ARM

Puppeteer va configurato per usare il Chromium di brew invece di quello bundled.
Crea il file `.puppeteerrc.cjs` nella root del progetto:

```js
const { join } = require("path")

module.exports = {
  executablePath: "/opt/homebrew/bin/chromium",
  headless: "new",
  args: [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
  ],
}
```

Installa il pacchetto senza scaricare Chromium bundled:
```bash
PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true npm install puppeteer
```

Test rapido:
```bash
node -e "
const puppeteer = require('puppeteer');
puppeteer.launch().then(b => { console.log('Puppeteer OK'); b.close(); });
"
```

---

## Configurazione WeasyPrint per macOS

WeasyPrint richiede che le librerie brew siano trovate da Python.
Su Apple Silicon i path non sono standard — aggiungere al virtualenv:

```bash
# Aggiungere a .venv/bin/activate (o all'env del progetto)
export DYLD_LIBRARY_PATH="/opt/homebrew/lib:$DYLD_LIBRARY_PATH"
export PKG_CONFIG_PATH="/opt/homebrew/lib/pkgconfig:$PKG_CONFIG_PATH"
```

Oppure, più pulito, creare `.env.local` (non committare):
```bash
DYLD_LIBRARY_PATH=/opt/homebrew/lib
```

Test WeasyPrint:
```bash
python -c "from weasyprint import HTML; HTML(string='<h1>OK</h1>').write_pdf('/tmp/test.pdf'); print('WeasyPrint OK')"
```

---

## Celery + async su macOS

Celery non supporta `async def` task nativamente. Pattern adottato in AgentPeXI:

```python
# agents/{nome}/worker.py — ogni agente ha il proprio worker sync
import asyncio
from celery import Celery

app = Celery("agentpexi", broker=REDIS_URL)

@app.task(name="agents.scout.run", max_retries=3)
def run_scout(task_dict: dict) -> dict:
    # Wrapper sync → async
    return asyncio.run(_run_async(task_dict))

async def _run_async(task_dict: dict) -> dict:
    agent = ScoutAgent()
    task = AgentTask(**task_dict)
    result = await agent.run(task)
    return result.model_dump()
```

> **Non usare** `celery[gevent]` o `celery[eventlet]` — hanno problemi su macOS ARM.
> `asyncio.run()` è la soluzione corretta e stabile.

Avvio worker Celery in sviluppo:
```bash
celery -A agents.worker worker --loglevel=info --concurrency=4
```

---

## Variabili d'ambiente

```bash
cp .env.example .env
```

Compilare almeno:
- `ANTHROPIC_API_KEY`
- `GOOGLE_MAPS_API_KEY`
- `SECRET_KEY` (qualsiasi stringa random lunga)
- `PORTAL_SECRET_KEY` (diverso dal precedente)
- `OPERATOR_EMAIL`
- `BASE_URL=http://localhost:3000`

---

## Setup database

```bash
# Con docker-compose attivo
alembic upgrade head

# Verifica
psql postgresql://agentpexi:agentpexi@localhost:5432/agentpexi -c "\dt"
```

---

## Avvio completo sviluppo

```bash
# Terminale 1 — Servizi
docker-compose up -d

# Terminale 2 — Backend API
source .venv/bin/activate
uvicorn api.main:app --reload --port 8000

# Terminale 3 — Celery worker
source .venv/bin/activate
celery -A agents.worker worker --loglevel=info --concurrency=4

# Terminale 4 — Orchestrator LangGraph
source .venv/bin/activate
python -m orchestrator.graph --dev

# Terminale 5 — Frontend Next.js
cd frontend && npm run dev
```

---

## Verifica installazione

```bash
# Python e dipendenze
python -c "import fastapi, langchain, langgraph, celery, structlog, alembic; print('Python deps OK')"

# WeasyPrint
python -c "from weasyprint import HTML; print('WeasyPrint OK')"

# Puppeteer
node -e "require('puppeteer').launch().then(b => { console.log('Puppeteer OK'); b.close() })"

# Docker
docker-compose ps

# Database
alembic current

# API
curl http://localhost:8000/health
```

---

## Problemi comuni su M4

| Problema | Causa | Soluzione |
|---------|-------|-----------|
| `cairo` non trovato da WeasyPrint | Path brew non nel DYLD | Aggiungere `DYLD_LIBRARY_PATH=/opt/homebrew/lib` |
| Puppeteer crash all'avvio | Chromium bundled x86 | Usare Chromium brew + `.puppeteerrc.cjs` |
| Celery task mai eseguiti | `async def` non supportato | Usare `asyncio.run()` nel task sync |
| `psycopg2` build fallita | Mancano header libpq | `brew install libpq` poi reinstallare |
| `uvloop` warning su macOS | macOS non supporta uvloop pienamente | Ignorabile in dev, usa `asyncio` default |
