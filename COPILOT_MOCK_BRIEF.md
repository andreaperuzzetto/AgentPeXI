# Brief Copilot — Etsy Mock Mode

**Obiettivo**: implementare un mock mode completo che permette di testare la pipeline
Research → Design → Publisher → Analytics senza token Etsy reali e senza Replicate.
Nessun nuovo endpoint backend necessario. Attivazione via comando Telegram `/mock on|off`.

**Verifica obbligatoria finale**: `python3 -m py_compile` su ogni file Python modificato.
Zero errori.

---

## 1. Architettura — cosa cambia e dove

```
apps/backend/
  core/
    pepe.py              ← aggiungere attributo mock_mode + set_mock_mode()
  tools/
    etsy_api.py          ← mock_mode check su ogni metodo pubblico + _mock_* methods
    image_gen.py         ← aggiungere mock_mode_getter param a DesignAgent handshake
  agents/
    design.py            ← passare mock_mode_getter a ImageGenerator
  telegram/
    bot.py               ← aggiungere /mock comando
  api/
    main.py              ← passare mock_mode_getter a DesignAgent, broadcast mock_mode
apps/frontend/src/
  store/index.ts         ← aggiungere mock_mode a systemStatus
  components/Header.tsx  ← badge "MOCK" quando mock_mode attivo
```

**File da NON toccare**: `memory.py`, `scheduler.py`, `publisher.py`, `analytics.py`,
`research.py`, `base.py`, `config.py`, tutto il frontend tranne Header.tsx e store/index.ts.

---

## 2. Pepe — attributo mock_mode

**File**: `apps/backend/core/pepe.py`

Aggiungere nell'`__init__`:
```python
self.mock_mode: bool = False
```

Aggiungere metodo pubblico:
```python
def set_mock_mode(self, value: bool) -> None:
    """Attiva/disattiva mock mode a runtime. Thread-safe (GIL)."""
    self.mock_mode = value
    logger.info("Mock mode: %s", "ON" if value else "OFF")
```

Aggiungere metodo per leggere lo stato (usato dal WS broadcast):
```python
def get_mock_mode(self) -> bool:
    return self.mock_mode
```

Nessun altro cambiamento a Pepe.

---

## 3. EtsyAPI — mock mode completo

**File**: `apps/backend/tools/etsy_api.py`

### 3a. Import aggiuntivi in cima al file
```python
import random
import time as _time
```

### 3b. Proprietà mock_mode nell'__init__
`EtsyAPI.__init__` riceve già `pepe: Any = None` e lo salva come `self._pepe`.
Aggiungere una property (non un attributo — legge sempre da pepe):

```python
@property
def mock_mode(self) -> bool:
    return bool(getattr(self._pepe, 'mock_mode', False))
```

### 3c. Mock methods privati
Aggiungere questi metodi privati alla classe (prima dei metodi pubblici):

