# Delivery Orchestrator Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.
> **Sostituisce** il vecchio Dev Orchestrator Agent.

## Responsabilità

Pianifica e coordina l'erogazione del servizio venduto in base al `service_type` del deal.
Decompone il servizio in task di erogazione atomici (`service_deliveries`), li assegna
agli agenti specializzati (Document Generator, Delivery Tracker) e monitora l'avanzamento.
È il "project manager" del servizio. Non produce documenti direttamente.

## GATE 2 — controllo obbligatorio prima dell'avvio

```python
deal = await db.get(Deal, deal_id)
if not deal.kickoff_confirmed:
    raise GateNotApprovedError("GATE 2 non confermato — erogazione bloccata")
```

## Tool disponibili

- `tools/db_tools.py` — lettura deal, scrittura service_deliveries
- `tools/file_store.py` — lettura documenti proposta da MinIO
- Accesso al workspace cliente: `/workspace/clients/{client_id}/`

## Input atteso (task.payload)

```python
{
    "deal_id": str,
    "client_id": str,
    "service_type": str,        # "consulting" | "web_design" | "digital_maintenance"
    "proposal_path": str,       # path MinIO della proposta approvata
    "deliverables_json": dict,  # lista deliverable dalla proposta
    "priority_deliverables": list[str]
}
```

## Output atteso (AgentResult.output)

```python
{
    "deal_id": str,
    "service_deliveries_created": int,
    "estimated_weeks": int,
    "delivery_breakdown": list[dict]  # {id, type, title, depends_on, milestone_name}
}
```

## Logica di decomposizione per servizio

### Consulenza
1. Analisi esigenze → report iniziale
2. Workshop (uno o più) → presentazione + materiali
3. Roadmap operativa → documento finale
4. Consegna e presentazione risultati

### Web Design
1. Wireframe → approvazione struttura
2. Mockup dettagliato → revisione design
3. Sviluppo pagine → implementazione
4. Test e QA → verifica responsive/cross-browser
5. Consegna e pubblicazione

### Manutenzione Digitale
1. Audit iniziale → report stato attuale
2. Piano aggiornamenti → roadmap tecnica
3. Primo ciclo aggiornamento → esecuzione
4. Setup monitoraggio → dashboard

## Milestone per servizio

| Servizio | Milestone chiave | Gate associato |
|----------|-----------------|---------------|
| Consulenza | Inizio primo workshop / firma contratto | `consulting_approved` |
| Web Design | Approvazione mockup finale | `delivery_approved` |
| Manutenzione Digitale | Primo ciclo di aggiornamento pianificato | `delivery_approved` |

## Monitoraggio avanzamento

Il Delivery Orchestrator viene richiamato dall'Orchestrator ogni volta che
un task di erogazione viene completato. Valuta se:
- Procedere con il task successivo nella catena
- Sbloccare task paralleli ora che le dipendenze sono soddisfatte
- Segnalare blocchi all'Orchestrator principale
- Aggiornare `deal.status = "delivered"` quando tutti i deliverable sono completati

## Tabelle accessibili

| Op. | Tabella / risorsa |
|-----|------------------|
| Legge | `deals`, `proposals`, MinIO `clients/{deal_id}/proposals/` |
| Scrive | `service_deliveries`, `tasks` |
| Legge/scrive | `/workspace/clients/{client_id}/` |

## Test

```bash
pytest tests/agents/test_delivery_orchestrator.py -v
python -m agents.delivery_orchestrator.run --deal-id <uuid> --dry-run
```
