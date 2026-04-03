# API REST — Contratto completo

FastAPI 0.115. Base URL: `http://localhost:8000` (dev).
Autenticazione: Bearer JWT firmato con `SECRET_KEY` (env).
Tutte le risposte in JSON. Errori seguono lo schema standard.

---

## Schema errore standard

```json
{
  "error": "deal_not_found",
  "message": "Deal 550e8400 non trovato",
  "detail": {}
}
```

HTTP status codes usati: `200`, `201`, `400`, `401`, `403`, `404`, `409`, `422`, `500`.

---

## Auth

**Credenziali operatore:** `OPERATOR_EMAIL` e `OPERATOR_PASSWORD_HASH` (bcrypt) da `.env`.
Non esiste una tabella `users` — un solo operatore, credenziali in env.

```
POST /auth/token
```

**Request:**
```json
{ "email": "andrea@example.com", "password": "..." }
```

**Response 200:**
```json
{ "access_token": "eyJ...", "token_type": "bearer", "expires_in": 86400 }
```

Tutti gli altri endpoint richiedono `Authorization: Bearer {token}`.

---

## Health

```
GET /health
```
**Response 200:** `{ "status": "ok", "version": "0.1.0" }`

Nessuna autenticazione richiesta. Usato da Docker health check.

---

## Orchestrator — `/runs`

### Lista runs attivi

```
GET /runs?status=awaiting_gate&deal_id={deal_id}&page=1&per_page=20
```

Query params opzionali: `status`, `deal_id`, `page`, `per_page`.

**Response 200:**
```json
{
  "items": [
    {
      "run_id": "uuid",
      "deal_id": "uuid",
      "status": "awaiting_gate",
      "gate_type": "proposal_review",
      "awaiting_gate_since": "2025-01-01T11:00:00Z",
      "current_phase": "proposal",
      "current_agent": "proposal",
      "started_at": "2025-01-01T10:00:00Z"
    }
  ],
  "total": 3,
  "page": 1,
  "per_page": 20
}
```

---

### Avvia un nuovo run

```
POST /runs
```

**Request:**
```json
{
  "type": "discovery",
  "payload": {
    "zone": "Treviso, Italia",
    "sector": "horeca",
    "radius_km": 10,
    "max_results": 20
  }
}
```

`type` può essere: `"discovery"` | `"proposal"` | `"delivery"` | `"post_sale"`.

**Response 201:**
```json
{
  "run_id": "uuid",
  "status": "started",
  "created_at": "2025-01-01T10:00:00Z"
}
```

---

### Stato di un run

```
GET /runs/{run_id}
```

**Response 200:**
```json
{
  "run_id": "uuid",
  "status": "running",
  "current_phase": "discovery",
  "current_agent": "scout",
  "task_history": [
    {
      "task_id": "uuid",
      "type": "scout.discover",
      "status": "completed",
      "started_at": "...",
      "completed_at": "..."
    }
  ],
  "awaiting_gate": false,
  "gate_type": null,
  "error": null
}
```

---

### Cancella un run

```
POST /runs/{run_id}/cancel
```

**Response 200:** `{ "run_id": "uuid", "status": "cancelled" }`

---

## Leads — `/leads`

### Lista leads

```
GET /leads?sector=horeca&qualified=true&page=1&per_page=20
```

Query params opzionali: `sector`, `qualified`, `status`, `city`, `suggested_service_type`, `page`, `per_page`.

**Response 200:**
```json
{
  "items": [ { ...lead } ],
  "total": 145,
  "page": 1,
  "per_page": 20
}
```

---

### Dettaglio lead

```
GET /leads/{lead_id}
```

**Response 200:** oggetto lead completo (senza campi cifrati — solo `client_id` se presente).

---

## Deals — `/deals`

### Lista deals

```
GET /deals?status=proposal_ready&service_type=consulting&page=1&per_page=20
```

**Response 200:**
```json
{
  "items": [ { ...deal } ],
  "total": 12,
  "page": 1,
  "per_page": 20
}
```

---

### Dettaglio deal

```
GET /deals/{deal_id}
```

**Response 200:** oggetto deal completo con gate flags, status, service_type, timestamps.

---

### Aggiorna status deal