```python
# ------------------------------------------------------------------
# Mock implementations — usati quando self.mock_mode is True
# ------------------------------------------------------------------

def _mock_listing_id(self) -> str:
    """Genera listing_id mock univoco."""
    return f"MOCK_{int(_time.time())}_{random.randint(1000, 9999)}"

async def _mock_create_listing(self, title: str, price: float, tags: list[str], **kwargs) -> dict:
    """Simula creazione listing Etsy — salva nel DB locale."""
    listing_id = self._mock_listing_id()
    # Struttura risposta identica a quella reale Etsy v3
    return {
        "listing_id": listing_id,
        "title": title,
        "description": kwargs.get("description", ""),
        "price": {"amount": int(price * 100), "divisor": 100, "currency_code": "EUR"},
        "tags": tags,
        "state": "active",
        "views": 0,
        "num_favorers": 0,
        "quantity": 999,
        "is_digital": True,
        "url": f"https://www.etsy.com/listing/{listing_id}/mock-product",
        "creation_timestamp": int(_time.time()),
        "shop_id": "MOCK_SHOP_001",
    }

async def _mock_upload_file(self, listing_id: int | str, file_path: str, name: str) -> dict:
    """Simula upload file — no-op, ritorna success."""
    return {
        "listing_file_id": f"MOCKFILE_{int(_time.time())}",
        "listing_id": str(listing_id),
        "filename": name,
        "filesize": "1.2 MB",
        "filetype": "application/pdf",
        "create_timestamp": int(_time.time()),
    }

async def _mock_get_listing(self, listing_id: int | str) -> dict:
    """Legge listing dal DB locale (salvato da publisher) + aggiunge drift views."""
    try:
        listings = await self.memory.get_etsy_listings()
        listing = next(
            (l for l in listings if str(l.get("listing_id")) == str(listing_id)),
            None
        )
    except Exception:
        listing = None

    if listing:
        # Simula piccola crescita views ad ogni lettura
        current_views = listing.get("views", 0)
        view_drift = random.randint(0, 15)
        return {
            "listing_id": str(listing_id),
            "title": listing.get("title", "Mock Product"),
            "price": {
                "amount": int(listing.get("price_eur", 4.99) * 100),
                "divisor": 100,
                "currency_code": "EUR",
            },
            "state": listing.get("status", "active"),
            "views": current_views + view_drift,
            "num_favorers": listing.get("favorites", 0) + random.randint(0, 3),
            "shop_id": "MOCK_SHOP_001",
        }

    # Fallback se listing non trovato nel DB
    return {
        "listing_id": str(listing_id),
        "title": "Mock Product",
        "price": {"amount": 499, "divisor": 100, "currency_code": "EUR"},
        "state": "active",
        "views": random.randint(10, 150),
        "num_favorers": random.randint(0, 20),
        "shop_id": "MOCK_SHOP_001",
    }

async def _mock_get_shop_transactions(
    self, shop_id: str | None = None, listing_id: int | None = None
) -> dict:
    """
    Simula transazioni realistiche.
    Distribuzione: 60% → 0 vendite, 25% → 1-2, 10% → 3-5, 5% → 6-10.
    Questo crea naturalmente listing con pattern diversi per testare analytics.
    """
    roll = random.random()
    if roll < 0.60:
        num_sales = 0
    elif roll < 0.85:
        num_sales = random.randint(1, 2)
    elif roll < 0.95:
        num_sales = random.randint(3, 5)
    else:
        num_sales = random.randint(6, 10)

    results = []
    for i in range(num_sales):
        results.append({
            "transaction_id": f"MOCKTX_{int(_time.time())}_{i}",
            "listing_id": str(listing_id) if listing_id else "0",
            "quantity": 1,
            "price": {"amount": 499, "divisor": 100, "currency_code": "EUR"},
            "create_timestamp": int(_time.time()) - random.randint(0, 86400 * 30),
        })

    return {"count": num_sales, "results": results}

async def _mock_get_shop(self, shop_id: str | None = None) -> dict:
    """Shop info mock."""
    return {
        "shop_id": "MOCK_SHOP_001",
        "shop_name": "AgentPeXI Mock Shop",
        "title": "Digital Products by AgentPeXI",
        "listing_active_count": 0,
        "currency_code": "EUR",
        "is_vacation": False,
        "url": "https://www.etsy.com/shop/AgentPeXIMock",
    }

async def _mock_check_auth_status(self) -> dict:
    """Mock auth — sempre autenticato."""
    from datetime import datetime, timezone, timedelta
    return {
        "authenticated": True,
        "expired": False,
        "mock": True,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
    }
```

### 3d. Modificare i metodi pubblici — aggiungere mock check
Modificare ogni metodo pubblico aggiungendo il check in cima, **prima** di qualsiasi altra logica:

```python
async def create_listing(self, title, description, price, tags, taxonomy_id, ...):
    if self.mock_mode:
        return await self._mock_create_listing(title=title, price=price, tags=tags,
                                                description=description)
    # ... codice esistente invariato ...

async def upload_file(self, listing_id, file_path, name):
    if self.mock_mode:
        return await self._mock_upload_file(listing_id, file_path, name)
    # ... codice esistente invariato ...

async def get_listing(self, listing_id):
    if self.mock_mode:
        return await self._mock_get_listing(listing_id)
    # ... codice esistente invariato ...

async def get_shop(self, shop_id=None):
    if self.mock_mode:
        return await self._mock_get_shop(shop_id)
    # ... codice esistente invariato ...

async def get_shop_stats(self, shop_id=None):
    if self.mock_mode:
        return await self._mock_get_shop(shop_id)  # stesso fallback
    # ... codice esistente invariato ...

async def get_shop_transactions(self, shop_id=None, listing_id=None):
    if self.mock_mode:
        return await self._mock_get_shop_transactions(shop_id, listing_id)
    # ... codice esistente invariato ...

async def get_listings(self, shop_id=None, limit=100):
    if self.mock_mode:
        # Legge direttamente dal DB locale invece di Etsy
        listings = await self.memory.get_etsy_listings(status="active")
        return listings[:limit]
    # ... codice esistente invariato ...

async def check_auth_status(self):
    if self.mock_mode:
        return await self._mock_check_auth_status()
    # ... codice esistente invariato ...
```

