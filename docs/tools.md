# Tools — Firme e contratti

Ogni tool è un modulo Python in `tools/`. Gli agenti **non** chiamano mai
API esterne o DB direttamente: usano solo questi wrapper.

---

## `tools/db_tools.py`

Accesso asincrono al database (SQLAlchemy async). Ogni funzione accetta
una `AsyncSession` già aperta — la gestione delle transazioni è a carico
dell'agente chiamante.

```python
from tools.db_tools import (
    get_lead, update_lead, create_lead,
    get_deal, update_deal,
    get_client, create_client,
    get_proposal, create_proposal, update_proposal,
    create_task, update_task, get_task_by_idempotency_key,
    create_service_delivery, update_service_delivery, get_service_deliveries_for_deal,
    create_delivery_report,
    create_invoice, update_invoice,
    create_ticket, update_ticket,
    create_nps_record,
    log_email,
)
```

### Leads

```python
async def get_lead(lead_id: UUID, db: AsyncSession) -> Lead | None
async def get_lead_by_place_id(google_place_id: str, db: AsyncSession) -> Lead | None
async def create_lead(data: dict, db: AsyncSession) -> Lead
    # data: tutti i campi della tabella leads
    # Eccezione: LeadAlreadyExistsError se google_place_id duplicato
async def update_lead(lead_id: UUID, data: dict, db: AsyncSession) -> Lead
    # data: dict parziale — aggiorna solo i campi presenti
    # Imposta automaticamente updated_at = now()
```

### Deals

```python
async def get_deal(deal_id: UUID, db: AsyncSession) -> Deal | None
    # IMPORTANTE: leggere SEMPRE da DB, mai usare oggetto in cache per gate flags
async def update_deal(deal_id: UUID, data: dict, db: AsyncSession) -> Deal
async def create_deal(lead_id: UUID, service_type: str, db: AsyncSession) -> Deal
```

### Clients

```python
async def get_client(client_id: UUID, db: AsyncSession) -> Client | None
async def create_client(lead_id: UUID, deal_id: UUID, db: AsyncSession) -> Client
    # Crea client + schema PostgreSQL dedicato + workspace locale
    # Chiama internamente: create_client_schema(), init_client_workspace()
async def create_client_schema(client_id: UUID, db: AsyncSession) -> str
    # Restituisce nome schema creato: "client_{id_senza_trattini}"
```

### Proposals

```python
async def get_proposal(proposal_id: UUID, db: AsyncSession) -> Proposal | None
async def get_latest_proposal(deal_id: UUID, db: AsyncSession) -> Proposal | None
async def create_proposal(deal_id: UUID, data: dict, db: AsyncSession) -> Proposal
    # data: pdf_path, page_count, gap_summary, solution_summary, service_type,
    #       deliverables_json, pricing_json, timeline_weeks, roi_summary, artifact_paths
    # Auto-incrementa version (MAX version + 1)
    # Eccezione: MaxProposalVersionsError se version > 5
async def update_proposal(proposal_id: UUID, data: dict, db: AsyncSession) -> Proposal
```

### Tasks

```python
async def create_task(
    type: str,
    agent: str,
    payload: dict,
    db: AsyncSession,
    deal_id: UUID | None = None,
    client_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> Task

async def update_task(task_id: UUID, data: dict, db: AsyncSession) -> Task
    # data può includere: status, output, error, blocked_reason,
    #                     started_at, completed_at

async def get_task_by_idempotency_key(key: str, db: AsyncSession) -> Task | None
    # Restituisce None se non esiste. Usato per controllo idempotenza.
    # Se il task esiste ed è completed → l'operazione è già avvenuta.
```

### Service Deliveries

```python
async def create_service_delivery(deal_id: UUID, client_id: UUID, data: dict, db: AsyncSession) -> ServiceDelivery
    # data: service_type, type, title, description, milestone_name,
    #       milestone_due, depends_on (list[UUID])

async def update_service_delivery(sd_id: UUID, data: dict, db: AsyncSession) -> ServiceDelivery
    # Per aggiornare: status, artifact_paths, rejection_notes, rejection_count,
    #                 operator_approved, operator_notes, completed_at

async def get_service_deliveries_for_deal(deal_id: UUID, db: AsyncSession) -> list[ServiceDelivery]
async def get_service_delivery(sd_id: UUID, db: AsyncSession) -> ServiceDelivery | None
```

