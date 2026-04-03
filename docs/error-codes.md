# Catalogo codici errore

Errori emessi dagli agenti e dai tool. Tutti i codici sono stringhe snake_case.
Usati in `task.error`, nei log e nelle risposte API.

---

## Classificazione

| Classe | Recoverable | Comportamento Celery |
|--------|-------------|----------------------|
| `transient_*` | ✅ Sì | Retry automatico (max 3×, backoff) |
| `gate_*` | 🟡 Attende umano | Task → `blocked`, `requires_human_gate = True` |
| `validation_*` | ❌ No | Task → `failed`, no retry |
| `tool_*` | Dipende | Vedi tabella |
| `agent_*` | ❌ No | Task → `failed`, no retry |
| `security_*` | ❌ No | Task → `blocked`, notifica operatore urgente |

---

## Tool errors — `tools/db_tools.py`

| Codice | Quando | Recoverable |
|--------|--------|-------------|
| `tool_db_lead_not_found` | `get_lead()` con ID inesistente | ❌ |
| `tool_db_deal_not_found` | `get_deal()` con ID inesistente | ❌ |
| `tool_db_client_not_found` | `get_client()` con ID inesistente | ❌ |
| `tool_db_proposal_not_found` | `get_proposal()` con ID inesistente | ❌ |
| `tool_db_service_delivery_not_found` | `get_service_delivery()` con ID inesistente | ❌ |
| `tool_db_duplicate_lead` | `create_lead()` con `google_place_id` già presente | ❌ (skip, non errore) |
| `tool_db_max_proposal_versions` | `create_proposal()` con version > 5 | ❌ → escalation manuale |
| `tool_db_connection_error` | Connessione PostgreSQL fallita | ✅ transient |
| `tool_db_timeout` | Query > 30s | ✅ transient |
| `tool_db_write_error` | Scrittura fallita (constraint, lock) | ✅ transient |

## Tool errors — `tools/file_store.py`

| Codice | Quando | Recoverable |
|--------|--------|-------------|
| `tool_storage_upload_error` | Upload MinIO fallito | ✅ transient |
| `tool_storage_not_found` | `download_file()` con key inesistente | ❌ |
| `tool_storage_connection_error` | MinIO non raggiungibile | ✅ transient |

## Tool errors — `tools/google_maps.py`

| Codice | Quando | Recoverable |
|--------|--------|-------------|
| `tool_maps_quota_exceeded` | Quota giornaliera API esaurita | ❌ → `blocked`, notificare operatore |
| `tool_maps_place_not_found` | Place ID inesistente o rimosso | ❌ (skip lead) |
| `tool_maps_timeout` | Risposta API > 10s dopo 3 retry | ✅ transient |
| `tool_maps_api_error` | Errore 5xx Maps API | ✅ transient |

## Tool errors — `tools/gmail.py`

| Codice | Quando | Recoverable |
|--------|--------|-------------|
| `tool_gmail_send_error` | Invio email fallito | ✅ transient (max 2×) |
| `tool_gmail_auth_error` | Token OAuth scaduto / revocato | ❌ → `blocked`, operatore deve rinnovare token |
| `tool_gmail_rate_limit` | 429 da Gmail API | ✅ transient (backoff 60s) |
| `tool_gmail_thread_not_found` | Thread ID inesistente | ❌ |

## Tool errors — `tools/pdf_generator.py`

| Codice | Quando | Recoverable |
|--------|--------|-------------|
| `tool_pdf_template_not_found` | File template HTML assente | ❌ |
| `tool_pdf_render_error` | WeasyPrint errore | ✅ transient (1 retry) |
| `tool_pdf_output_error` | Impossibile scrivere file output | ❌ (disco pieno?) |

## Tool errors — `tools/mockup_renderer.py`

| Codice | Quando | Recoverable |
|--------|--------|-------------|
| `tool_render_timeout` | Puppeteer > 60s | ✅ transient (1 retry con pagina semplificata) |
| `tool_render_error` | Errore Chromium / JS error nella pagina | ✅ transient (1 retry) |
| `tool_render_output_error` | Impossibile scrivere PNG/PDF | ❌ |

---

## Gate errors

| Codice | Gate | Azione |
|--------|------|--------|
| `gate_proposal_not_approved` | GATE 1 | Task → `blocked`, `requires_human_gate = True`, `gate_type = "proposal_review"` |
| `gate_kickoff_not_confirmed` | GATE 2 | Task → `blocked`, `requires_human_gate = True`, `gate_type = "kickoff"` |
| `gate_delivery_not_approved` | GATE 3 | Task → `blocked`, `requires_human_gate = True`, `gate_type = "delivery"` |

---

## Validation errors

| Codice | Agente | Causa |
|--------|--------|-------|
| `validation_invalid_sector` | Scout | `sector` non presente in `config/sectors.yaml` |
| `validation_invalid_service_type` | Tutti | `service_type` non in `["consulting", "web_design", "digital_maintenance"]` |
| `validation_missing_payload_field` | Tutti | Campo obbligatorio assente in `task.payload` |
| `validation_lead_not_qualified` | Analyst → Proposal | Lead con `qualified = False`: non procedere |
| `validation_deal_wrong_status` | Delivery Orch | Deal non in stato compatibile con l'azione richiesta |
| `validation_invoice_amount_mismatch` | Billing | Importo calcolato diverge da quanto nel deal |

---

## Agent errors

| Codice | Agente | Causa |
|--------|--------|-------|
| `agent_scout_no_results` | Scout | 0 risultati dopo 3 espansioni → `blocked` |
| `agent_analyst_no_qualified_leads` | Analyst | Tutti i lead in lista < 65 score |
| `agent_proposal_max_versions` | Proposal | Superato limit 5 versioni → `blocked`, escalation manuale |
| `agent_sales_max_negotiation_rounds` | Sales | > 2 round autonomi → `blocked`, operatore deve gestire |
| `agent_sales_client_lost` | Sales | 3 follow-up senza risposta → deal `lost` |
| `agent_delivery_tracker_max_rejections` | Delivery Tracker | Deliverable rifiutato > 3 volte → `blocked` |
| `agent_billing_payment_overdue` | Billing | Fattura scaduta da > 15 giorni → `escalate = True` |
| `agent_support_sla_breach` | Support | SLA first response superato → `escalate = True` |

---

## Security errors

| Codice | Agente | Azione |
|--------|--------|--------|
| `security_injection_attempt` | Scout, Sales, Support, Design, Lead Profiler | Task → `blocked`, `requires_human_gate = True`, notifica urgente operatore |
| `security_unauthorized_workspace_access` | Doc Generator, Delivery Tracker | Task → `failed`, log audit, notifica operatore |
| `security_pii_in_log` | Qualsiasi | Non è un errore run-time — è un bug da fixare con priorità critica |

---

## Formato log per errori

```python
# Errore recoverable (transient)
log.warning(
    "task.error.transient",
    task_id=str(task.id),
    agent=task.agent,
    error_code="tool_db_timeout",
    retry_count=task.retry_count,
)

# Errore bloccante (gate o validazione)
log.error(
    "task.error.blocking",
    task_id=str(task.id),
    agent=task.agent,
    error_code="gate_proposal_not_approved",
    deal_id=str(task.deal_id),
)

# Errore sicurezza
log.critical(
    "task.error.security",
    task_id=str(task.id),
    agent=task.agent,
    error_code="security_injection_attempt",
    source="email_body",   # NON loggare il contenuto
)
```

**PII nei log:** mai loggare email, nome, telefono, P.IVA.
Loggare solo: task_id, deal_id, client_id, agent, error_code, source (categoria, non valore).
