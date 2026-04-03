# Proposal Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.

## Responsabilità

Assembla la proposta commerciale completa in PDF in base al `service_type` del deal.
La struttura della proposta cambia leggermente a seconda del contesto del servizio.
Salva su MinIO e imposta `deals.status = "proposal_ready"`. Non invia nulla al cliente.
Pricing: **per progetto**.

## Tool disponibili

- `tools/pdf_generator.py` — WeasyPrint + Jinja2 wrapper
- `tools/file_store.py` — lettura artefatti, upload PDF
- `tools/db_tools.py` — lettura deal/lead/client, scrittura proposal

## Input atteso (task.payload)

```python
{
    "deal_id": str,
    "service_type": str,              # "consulting" | "web_design" | "digital_maintenance"
    "artifact_paths": list[str],      # path MinIO da Design Agent
    "rejection_notes": str | None,    # presenti se è una ri-iterazione
    "iteration": int                  # 1 per prima proposta, >1 per iterazioni
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

## Struttura PDF (adattata per servizio)

| Sezione | Consulenza | Web Design | Manutenzione Digitale |
|---------|-----------|-----------|----------------------|
| 1. Cover | Nome cliente, data, logo | Nome cliente, data, logo | Nome cliente, data, logo |
| 2. Problema | Gap operativi identificati | Gap digitale/brand | Sistemi obsoleti, performance |
| 3. Soluzione | Piano di consulenza, deliverable | Design proposto, mockup | Piano manutenzione, SLA |
| 4. Artefatti | Roadmap, schemi processo | Mockup inline | Piano aggiornamenti |
| 5. ROI | Stima miglioramento operativo | Stima valore presenza online | Stima risparmio downtime |
| 6. Pricing | Per progetto (da `config/pricing.yaml`) | Per progetto | Per progetto |
| 7. Timeline | Milestone consulenza | Milestone web design | Cicli di manutenzione |
| 8. Prossimi passi | CTA approvazione via portale | CTA approvazione | CTA approvazione |

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
