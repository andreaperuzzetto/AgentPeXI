# Proposal Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.

## Responsabilità

Assembla la proposta commerciale completa in PDF: analisi problema, soluzione,
mockup incorporati, ROI stimato, pricing, timeline. Salva su MinIO e imposta
`deals.status = "proposal_ready"`. Non invia nulla al cliente.

## Tool disponibili

- `tools/pdf_generator.py` — WeasyPrint + Jinja2 wrapper
- `tools/file_store.py` — lettura PNG mockup, upload PDF
- `tools/db_tools.py` — lettura deal/lead/client, scrittura proposal

## Input atteso (task.payload)

```python
{
    "deal_id": str,
    "mockup_paths": list[str],       # path MinIO da Design Agent
    "rejection_notes": str | None,   # presenti se è una ri-iterazione
    "iteration": int                 # 1 per prima proposta, >1 per iterazioni
}
```

## Output atteso (AgentResult.output)

```python
{
    "deal_id": str,
    "proposal_path": str,    # "clients/{deal_id}/proposals/v{n}.pdf"
    "proposal_version": int,
    "page_count": int
}
```

## Struttura PDF

| Sezione | Contenuto |
|---------|-----------|
| 1. Cover | Nome cliente, data, logo operatore |
| 2. Problema | Gap digitale identificato, dati di supporto |
| 3. Soluzione | Business model, feature principali |
| 4. Mockup | PNG inline, una pagina per schermata |
| 5. ROI | Stima valore generato, payback period |
| 6. Pricing | Opzioni e prezzi (da `config/pricing.yaml`) |
| 7. Timeline | Milestone di sviluppo |
| 8. Prossimi passi | CTA approvazione via portale |

Template Jinja2 in `config/templates/proposal/base.html`.

## Iterazioni

Se `rejection_notes != None`: includere le note nel prompt di generazione.
Salvare come `v{iteration}.pdf` — **non** sovrascrivere versioni precedenti.
Max versioni: 5 (controllato dall'Orchestrator, non da questo agente).

## Tabelle accessibili

| Op. | Tabella / risorsa |
|-----|------------------|
| Legge | `deals`, `leads`, `clients` |
| Scrive | `proposals`, `tasks`, MinIO `clients/{deal_id}/proposals/` |
| Aggiorna | `deals.status → "proposal_ready"` |

## Test

```bash
pytest tests/agents/test_proposal.py -v
python -m agents.proposal.run --deal-id <uuid> --dry-run
```
