# Design Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.

## Responsabilità

Genera artefatti visivi contestuali al tipo di servizio e al settore del business.
La tipologia di artefatti cambia in base al `service_type` del deal.
Renderizza via Puppeteer in PNG e PDF, salva gli artefatti su MinIO.
Non interagisce col cliente.

## Artefatti per tipo di servizio

| Servizio | Artefatti prodotti |
|----------|-------------------|
| **Consulenza** | Presentazioni visive, strutture workshop, schemi di processi, roadmap operative |
| **Web Design** | Mockup UI (landing, pagine interne, responsive desktop/mobile) |
| **Manutenzione Digitale** | Schemi architetturali, piani di aggiornamento, dashboard di monitoraggio |

## Tool disponibili

- `tools/mockup_renderer.py` — Puppeteer wrapper (PNG + PDF da HTML)
- `tools/file_store.py` — upload MinIO, ritorna path artefatti
- `tools/db_tools.py` — lettura deal e lead per contesto

## Input atteso (task.payload)

```python
{
    "deal_id": str,
    "service_type": str,            # "consulting" | "web_design" | "digital_maintenance"
    "business_name": str,
    "sector": str,
    "brand_colors": list[str] | None,  # hex rilevati dal Lead Profiler
    "artifact_pages": list[str]        # varia per servizio (vedi sotto)
}
```

**`artifact_pages` per servizio:**
- Consulenza: `["roadmap", "workshop_structure", "process_schema", "presentation"]`
- Web Design: `["landing", "about", "services", "contact"]`
- Manutenzione: `["architecture", "update_plan", "monitoring_dashboard"]`

## Output atteso (AgentResult.output)

```python
{
    "deal_id": str,
    "pages_generated": int,
    "artifacts": list[str]   # path MinIO per ogni PNG/PDF
}
```

## Flusso di generazione

1. Seleziona template base da `config/templates/artifacts/{service_type}/`
2. Usa Claude (`claude-sonnet-4-6`) per personalizzare il codice HTML
   con nome business, colori brand, copy contestuale al servizio
3. Scrive HTML in `/tmp/{deal_id}/artifact_{page}.html`
4. Puppeteer renderizza: viewport 1440×900 (desktop) e 390×844 (mobile)
5. Esporta PNG (2× DPR) e PDF (A4, margini 0)
6. Upload su MinIO: `clients/{deal_id}/artifacts/{page}_{device}.png`

## Vincoli

- Puppeteer timeout: 60s per pagina
- Max 5 pagine per deal
- Temp files in `/tmp/{deal_id}/` — pulire dopo upload
- Non sovrascrivere artefatti esistenti: usare versioning (`v1_`, `v2_`)

## Tabelle accessibili

| Op. | Tabella / risorsa |
|-----|------------------|
| Legge | `deals`, `leads` |
| Scrive | `tasks`, MinIO `clients/{deal_id}/artifacts/` |

## Test

```bash
pytest tests/agents/test_design.py -v
python -m agents.design.run --deal-id <uuid> --dry-run
```
