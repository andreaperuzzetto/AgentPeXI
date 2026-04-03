# Gmail MCP Server

Server MCP custom che espone Gmail all'agente tramite protocollo stdio.
Usato da tutti gli agenti che devono inviare o leggere email.

**File:** `src/mcp_servers/gmail/server.py`
**Avvio:** `python -m mcp_servers.gmail.server` (stdio)

---

## Struttura

```
src/mcp_servers/gmail/
├── __init__.py
├── server.py       ← Entry point MCP (stdio)
└── auth.py         ← Credenziali OAuth2 da env
```

---

## Avvio del server

Il server viene avviato come processo figlio separato (stdio).
**Non viene importato direttamente dagli agenti** — il wrapper `tools/gmail.py`
gestisce il ciclo di vita del processo.

```bash
# Avvio manuale per testing
python -m mcp_servers.gmail.server
```

---

## Autenticazione (`auth.py`)

```python
# src/mcp_servers/gmail/auth.py
import os
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

def build_credentials() -> Credentials:
    """
    Costruisce credenziali OAuth2 dalle variabili d'ambiente.
    Non salvare mai token su disco. Ogni avvio si autentica da env.
    """
    return Credentials(
        token=os.environ["GMAIL_ACCESS_TOKEN"],
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        scopes=SCOPES,
    )
```

**Variabili d'ambiente richieste:**

| Variabile | Descrizione |
|-----------|-------------|
| `GMAIL_CLIENT_ID` | OAuth2 client ID |
| `GMAIL_CLIENT_SECRET` | OAuth2 client secret |
| `GMAIL_ACCESS_TOKEN` | Access token (refresh automatico) |
| `GMAIL_REFRESH_TOKEN` | Refresh token permanente |
| `GMAIL_SENDER_ADDRESS` | Indirizzo mittente (es. `andrea@agentpexi.it`) |

---

## Tool esposti

### `send_email`

Invia una nuova email o risponde a un thread esistente.

**Input:**
```json
{
  "to": "cliente@example.com",
  "subject": "La tua proposta AgentPeXI",
  "body": "<html>...</html>",
  "thread_id": null
}
```

| Campo | Tipo | Obbligatorio | Descrizione |
|-------|------|-------------|-------------|
| `to` | `string` | sì | Indirizzo destinatario |
| `subject` | `string` | sì | Oggetto email |
| `body` | `string` | sì | Corpo HTML o plain text |
| `thread_id` | `string \| null` | no | Se presente, risponde al thread |

**Output:**
```json
{
  "message_id": "18abc123def456",
  "thread_id": "18abc123def456"
}
```

**Sicurezza:** `to` non viene mai loggato — solo `message_id`.

---

### `read_thread`

Legge tutti i messaggi di un thread Gmail.

**Input:**
```json
{
  "thread_id": "18abc123def456"
}
```

**Output:**
```json
{
  "thread_id": "18abc123def456",
  "messages": [
    {
      "message_id": "18abc123def456",
      "from": "cliente@example.com",
      "date": "2025-01-15T10:30:00Z",
      "snippet": "Grazie per la proposta...",
      "body": "Testo completo del messaggio"
    }
  ]
}
```

**Sicurezza:** Non loggare `from` — usare solo per elaborazione interna.

---

### `list_unread`

Lista email non lette in inbox.

**Input:**
```json
{
  "max_results": 50
}
```

| Campo | Tipo | Default | Descrizione |
|-------|------|---------|-------------|
| `max_results` | `integer` | `50` | Numero massimo di email da restituire |

**Output:**
```json
[
  {
    "message_id": "18abc123def456",
    "thread_id": "18abc123def456",
    "subject": "Re: Proposta web design",
    "from": "cliente@example.com",
    "date": "2025-01-15T10:30:00Z",
    "snippet": "Ho ricevuto la proposta..."
  }
]
```

Usato dal Support Agent per rilevare nuovi ticket e risposte a proposte.

---

### `search_emails`

Ricerca email con query Gmail standard.

**Input:**
```json
{
  "query": "from:cliente@example.com subject:proposta"
}
```

**Output:** Stessa struttura di `list_unread`.

**Query examples:**
- `"from:xyz@example.com"` — email da mittente specifico
- `"subject:proposta"` — email con parola nel subject
- `"thread_id:18abc"` — tutti i messaggi di un thread
- `"is:unread after:2025/01/01"` — non lette dopo data

---

## Pattern d'uso dagli agenti

Gli agenti non chiamano il MCP direttamente. Usano `tools/gmail.py`:

```python
# src/agents/sales/agent.py
from tools.gmail import send_email

async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
    result = await send_email(
        to=client.email,        # non loggare questa variabile
        subject=f"Proposta per {client.business_name}",
        body=proposal_html,
        thread_id=None,
    )
    await log_email(
        agent=self.agent_name,
        direction="outbound",
        template_name="proposal_send",
        gmail_message_id=result["message_id"],
        gmail_thread_id=result["thread_id"],
        subject=f"Proposta per {client.business_name}",
        db=db,
        deal_id=task.deal_id,
        client_id=task.client_id,
        task_id=task.id,
    )
```

**Obbligatorio:** dopo ogni `send_email`, chiamare `log_email` con `gmail_message_id`.

---

## Errori

| Codice | Eccezione | Causa |
|--------|-----------|-------|
| `gmail_send_error` | `GmailSendError` | Errore API Gmail in invio |
| `gmail_auth_error` | `GmailSendError` | Token scaduto e refresh fallito |
| `gmail_quota_error` | `GmailSendError` | Quota giornaliera Gmail esaurita |

Tutte le eccezioni estendono `AgentToolError` — il `BaseAgent.run()` le cattura e
imposta `task.status = "failed"`.
