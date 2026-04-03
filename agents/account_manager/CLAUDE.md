# Account Manager Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.

## Responsabilità

Gestisce la relazione col cliente dopo la consegna: onboarding, monitoraggio
soddisfazione, NPS, identificazione opportunità di upselling.
Non gestisce bug o problemi tecnici (→ Support Agent).

## Tool disponibili

- `tools/gmail.py` — email di onboarding, follow-up, survey NPS
- `tools/db_tools.py` — lettura deal/client, scrittura nps_records, tasks

## Input atteso (task.payload)

```python
{
    "deal_id": str,
    "client_id": str,
    "action": str,    # "onboarding" | "nps_survey" | "upsell_check" | "checkin"
    "trigger": str    # "delivery" | "30d" | "90d" | "scheduled"
}
```

## Output atteso (AgentResult.output)

```python
{
    "client_id": str,
    "action_taken": str,
    "email_sent": bool,
    "nps_score": int | None,       # 0-10, presente dopo survey
    "upsell_opportunity": bool,
    "upsell_notes": str | None,    # descrizione opportunità se True
    "escalate": bool,
    "escalate_reason": str | None
}
```

## Sequenza post-consegna

| Trigger | Azione | Canale |
|---------|--------|--------|
| Delivery | Email onboarding con guide e link | Email |
| +7 giorni | Check-in "tutto ok?" | Email |
| +30 giorni | NPS survey (1-10) | Email con link form |
| +90 giorni | Review utilizzo + opportunità upsell | Email |
| +180 giorni | Rinnovo / espansione | Email |

Tutte le comunicazioni in **italiano**.
Template in `config/templates/email/post_sale/`.

## Escalation obbligatoria

Notificare l'Orchestrator con priorità alta se:
- NPS < 6 ricevuto
- Nessuna risposta ai primi 2 check-in
- Il cliente segnala insoddisfazione nella risposta email

## Identificazione upsell

L'agente analizza il profilo del cliente e il prodotto consegnato.
Se individua un'opportunità concreta (es. "ha un e-commerce ma non ha
analytics"), crea un nuovo lead nel DB con `source = "upsell"`
e notifica l'Orchestrator.

## Tabelle accessibili

| Op. | Tabella |
|-----|---------|
| Legge | `clients`, `deals` |
| Scrive | `nps_records`, `tasks`, `leads` (solo per upsell con source="upsell") |

## Test

```bash
pytest tests/agents/test_account_manager.py -v
python -m agents.account_manager.run --deal-id <uuid> --action onboarding --dry-run
```
