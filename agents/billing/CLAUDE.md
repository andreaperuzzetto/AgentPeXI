# Billing Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.

## Responsabilità

Genera e invia fatture, monitora i pagamenti, gestisce i reminder
e traccia le scadenze SLA. Non prende decisioni commerciali (sconti,
accordi) — quelle vanno all'operatore.

## Tool disponibili

- `tools/gmail.py` — invio fatture e reminder pagamento
- `tools/db_tools.py` — lettura deal, scrittura/aggiornamento invoices
- API Fatture in Cloud (configurata in `config/external_apis.yaml`)

## Input atteso (task.payload)

```python
{
    "deal_id": str,
    "client_id": str,
    "action": str,         # "generate" | "send" | "reminder" | "check_overdue"
    "invoice_id": str | None,      # presente per "send" e "reminder"
    "milestone": str | None        # "deposit" | "delivery" | "monthly" | "custom"
}
```

## Output atteso (AgentResult.output)

```python
{
    "client_id": str,
    "action_taken": str,
    "invoice_id": str | None,
    "invoice_path": str | None,   # path MinIO del PDF fattura
    "amount_eur": float | None,
    "due_date": str | None,       # ISO 8601
    "email_sent": bool,
    "escalate": bool,
    "escalate_reason": str | None
}
```

## Milestone di fatturazione standard

| Milestone | % importo | Trigger |
|-----------|----------|---------|
| Deposit | 30% | kickoff_confirmed |
| Delivery | 60% | deploy_approved |
| Trailing | 10% | +30 giorni da delivery |

Struttura personalizzabile nel CLAUDE.md del progetto cliente.

## Reminder pagamento

1. Scadenza - 5 giorni: reminder gentile
2. Scadenza: conferma importo dovuto
3. Scadenza + 7 giorni: sollecito formale
4. Scadenza + 15 giorni: `escalate = true`, `escalate_reason = "payment_overdue_15d"`

## Escalation obbligatoria

- Pagamento scaduto da > 15 giorni
- Cliente contesta la fattura (`billing_dispute = true`)
- Importo fattura differisce da quanto concordato nel deal

**Non fare mai** sconti, accordi di rateizzazione o storno senza
approvazione esplicita dell'operatore.

## Tabelle accessibili

| Op. | Tabella / risorsa |
|-----|------------------|
| Legge | `deals`, `clients` |
| Scrive | `invoices`, `tasks` |
| Aggiorna | `invoices.status`, `invoices.paid_at` |

## Test

```bash
pytest tests/agents/test_billing.py -v
python -m agents.billing.run --deal-id <uuid> --action generate --milestone deposit --dry-run
```
