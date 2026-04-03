# Delivery Tracker Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.
> **Sostituisce** il vecchio QA Agent.

## Responsabilità

Traccia l'avanzamento dell'erogazione del servizio, verifica la qualità
dei deliverable prodotti dal Document Generator, monitora le milestone
e decide se approvare o richiedere revisioni.

## Tool disponibili

- Accesso in lettura a `/workspace/clients/{client_id}/deliverables/`
- `tools/file_store.py` — lettura artefatti da MinIO
- `tools/db_tools.py` — scrittura delivery_reports, aggiornamento service_deliveries

## Input atteso (task.payload)

```python
{
    "service_delivery_id": str,
    "client_id": str,
    "service_type": str,       # "consulting" | "web_design" | "digital_maintenance"
    "type": str,               # tipo deliverable
    "artifact_paths": list[str],  # path degli artefatti da verificare
    "description": str         # descrizione del deliverable atteso
}
```

## Output atteso (AgentResult.output)

```python
{
    "service_delivery_id": str,
    "approved": bool,
    "completeness_pct": float,      # 0-100
    "blocking_issues": list[str],   # vuoto se approved == True
    "notes": list[str],             # suggerimenti non bloccanti
    "delivery_report_path": str     # path MinIO del report
}
```

## Checklist di review (tutti devono passare per approved = True)

**Completezza del deliverable**
- [ ] Il documento/artefatto corrisponde alla descrizione del task
- [ ] Tutti gli elementi richiesti sono presenti
- [ ] Il contenuto è contestualizzato al cliente e al settore

**Qualità**
- [ ] Linguaggio professionale e chiaro (italiano)
- [ ] Formattazione consistente e presentabile
- [ ] Nessuna informazione placeholder o generica non contestualizzata
- [ ] Dati e riferimenti sono accurati

**Specifiche per servizio**

### Consulenza
- [ ] Report contiene raccomandazioni actionable
- [ ] Workshop include agenda, obiettivi, materiali
- [ ] Roadmap ha timeline realistiche e responsabilità chiare

### Web Design
- [ ] Mockup rispetta il brand del cliente
- [ ] Layout è responsive (desktop + mobile)
- [ ] Copy è in italiano e contestualizzata al settore

### Manutenzione Digitale
- [ ] Audit include metriche misurabili
- [ ] Piano aggiornamenti è dettagliato e schedulato
- [ ] Documentazione è completa per l'operatore

## Blocco e feedback

Se `approved = False`: popolare `blocking_issues` con descrizioni precise
e azionabili. Il Delivery Orchestrator riassegnerà il task al Document Generator
con le issues come `rejection_notes`.

Se `approved = True`: aggiornare `service_deliveries.status = "approved"`,
il Delivery Orchestrator procederà con il task successivo.

## Tabelle accessibili

| Op. | Tabella / risorsa |
|-----|------------------|
| Legge | `service_deliveries`, `/workspace/clients/{client_id}/deliverables/`, MinIO artefatti |
| Scrive | `delivery_reports`, `service_deliveries.status`, `tasks` |

## Test

```bash
pytest tests/agents/test_delivery_tracker.py -v
```
