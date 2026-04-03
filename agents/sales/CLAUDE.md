# Sales Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.

## Responsabilità

Invia la proposta al cliente via email, gestisce il follow-up e traccia
le interazioni nel CRM. Gestisce autonomamente fino a 2 round di negoziazione
su modifiche minor. Non invia MAI senza GATE 1 approvato.

## GATE 1 — controllo obbligatorio prima di qualsiasi invio

```python
# Verificare sempre da DB, mai da stato in-memory
deal = await db.get(Deal, deal_id)
if not deal.proposal_human_approved:
    raise GateNotApprovedError("GATE 1 non approvato — invio bloccato")
```

## Tool disponibili

- `tools/gmail.py` — send, reply, thread management
- `tools/file_store.py` — presigned URL per link proposta nel portale
- `tools/db_tools.py` — lettura deal/proposal/client, scrittura email_log

## Input atteso (task.payload)

```python
{
    "deal_id": str,
    "proposal_path": str,       # path MinIO PDF
    "action": str,              # "send_proposal" | "follow_up" | "negotiate"
    "negotiation_notes": str | None,  # dal cliente, se action == "negotiate"
    "follow_up_number": int     # 1 | 2 | 3  (max 3 follow-up automatici)
}
```

## Output atteso (AgentResult.output)

```python
{
    "deal_id": str,
    "action_taken": str,
    "email_sent": bool,
    "thread_id": str | None,
    "next_action": str | None,   # "await_response" | "escalate" | "close_lost"
    "deal_status_updated": str | None
}
```

## Sequenza di contatto

1. **Invio proposta** — email personalizzata + link portale approvazione (JWT 72h)
2. **Follow-up 1** — dopo 3 giorni lavorativi senza risposta
3. **Follow-up 2** — dopo altri 5 giorni
4. **Follow-up 3** — dopo altri 7 giorni, tono "ultimo contatto"
5. **Close lost** — se nessuna risposta dopo follow-up 3

Template email in `config/templates/email/`.
Lingua: **italiano** (tutte le comunicazioni verso il cliente).

## Negoziazione autonoma

Il Sales Agent gestisce autonomamente **fino a 2 round** su:
- Riduzione prezzo ≤ 15%
- Variazioni di timeline ≤ 2 settimane
- Aggiunta/rimozione di feature minor (non nel core scope)

Oltre: `status = "blocked"`, `blocked_reason = "negotiation_requires_human"`.

## Tabelle accessibili

| Op. | Tabella |
|-----|---------|
| Legge | `deals`, `proposals`, `clients` |
| Scrive | `deals.status`, `email_log`, `tasks` |

## Test

```bash
pytest tests/agents/test_sales.py -v
python -m agents.sales.run --deal-id <uuid> --action send_proposal --dry-run
```
