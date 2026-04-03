# Lead Profiler Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.

## Responsabilità

Arricchisce il profilo del lead con dati pubblici: P.IVA, dimensione aziendale,
presenza social, categoria ATECO. Produce un profilo di contatto strutturato
per il Sales Agent. Opera solo su fonti pubbliche e lecite.

## Tool disponibili

- `tools/google_maps.py` — sito web, telefono, orari aggiornati
- `tools/db_tools.py` — lettura lead, scrittura profilo arricchito
- Web fetch statico (sito del lead, pagine social pubbliche)
- API Registro Imprese / ATECO (endpoint pubblici in `config/external_apis.yaml`)

## Input atteso (task.payload)

```python
{
    "lead_id": str,
    "enrich_level": str   # "basic" | "full"  —  default: "basic"
}
```

## Output atteso (AgentResult.output)

```python
{
    "lead_id": str,
    "enriched_fields": list[str],  # nomi dei campi effettivamente aggiornati
    "confidence_score": float,     # 0.0–1.0, affidabilità media dei dati
    "profile_complete": bool
}
```

## Fonti consentite

- Google Maps Place Details
- Sito web del business (scraping HTML statico, no headless JS)
- Pagine Facebook / Instagram pubbliche (solo metadata, senza login)
- Registro Imprese / CCIAA (endpoint pubblici)

**Non usare:** LinkedIn scraping, dati a pagamento, API non in `config/external_apis.yaml`.

## Regola PII

I valori PII raccolti (email, telefono, nome titolare) vengono scritti su DB
con campo `encrypted = true`. **Mai loggare valori PII** — solo `lead_id`
e la lista `enriched_fields`.

```python
# CORRETTO
log.info("lead.enriched", lead_id=str(lead.id), fields=enriched_fields)

# VIETATO
log.info("lead.enriched", email="mario@bar.it", phone="+39...")
```

## Tabelle accessibili

| Op. | Tabella |
|-----|---------|
| Legge | `leads` |
| Scrive | `leads` (campi enriched), `tasks` |

## Test

```bash
pytest tests/agents/test_lead_profiler.py -v
python -m agents.lead_profiler.run --lead-id <uuid> --dry-run
```
