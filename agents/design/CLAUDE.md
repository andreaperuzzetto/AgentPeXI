# Design Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.

## Responsabilità

Genera mockup UI contestuali al business model identificato dall'Analyst.
Produce HTML/React, li renderizza via Puppeteer in PNG e PDF, e salva
gli artefatti su MinIO. Non interagisce col cliente.

## Tool disponibili

- `tools/mockup_renderer.py` — Puppeteer wrapper (PNG + PDF da HTML)
- `tools/file_store.py` — upload MinIO, ritorna path artefatti
- `tools/db_tools.py` — lettura deal e lead per contesto

## Input atteso (task.payload)

```python
{
    "deal_id": str,
    "business_model": str,          # "saas_booking" | "ecommerce" | "crm" | ...
    "business_name": str,
    "sector": str,
    "brand_colors": list[str] | None,  # hex rilevati dal Lead Profiler
    "mockup_pages": list[str]          # ["landing", "booking", "dashboard"]
}
```

## Output atteso (AgentResult.output)

```python
{
    "deal_id": str,
    "pages_generated": int,
    "artifacts": list[str]   # path MinIO per ogni PNG/PDF
}
```

## Flusso di generazione

1. Seleziona template base da `config/templates/mockups/{business_model}/`
2. Usa Claude (`claude-sonnet-4-6`) per personalizzare il codice HTML
   con nome business, colori brand, copy contestuale
3. Scrive HTML in `/tmp/{deal_id}/mockup_{page}.html`
4. Puppeteer renderizza: viewport 1440×900 (desktop) e 390×844 (mobile)
5. Esporta PNG (2× DPR) e PDF (A4, margini 0)
6. Upload su MinIO: `clients/{deal_id}/mockups/{page}_{device}.png`

## Vincoli

- Puppeteer timeout: 60s per pagina
- Max 5 pagine per deal
- Temp files in `/tmp/{deal_id}/` — pulire dopo upload
- Non sovrascrivere artefatti esistenti: usare versioning (`v1_`, `v2_`)

## Tabelle accessibili

| Op. | Tabella / risorsa |
|-----|------------------|
| Legge | `deals`, `leads` |
| Scrive | `tasks`, MinIO `clients/{deal_id}/mockups/` |

## Test

```bash
pytest tests/agents/test_design.py -v
python -m agents.design.run --deal-id <uuid> --dry-run
```
