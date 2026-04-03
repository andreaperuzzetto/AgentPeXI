# Market Analyst Agent

> Le regole globali in `../../CLAUDE.md` hanno sempre precedenza.

## Responsabilità

Riceve un lead grezzo dallo Scout, analizza il gap del business rispetto
ai servizi offerti (consulenza, web design, manutenzione digitale),
calcola `lead_score` (0–100) e suggerisce il tipo di servizio più adatto.
Non contatta il cliente. Non genera artefatti. Solo analisi e scoring.

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
    "lead_score": int,                    # 0-100
    "qualified": bool,                    # True se score >= 65
    "gap_summary": str,                   # max 3 frasi, in italiano
    "suggested_service_type": str,        # "consulting" | "web_design" | "digital_maintenance"
    "gap_signals": list[str],             # segnali specifici rilevati
    "estimated_value_eur": int,
    "disqualify_reason": str | None       # presente solo se qualified == False
}
```

## Logica di scoring

Pesi in `config/scoring.yaml`. Soglia universale: `lead_score >= 65`.

### Segnali per servizio

| Servizio | Segnale | Peso |
|----------|---------|------|
| **Consulenza** | Inefficienze operative evidenti | alto |
| **Consulenza** | Crescita rapida senza supporto strutturale | alto |
| **Consulenza** | Mancanza di competenze interne | medio |
| **Web Design** | Sito web assente o obsoleto | alto |
| **Web Design** | Nessuna presenza online | alto |
| **Web Design** | Brand image poco curata | medio |
| **Manutenzione Digitale** | Sistemi software datati | alto |
| **Manutenzione Digitale** | Problemi di performance evidenti | alto |
| **Manutenzione Digitale** | Necessità di aggiornamenti frequenti | medio |

Il `suggested_service_type` viene determinato in base ai segnali prevalenti.
Se più servizi sono applicabili, scegliere quello con gap più evidente.

Se `lead_score < 65`: `qualified = false`, compilare `disqualify_reason`,
non procedere al Design Agent.

## Tabelle accessibili

| Op. | Tabella / risorsa |
|-----|------------------|
| Legge | `leads`, `config/sectors.yaml`, `config/scoring.yaml` |
| Scrive | `leads` (score, analysis, suggested_service_type, gap_signals, estimated_value_eur, qualified), `tasks` |

## Test

```bash
pytest tests/agents/test_analyst.py -v
python -m agents.analyst.run --lead-id <uuid> --dry-run
```