```
PATCH /deals/{deal_id}/status
```

**Request:** `{ "status": "lost", "notes": "Cliente non interessato" }`

**Response 200:** deal aggiornato.

---

### GATE 1 — Approva proposta (operatore)

```
POST /deals/{deal_id}/gates/proposal-approve
```

Imposta `proposal_human_approved = true`, `proposal_approved_at = now()`.
Il Gate Poller (Celery Beat, ogni 30s) rileverà il flag e riprende il run.

**Response 200:**
```json
{
  "deal_id": "uuid",
  "gate": "proposal_review",
  "approved": true,
  "approved_at": "2025-01-01T11:00:00Z"
}
```

---

### GATE 1 — Rifiuta proposta (operatore — richiede iterazione)

```
POST /deals/{deal_id}/gates/proposal-reject
```

**Request:** `{ "notes": "Cambiare il pricing, troppo alto per il settore" }`

Incrementa `proposal_rejection_count`, salva `proposal_rejection_notes`.
Il Gate Poller riprende il run non appena il flag `proposal_human_approved` viene impostato
da una successiva approvazione.

**Response 200:**
```json
{
  "deal_id": "uuid",
  "gate": "proposal_review",
  "approved": false,
  "rejection_count": 2,
  "notes": "Cambiare il pricing..."
}
```

---

### GATE 2 — Conferma kickoff erogazione (operatore)

```
POST /deals/{deal_id}/gates/kickoff-confirm
```

Imposta `kickoff_confirmed = true`, `kickoff_confirmed_at = now()`.
Il Gate Poller (Celery Beat) rileverà il flag e riprende il run.

**Response 200:**
```json
{ "deal_id": "uuid", "gate": "kickoff", "confirmed": true, "confirmed_at": "..." }
```

---

### GATE 3 — Approva consegna (operatore)

```
POST /deals/{deal_id}/gates/delivery-approve
```

Behavior in base al `service_type` del deal:
- `web_design` / `digital_maintenance`: imposta `delivery_approved = true`, `delivery_approved_at = now()`
- `consulting`: imposta `consulting_approved = true`, `consulting_approved_at = now()`

Il Gate Poller (Celery Beat) rileverà il flag corretto e completerà il run.

**Response 200:**
```json
{ "deal_id": "uuid", "gate": "delivery", "approved": true, "approved_at": "2025-01-01T12:00:00Z" }
```

---

### GATE 3 — Rifiuta consegna (operatore)

```
POST /deals/{deal_id}/gates/delivery-reject
```

**Request:** `{ "notes": "Il sito non corrisponde al brief concordato" }`

Incrementa `delivery_rejection_count`, salva `delivery_rejection_notes`.
Non sblocca il Gate Poller — il nodo delivery tracker viene rilasciato dalla pipeline per revisione.

**Response 200:**
```json
{ "deal_id": "uuid", "gate": "delivery", "approved": false, "rejection_count": 1, "notes": "Il sito non corrisponde..." }
```

---

## Clients — `/clients`

### Lista clienti

```
GET /clients?page=1&per_page=20
```

**Response 200:** paginato, senza campi PII cifrati (solo `id`, `business_name`, `city`, `status`).

---

### Dettaglio cliente

```
GET /clients/{client_id}
```

**Response 200:** oggetto client completo (campi PII solo se richiesti con scope `admin`).

---

## Proposals — `/proposals`

### Lista proposte per deal

```
GET /proposals?deal_id={deal_id}
```

**Response 200:**
```json
{
  "items": [
    {
      "id": "uuid",
      "deal_id": "uuid",
      "version": 1,
      "pdf_path": "clients/.../proposals/v1.pdf",
      "pdf_download_url": "https://...",    // presigned URL MinIO, 1h
      "sent_at": "...",
      "client_response": "approved"
    }
  ]
}
```

---

### Download PDF proposta

```
GET /proposals/{proposal_id}/download
```

**Response 302:** redirect a presigned URL MinIO (scadenza 1h).

---

## Webhooks — `/webhooks`

Endpoint chiamati dal portale cliente. Autenticati con `PORTAL_SECRET_KEY` (diverso da `SECRET_KEY`).

### Approvazione cliente via portale