Metodi `get_messages`, `reply_message`, `update_listing`, `get_shop_receipts`:
aggiungere `if self.mock_mode: return {}` (o lista vuota) — non usati in produzione
mock.

---

## 4. ImageGenerator — mock via mock_mode_getter

**File**: `apps/backend/tools/image_gen.py`

`ImageGenerator` ha già un `_generate_placeholder()` che funziona con Pillow.
Il problema è che viene usato solo quando `REPLICATE_API_TOKEN` è assente.

Modificare `generate_digital_art()` per accettare un parametro opzionale:

```python
async def generate_digital_art(
    self,
    brief: dict,
    output_path: str,
    mock_mode: bool = False,      # ← AGGIUNGERE questo parametro
) -> str:
    # All'inizio del metodo, prima del check is_available():
    if mock_mode:
        logger.info("ImageGenerator: mock mode — usando placeholder Pillow")
        return await self._generate_placeholder(brief, output_path)
    
    # ... resto del codice esistente invariato ...
```

---

## 5. DesignAgent — passare mock_mode

**File**: `apps/backend/agents/design.py`

`DesignAgent.__init__` deve ricevere un riferimento a come leggere il mock_mode.
**Non passare pepe direttamente** — usa una callable leggera:

Modificare `__init__`:
```python
def __init__(
    self,
    *,
    anthropic_client,
    memory,
    storage,
    ws_broadcaster=None,
    get_mock_mode=None,          # ← AGGIUNGERE: Callable[[], bool] | None
):
    super().__init__(...)
    self._get_mock_mode = get_mock_mode or (lambda: False)
```

Poi, dove `DesignAgent` chiama `image_gen.generate_digital_art(...)`, aggiungere:
```python
result = await image_gen.generate_digital_art(
    brief=brief,
    output_path=output_path,
    mock_mode=self._get_mock_mode(),   # ← AGGIUNGERE
)
```

Trovare la chiamata a `generate_digital_art` cercando quella stringa nel file.

---

## 6. main.py — collegare tutto

**File**: `apps/backend/api/main.py`

### 6a. Passare get_mock_mode a DesignAgent
Nella sezione lifespan dove si crea `design_agent`:
```python
design_agent = DesignAgent(
    anthropic_client=pepe.client,
    memory=memory,
    storage=storage,
    ws_broadcaster=ws_manager.broadcast,
    get_mock_mode=pepe.get_mock_mode,    # ← AGGIUNGERE
)
```

### 6b. Endpoint GET /api/mock/status
```python
@app.get("/api/mock/status")
async def get_mock_status() -> dict:
    """Stato corrente del mock mode."""
    return {"mock_mode": pepe.mock_mode if pepe else False}
```

### 6c. WS broadcast mock_mode nel system_status
Nella funzione `get_status()` (già esistente):
```python
@app.get("/api/status")
async def get_status() -> dict:
    return {
        "status": "running",
        "timestamp": datetime.utcnow().isoformat(),
        "agents": agent_statuses,
        "queue_size": pepe._queue.qsize() if pepe else 0,
        "connected_clients": len(ws_manager._connections),
        "mock_mode": pepe.mock_mode if pepe else False,  # ← AGGIUNGERE
    }
```

---

## 7. Telegram bot — comando /mock

**File**: `apps/backend/telegram/bot.py`

### 7a. Registrare il comando in _register_handlers
```python
add(CommandHandler("mock", self._cmd_mock, filters=self._chat_filter))
```
Aggiungere PRIMA dell'handler testo generico (MessageHandler).

