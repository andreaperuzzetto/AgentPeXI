# Configurazione YAML — schema e utilizzo

Tutti i file di configurazione vivono in `config/` (radice del progetto).
Sono letti a runtime dagli agenti — non hardcodare questi valori nel codice.

---

## `config/sectors.yaml` — settori di mercato

Elenco dei settori supportati dallo Scout. Ogni agente che lavora con settori
deve usare le chiavi di questo file, non stringhe libere.

```yaml
horeca:
  label: "Ristorazione & Hospitality"
  keywords: ["ristorante", "bar", "hotel", "pizzeria", "trattoria", "osteria", "agriturismo"]

retail:
  label: "Commercio al dettaglio"
  keywords: ["negozio", "boutique", "ferramenta", "gioielleria", "abbigliamento"]

benessere:
  label: "Benessere & Salute"
  keywords: ["parrucchiere", "estetista", "barbiere", "spa", "centro estetico", "palestra"]

professioni:
  label: "Liberi professionisti & Studi"
  keywords: ["studio", "consulente", "avvocato", "commercialista", "architetto", "dentista"]

artigianato:
  label: "Artigianato & Manifattura"
  keywords: ["falegname", "idraulico", "elettricista", "meccanico", "carrozzeria"]
```

**Utilizzo:**

```python
import yaml
from pathlib import Path

sectors = yaml.safe_load((Path(__file__).parents[2] / "config" / "sectors.yaml").read_text())
# settori validi: list(sectors.keys())
```

---

## `config/scoring.yaml` — pesi per qualifica lead

Definisce i pesi dei segnali di gap per ogni `service_type`. Il Market Analyst
usa questi pesi per calcolare il `lead_score`. Soglia minima in `thresholds`.

```yaml
web_design:
  no_website:            { weight: 40, threshold: true }   # segnale bloccante
  obsolete_website:      { weight: 25, threshold: false }
  low_social_presence:   { weight: 15, threshold: false }
  poor_reviews_presence: { weight: 10, threshold: false }
  missing_google_profile:{ weight: 10, threshold: false }

consulting:
  operational_gaps:      { weight: 35 }
  fast_growth:           { weight: 30 }
  new_management:        { weight: 20 }
  manual_processes:      { weight: 15 }

digital_maintenance:
  outdated_systems:      { weight: 40 }
  performance_issues:    { weight: 30 }
  no_monitoring:         { weight: 20 }
  security_gaps:         { weight: 10 }

thresholds:
  min_score_to_qualify: 65
```

---

## `config/pricing.yaml` — range prezzi per servizio

Usato dal Proposal Agent per generare la proposta commerciale.
`billing` indica la struttura di pagamento milestone (es. 30% anticipo / 60% consegna / 10% saldo).

```yaml
consulting:
  min_eur: 2000
  max_eur: 8000
  default_eur: 4000
  billing: "30/60/10"
  deposit_pct: 30

web_design:
  min_eur: 1500
  max_eur: 6000
  default_eur: 3000
  billing: "30/60/10"
  deposit_pct: 30

digital_maintenance:
  one_time:
    min_eur: 500
    max_eur: 2000
    default_eur: 1000
  monthly:
    min_eur: 150
    max_eur: 600
    default_eur: 300
  billing: "30/60/10_or_monthly"
  deposit_pct: 30
```

---

## `config/external_apis.yaml` — integrazioni esterne

### Schema generale

```yaml
fatture_in_cloud:
  base_url: "https://api.fattureincloud.it/v2"
  company_id_env: FATTURE_IN_CLOUD_COMPANY_ID
  token_env: FATTURE_IN_CLOUD_ACCESS_TOKEN
  endpoints:
    create_invoice: "POST /c/{company_id}/issued_documents"
    get_invoice:    "GET  /c/{company_id}/issued_documents/{id}"
  invoice_type:
    b2b: "b"
    b2c: "c"
```

### Wrapper Python — Fatture in Cloud

Il Billing Agent usa questo wrapper. Non chiamare le API direttamente.

```python
# src/tools/fatture_in_cloud.py
import os
import httpx
from typing import Any

_BASE_URL = "https://api.fattureincloud.it/v2"
_COMPANY_ID = os.environ["FATTURE_IN_CLOUD_COMPANY_ID"]
_TOKEN = os.environ["FATTURE_IN_CLOUD_ACCESS_TOKEN"]

_HEADERS = {
    "Authorization": f"Bearer {_TOKEN}",
    "Content-Type": "application/json",
}

async def create_invoice(data: dict[str, Any]) -> dict[str, Any]:
    """
    Crea una fattura emessa su Fatture in Cloud.

    data: dict con campi FIC (type, entity, items_list, payment_method, ...)
    Restituisce il documento creato (include id, url, attachment_url).
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{_BASE_URL}/c/{_COMPANY_ID}/issued_documents",
            headers=_HEADERS,
            json={"data": data},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()["data"]

async def get_invoice(invoice_id: int) -> dict[str, Any]:
    """Recupera una fattura esistente per ID FIC."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{_BASE_URL}/c/{_COMPANY_ID}/issued_documents/{invoice_id}",
            headers=_HEADERS,
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()["data"]
```

**Variabili d'ambiente richieste** (aggiungere a `.env`):
```
FATTURE_IN_CLOUD_COMPANY_ID=123456
FATTURE_IN_CLOUD_ACCESS_TOKEN=your_token_here
```