```
POST /webhooks/portal/client-approve
```

**Header:** `Authorization: Bearer {portal_jwt}`

**Request:**
```json
{
  "proposal_id": "uuid",
  "token": "jwt_token_dal_link_email"
}
```

**Logica:**
1. Verifica JWT con `PORTAL_SECRET_KEY`
2. Verifica scadenza (72h da `proposal.portal_link_expires`)
3. Aggiorna `proposal.client_response = "approved"`, `proposal.client_response_at`
4. Aggiorna `deal.status = "client_approved"`
5. Notifica Orchestrator via Redis

**Response 200:** `{ "message": "Proposta approvata. Verrete contattati per il kickoff." }`
**Response 400:** `{ "error": "token_expired" }` se JWT scaduto.
**Response 409:** `{ "error": "already_responded" }` se già risposta presente.

---

### Rifiuto cliente via portale

```
POST /webhooks/portal/client-reject
```

**Request:**
```json
{
  "proposal_id": "uuid",
  "token": "jwt_token",
  "notes": "Non siamo pronti ora, richiamatemi tra 6 mesi"
}
```

**Logica:** aggiorna `deal.status = "lost"`, salva note, notifica Orchestrator.

**Response 200:** `{ "message": "Grazie per il feedback." }`

---

## Tasks — `/tasks`

### Lista task (per debug e monitoring)

```
GET /tasks?deal_id={deal_id}&agent=scout&status=failed&page=1&per_page=50
```

**Response 200:** paginato.

---

### Dettaglio task

```
GET /tasks/{task_id}
```

**Response 200:** oggetto task completo con `payload`, `output`, `error`.

---

## Dashboard stats — `/stats`

### Overview pipeline

```
GET /stats/pipeline
```

**Response 200:**
```json
{
  "leads_total": 340,
  "leads_qualified": 87,
  "deals_active": 12,
  "deals_by_service": {
    "consulting": 4,
    "web_design": 5,
    "digital_maintenance": 3
  },
  "deals_awaiting_gate": 3,
  "deals_in_delivery": 4,
  "deals_delivered": 18,
  "revenue_delivered_eur": 145000,
  "revenue_pipeline_eur": 78000
}
```

---

## Clients — NPS Survey — `/clients/{client_id}/nps-survey`

```
GET /clients/{client_id}/nps-survey
```

Genera un link portale con JWT di tipo `"nps"` (scadenza 30 giorni).
Salva il token su `nps_records.survey_token`. Usato dal template email `{{nps_url}}`.

**Response 200:**
```json
{ "survey_url": "https://.../portal/nps/{token}" }
```

---

## `POST /runs` — payload per tipo

Oltre a `"discovery"`, i seguenti tipi richiedono payload specifici:

### `type="proposal"` — rigenerazione proposta

```json
{
  "type": "proposal",
  "payload": {
    "deal_id": "uuid",
    "service_type": "web_design",
    "rejection_notes": null
  }
}
```

`rejection_notes` è `null` per una prima generazione, stringa per rigenera dopo rifiuto.

### `type="delivery"` — avvio/ripresa erogazione

```json
{
  "type": "delivery",
  "payload": {
    "deal_id": "uuid",
    "client_id": "uuid"
  }
}
```

### `type="post_sale"` — avvio post-sale

```json
{
  "type": "post_sale",
  "payload": {
    "deal_id": "uuid",
    "client_id": "uuid"
  }
}
```

### `type="support"` — avvio ticket support (da gmail_poller)

```json
{
  "type": "support",
  "payload": {
    "ticket_id": "uuid",
    "client_id": "uuid",
    "action": "classify",
    "email_thread_id": "gmail_thread_id"
  }
}
```

---

## Note implementative

- Paginazione: sempre `page` (1-based) + `per_page` (max 100).
- Ordering: default `created_at DESC`. Parametro `sort` opzionale: `created_at_asc`, `score_desc`.
- Tutti i timestamp in risposta: ISO 8601 UTC con suffisso `Z`.
- UUID sempre come stringa lowercase con trattini.
- Importi monetari: **sempre in centesimi** (integer), mai float.
- Campi PII: mai esposti in risposta senza scope esplicito. Usare sempre `client_id`.