### 7b. Implementare _cmd_mock
```python
async def _cmd_mock(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/mock [on|off] — attiva o disattiva mock mode Etsy."""
    args = context.args or []
    arg = args[0].lower() if args else ""

    if arg == "on":
        self.pepe.set_mock_mode(True)
        # Broadcast WS ai client frontend
        if self.pepe._ws_broadcaster:
            await self.pepe._ws_broadcaster({
                "type": "system_status",
                "mock_mode": True,
                "message": "Mock mode attivato",
            })
        await update.message.reply_text(
            "🟡 *MOCK MODE ATTIVO*\n\n"
            "Etsy API e Replicate sono simulati.\n"
            "I listing vengono salvati nel DB locale.\n"
            "Usa /ask per avviare una pipeline di test.",
            parse_mode="Markdown",
        )

    elif arg == "off":
        self.pepe.set_mock_mode(False)
        if self.pepe._ws_broadcaster:
            await self.pepe._ws_broadcaster({
                "type": "system_status",
                "mock_mode": False,
                "message": "Mock mode disattivato",
            })
        await update.message.reply_text(
            "✅ *Mock mode disattivato*\n\n"
            "Il sistema tornerà a usare Etsy API reale "
            "non appena i token saranno disponibili.",
            parse_mode="Markdown",
        )

    else:
        # Mostra stato corrente
        status = "🟡 ATTIVO" if self.pepe.mock_mode else "⚫ INATTIVO"
        await update.message.reply_text(
            f"*Mock Mode*: {status}\n\n"
            "Uso: `/mock on` oppure `/mock off`",
            parse_mode="Markdown",
        )
```

### 7c. Mostrare mock_mode in /status
Nel metodo `_cmd_status`, aggiungere alla stringa di risposta:
```python
mock_line = "\n🟡 *MOCK MODE ATTIVO*" if self.pepe.mock_mode else ""
# Aggiungere mock_line in fondo al messaggio di status
```

---

## 8. Frontend — badge MOCK nell'header

### 8a. store/index.ts
Nel tipo `SystemStatus` (o dove è definito `systemStatus`), aggiungere:
```typescript
mock_mode?: boolean
```
Nel reducer/handler che processa eventi WS `system_status`, assicurarsi che
`mock_mode` venga salvato nello store.

Nel `useWebSocket.ts` (NON modificare), verificare che l'handler `system_status`
già faccia: `set({ systemStatus: { ...s.systemStatus, ...data } })`.
Se sì, nessuna modifica necessaria — `mock_mode` si propaga automaticamente.

### 8b. Header.tsx
Aggiungere il badge MOCK accanto allo stato di connessione:

```tsx
const mockMode = useStore((s) => s.systemStatus?.mock_mode)

// Nel render, vicino al dot di connessione:
{mockMode && (
  <span style={{
    fontFamily: 'var(--fd)',
    fontSize: 9,
    letterSpacing: '0.08em',
    color: 'var(--warn)',
    border: '1px solid rgba(240,180,41,.35)',
    borderRadius: 4,
    padding: '2px 8px',
    animation: 'pulse 2.4s var(--e-io) infinite',
  }}>
    MOCK
  </span>
)}
```

---

## 9. Checklist finale per Copilot

- [ ] `python3 -m py_compile apps/backend/core/pepe.py` → OK
- [ ] `python3 -m py_compile apps/backend/tools/etsy_api.py` → OK
- [ ] `python3 -m py_compile apps/backend/tools/image_gen.py` → OK
- [ ] `python3 -m py_compile apps/backend/agents/design.py` → OK
- [ ] `python3 -m py_compile apps/backend/telegram/bot.py` → OK
- [ ] `python3 -m py_compile apps/backend/api/main.py` → OK
- [ ] `cd apps/frontend && node_modules/.bin/tsc --noEmit` → 0 errori
- [ ] `pepe.mock_mode` attributo presente
- [ ] `etsy_api.mock_mode` property legge da pepe
- [ ] Tutti i metodi pubblici EtsyAPI hanno `if self.mock_mode:` check
- [ ] `_mock_get_shop_transactions()` ha la distribuzione 60/25/10/5%
- [ ] `generate_digital_art()` accetta `mock_mode: bool = False`
- [ ] `DesignAgent.__init__` accetta `get_mock_mode` callable
- [ ] `/mock on`, `/mock off`, `/mock` (status) funzionano in bot.py
- [ ] WS broadcast `{"type":"system_status","mock_mode":true}` su toggle
- [ ] Badge MOCK in Header.tsx legge da store
