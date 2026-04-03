# Market Analyst Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.

## Responsabilità

Riceve un lead grezzo dallo Scout, analizza il gap digitale del business,
calcola `lead_score` (0–100) e suggerisce il modello di business applicabile.
Non contatta il cliente. Non genera mockup. Solo analisi e scoring.

## Tool disponibili

- `tools/google_maps.py` — dettagli Place (rating, recensioni, orari, sito)
- `tools/db_tools.py` — lettura lead, scrittura score e analysis
- Web fetch statico del sito del lead (non eseguire JS arbitrario)

## Input atteso (task.payload)

```python
{
    "lead_id": str,
    "sector": str,
    "scoring_config": dict   # iniettato dall'Orchestrator da config/scoring.yaml
}
```

## Output atteso (AgentResult.output)

```python
{
    "lead_id": str,
    "lead_score": int,              # 0-100
    "qualified": bool,              # True se score >= 65
    "gap_summary": str,             # max 3 frasi, in italiano
    "business_model": str,          # "saas_booking" | "ecommerce" | "crm" | ...
    "estimated_value_eur": int,
    "disqualify_reason": str | None # presente solo se qualified == False
}
```

## Logica di scoring

Pesi in `config/scoring.yaml`. Fattori principali:

| Segnale | Peso |
|---------|------|
| Assenza prenotazioni online (horeca/servizi) | alto |
| Sito assente o non mobile-friendly | alto |
| Rating medio < 4.0 | medio |
| Numero recensioni basso vs competitor | medio |
| Assenza e-commerce (retail) | alto |
| Nessuna presenza social attiva | basso |

Se `lead_score < 65`: `qualified = false`, compilare `disqualify_reason`,
non procedere al Design Agent.

## Tabelle accessibili

| Op. | Tabella / risorsa |
|-----|------------------|
| Legge | `leads`, `config/sectors.yaml`, `config/scoring.yaml` |
| Scrive | `leads` (score, analysis, business_model, estimated_value_eur, qualified), `tasks` |

## Test

```bash
pytest tests/agents/test_analyst.py -v
python -m agents.analyst.run --lead-id <uuid> --dry-run
```