### Delivery Reports

```python
async def create_delivery_report(
    service_delivery_id: UUID,
    client_id: UUID,
    approved: bool,
    completeness_pct: float,
    blocking_issues: list[str],
    notes: list[str],
    report_path: str,
    db: AsyncSession,
) -> DeliveryReport
```

### Invoices

```python
async def create_invoice(deal_id: UUID, client_id: UUID, data: dict, db: AsyncSession) -> Invoice
    # data: milestone, amount_cents, due_date, tax_rate_pct (default 22.00)
    # Auto-genera invoice_number: "{YYYY}-{NNN}" (progressivo annuale)

async def update_invoice(invoice_id: UUID, data: dict, db: AsyncSession) -> Invoice
    # Per: status, paid_at, payment_method, billing_dispute, billing_dispute_notes
```

### Tickets

```python
async def create_ticket(client_id: UUID, data: dict, db: AsyncSession) -> Ticket
async def update_ticket(ticket_id: UUID, data: dict, db: AsyncSession) -> Ticket
async def get_ticket(ticket_id: UUID, db: AsyncSession) -> Ticket | None
```

### NPS Records

```python
async def create_nps_record(client_id: UUID, deal_id: UUID, trigger: str, db: AsyncSession) -> NpsRecord
async def update_nps_record(nps_id: UUID, score: int, comment: str, db: AsyncSession) -> NpsRecord
```

### Email Log

```python
async def log_email(
    agent: str,
    direction: str,   # "outbound" | "inbound"
    template_name: str | None,
    gmail_message_id: str | None,
    gmail_thread_id: str | None,
    subject: str | None,
    db: AsyncSession,
    deal_id: UUID | None = None,
    client_id: UUID | None = None,
    task_id: UUID | None = None,
) -> EmailLog
```

---

## `tools/file_store.py`

Interazione con MinIO (compatibile S3). Usa `aiobotocore` o `miniopy-async`.

```python
from tools.file_store import upload_file, download_file, get_presigned_url, file_exists, list_files
```

```python
async def upload_file(
    local_path: str | Path,
    object_key: str,            # es. "clients/{deal_id}/proposals/v1.pdf"
) -> str
    # Restituisce l'object_key caricato.
    # Bucket letto da env MINIO_BUCKET.
    # Eccezione: FileUploadError in caso di errore MinIO.

async def upload_bytes(
    data: bytes,
    object_key: str,
    content_type: str = "application/octet-stream",
) -> str

async def download_file(
    object_key: str,
    local_path: str | Path,
) -> Path
    # Scarica il file in local_path. Crea le directory intermedie.
    # Eccezione: FileNotFoundError se object_key non esiste.

async def download_bytes(object_key: str) -> bytes
    # Restituisce il contenuto grezzo del file.

async def get_presigned_url(
    object_key: str,
    expires_in_seconds: int = 3600,
) -> str
    # Genera URL pre-firmato per accesso diretto temporaneo.

async def file_exists(object_key: str) -> bool

async def list_files(prefix: str) -> list[str]
    # Elenca tutti gli object_key con il prefix dato.
    # es. list_files("clients/550e.../artifacts/") → ["...", ...]
```

---

## `tools/google_maps.py`

Wrapper rate-limited (token bucket 100 req/s) per Google Maps Places API (New).

```python
from tools.google_maps import search_businesses, get_place_details, geocode_address
```

```python
async def search_businesses(
    query: str,             # es. "ristoranti Treviso"
    location: str,          # es. "Treviso, Italia"
    radius_km: int = 10,
    max_results: int = 20,
) -> list[dict]
    # Restituisce lista di place dict con campi:
    # {google_place_id, business_name, address, city, region, country,
    #  latitude, longitude, google_rating, google_review_count,
    #  google_category, website_url, phone}
    # Eccezione: MapsAPIError se quota esaurita o errore API.

async def get_place_details(google_place_id: str) -> dict
    # Restituisce dettagli aggiornati del place (stesso schema di search_businesses)
    # più: opening_hours, types completi.

async def geocode_address(address: str) -> tuple[float, float] | None
    # Restituisce (latitude, longitude) o None se non trovato.
```

