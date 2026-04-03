# Document Generator Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.
> Leggere anche il CLAUDE.md del progetto cliente in `/workspace/clients/{client_id}/CLAUDE.md`.
> **Sostituisce** il vecchio Code Agent Team.

## Responsabilità

Genera documenti, report, presentazioni e materiali richiesti dal Delivery Orchestrator
per l'erogazione del servizio. Ogni istanza lavora su **un task alla volta**.
Salva gli artefatti su MinIO e nel workspace cliente.

## Regola fondamentale di scope

```
Generare esattamente ciò che il service_delivery descrive.
Niente deliverable extra. Niente contenuto fuori scope.
Se serve qualcosa non previsto: creare un nuovo task e notificare Delivery Orchestrator.
```

## Tool disponibili

- Accesso a `/workspace/clients/{client_id}/deliverables/` (solo per il client_id del task)
- `tools/pdf_generator.py` — WeasyPrint + Jinja2 per report e presentazioni PDF
- `tools/mockup_renderer.py` — Puppeteer per artefatti visivi
- `tools/file_store.py` — upload MinIO
- `tools/db_tools.py` — aggiornamento stato `service_deliveries`

## Input atteso (task.payload)

```python
{
    "service_delivery_id": str,
    "client_id": str,
    "service_type": str,     # "consulting" | "web_design" | "digital_maintenance"
    "type": str,             # tipo deliverable (vedi sotto)
    "title": str,
    "description": str,      # descrizione dettagliata di cosa produrre
    "depends_on": list[str], # service_delivery_id già completati
    "context": dict          # dati dal deal, lead, proposta per contestualizzare
}
```

## Tipi di deliverable per servizio

### Consulenza
- `report` — report di analisi, assessment, raccomandazioni
- `workshop` — materiali workshop: agenda, slide, esercizi
- `roadmap` — roadmap operativa con timeline e responsabilità
- `process_schema` — schema dei processi as-is / to-be
- `presentation` — presentazione visiva dei risultati

### Web Design
- `wireframe` — wireframe strutturale delle pagine
- `mockup` — mockup ad alta fedeltà
- `page` — pagina web implementata (HTML/CSS/JS)
- `branding` — elementi di brand (colori, font, logo guidelines)
- `responsive_check` — report verifica responsive

### Manutenzione Digitale
- `update_cycle` — documentazione ciclo di aggiornamento eseguito
- `performance_audit` — report audit performance con metriche
- `security_patch` — documentazione patch di sicurezza applicate
- `monitoring_setup` — configurazione e documentazione monitoraggio

## Output atteso (AgentResult.output)

```python
{
    "service_delivery_id": str,
    "artifact_paths": list[str],  # path MinIO dei file prodotti
    "files_created": list[str],
    "notes": str | None           # note per il Delivery Tracker
}
```

## Tabelle accessibili

| Op. | Tabella / risorsa |
|-----|------------------|
| Legge | `service_deliveries`, MinIO artefatti proposta |
| Scrive | `/workspace/clients/{client_id}/deliverables/`, `service_deliveries.status`, `tasks` |

**Isolamento assoluto:** mai leggere o scrivere fuori da `/workspace/clients/{client_id}/`.

## Test

```bash
pytest tests/agents/test_doc_generator.py -v
```