---

## `tools/gmail.py`

Wrapper Gmail API via OAuth2. Usa `google-auth` + `googleapiclient`.

```python
from tools.gmail import send_email, reply_to_thread, get_thread, list_unread
```

```python
async def send_email(
    to_address: str,
    subject: str,
    body_html: str,
    body_text: str | None = None,
) -> dict
    # Restituisce: {"message_id": str, "thread_id": str}
    # Mittente: GMAIL_SENDER_ADDRESS da env.
    # PII: to_address non viene loggata — solo message_id.
    # Eccezione: GmailSendError.

async def reply_to_thread(
    thread_id: str,
    subject: str,
    body_html: str,
    body_text: str | None = None,
) -> dict
    # Risponde al thread esistente. Stesso schema di ritorno.

async def get_thread(thread_id: str) -> dict
    # Restituisce thread con messaggi. Campi:
    # {"thread_id": str, "messages": [{"message_id", "from", "date", "snippet", "body_text"}]}
    # "from" non loggarlo se contiene email — usare solo per elaborazione interna.

async def list_unread(max_results: int = 50) -> list[dict]
    # Lista email non lette in inbox. Ogni item:
    # {"message_id", "thread_id", "subject", "from", "date", "snippet"}
    # Usato dal Support Agent per rilevare nuovi ticket.
```

---

## `tools/pdf_generator.py`

Generatore PDF da template Jinja2 + WeasyPrint.

```python
from tools.pdf_generator import render_pdf
```

```python
async def render_pdf(
    template_path: str,         # path assoluto al file .html Jinja2
    context: dict,              # variabili da iniettare nel template
    output_path: str,           # path locale dove salvare il PDF
    base_url: str | None = None # per risolvere risorse relative (CSS, immagini)
) -> str
    # Restituisce output_path.
    # Eccezione: PDFGenerationError in caso di errore WeasyPrint.
    # Il file viene creato in output_path. Directory deve esistere.

async def render_pdf_to_bytes(
    template_path: str,
    context: dict,
    base_url: str | None = None,
) -> bytes
    # Come render_pdf ma restituisce bytes invece di scrivere su disco.
```

---

## `tools/mockup_renderer.py`

Renderer HTML → immagini/PDF via Puppeteer (Node.js child process).

```python
from tools.mockup_renderer import render_to_png, render_to_pdf
```

```python
async def render_to_png(
    html_path: str,             # path assoluto al file HTML
    output_path: str,           # path locale .png
    viewport_width: int = 1440,
    viewport_height: int = 900,
    device_scale_factor: float = 2.0,
) -> str
    # Restituisce output_path. Timeout: 60s.
    # Eccezione: RenderTimeoutError, RenderError.

async def render_to_pdf(
    html_path: str,
    output_path: str,           # path locale .pdf
    format: str = "A4",
    margin: dict | None = None, # default: tutti 0
    print_background: bool = True,
) -> str
    # Restituisce output_path.

# Costanti viewport standard
VIEWPORT_DESKTOP = {"width": 1440, "height": 900}
VIEWPORT_MOBILE  = {"width": 390,  "height": 844}
```

---

## Errori comuni

| Eccezione | Modulo | Quando |
|-----------|--------|--------|
| `LeadAlreadyExistsError` | db_tools | Insert lead con google_place_id duplicato |
| `MaxProposalVersionsError` | db_tools | Tentativo versione > 5 |
| `GateNotApprovedError` | db_tools (+ agenti) | Gate flag False nel deal |
| `FileUploadError` | file_store | Errore MinIO upload |
| `FileNotFoundError` | file_store | object_key inesistente |
| `MapsAPIError` | google_maps | Errore API o quota esaurita |
| `GmailSendError` | gmail | Errore invio email |
| `PDFGenerationError` | pdf_generator | Errore WeasyPrint |
| `RenderTimeoutError` | mockup_renderer | Puppeteer > 60s |
| `RenderError` | mockup_renderer | Errore generico Puppeteer |

Tutti queste eccezioni estendono `AgentToolError(Exception)` definita in `tools/__init__.py`.
Gli agenti devono catturare `AgentToolError` per gestire i fallback.
