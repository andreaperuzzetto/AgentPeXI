# C.1 — Product Ladder + Audience Specificity: Design Spec
**Data:** 2026-05-07
**Branch:** `feature/blocco-c`
**Scope:** ResearchAgent — `_single_niche_research` upgrade a 3 LLM calls sequenziali con Product Ladder completo

---

## Contesto

`_single_niche_research` oggi esegue 1 LLM call (Haiku) e produce un report con analisi
nicchia + 13 tag + selling signals. Con C.1 diventa la fonte di **cluster intelligence**:
ogni nicchia analizzata produce un Product Ladder completo (tripwire → core → bundle_blueprint)
e metadati di audience specificity (`audience_target`, `ai_producibility`) necessari per C.2.

---

## Decisioni di design

| Decisione | Scelta | Motivazione |
|---|---|---|
| N. LLM calls per ladder | **3 sequenziali** | Ogni call usa l'output reale della precedente — qualità superiore a 1 call unica che ragiona su ipotesi |
| expansion_potential < 10 | **Discard silenzioso** | confidence ≈ 0.0, flow continua con altri candidati. No Telegram alert (rumore inutile) |
| Schema cache v3 | **Invalida immediatamente** | Tutti i cache v2 diventano stale al deploy. Re-warmup entro 7 giorni (~€1.68 max) |
| Bundle pending | **Indefinito + reminder 48h** | Scheduler ogni 48h re-invia Telegram se `status='pending_approval'` |

---

## Architettura: data flow completo

```
_single_niche_research(task, niche, product_tier="core", core_context=None)
│
├── Step 0   — Cache check ChromaDB (schema_version="3" obbligatorio)
├── Step 0b  — Failure analysis ChromaDB
├── Step 0c  — Finance context
├── Step 0d  — Shared context
├── Step 1   — Tavily parallel (search + competitors + keywords + trends)
├── Step 2   — Track data_sources
│
├── Step 3a  — [CORE] Haiku LLM call
│               → output core: nicchia completa con audience_target,
│                 expansion_potential, ai_producibility, pricing, tags…
│
├── Step 3b  — [TRIPWIRE] Haiku call con core_context
│               → ladder.tripwire: {title, description, price_usd≤2.50, format, value_prop}
│
├── Step 3c  — [BUNDLE] Haiku call con core+tripwire context
│               → ladder.bundle_blueprint: {title, description, price_usd, items_included, value_prop}
│
├── Step 4   — Parse+validate: merge core output + ladder (tripwire + bundle)
├── Step 4b  — requires_human_review: True se ai_producibility.score == "low"
├── Step 4c  — Bundle Telegram notification (inline keyboard: bundle_approve/bundle_decline)
│               solo se ladder.bundle_blueprint presente
│
├── Step 5   — Confidence scoring (pesi v3, vedi sotto)
├── Step 5b  — expansion_potential gate: < 10 → completeness = 0.0 (silenzioso)
│
├── Step 6   — Confidence gate < 0.60 → _refine_low_confidence_research
└── Step 7   — Confidence gate < 0.50 → FAILED
```

---

## Schema SYSTEM_PROMPT v3

Aggiunge al blocco per-niche (dopo `failure_analysis_applied`):

```json
"ai_producibility": {
  "score": "high|medium|low",
  "reasoning": "perché è producibile o non producibile con AI",
  "risk_factors": ["fattore rischio 1", "fattore rischio 2"]
},
"ladder": {
  "tripwire": {
    "title": "titolo prodotto tripwire",
    "description": "descrizione — cosa contiene esattamente",
    "price_usd": 1.50,
    "format": "1-page printable PDF",
    "value_prop": "perché comprare questo prima del core"
  },
  "core": {
    "title": "titolo prodotto core",
    "description": "descrizione specifica",
    "price_usd": 4.99,
    "format": "multi-page printable PDF",
    "value_prop": "il prodotto principale"
  },
  "bundle_blueprint": {
    "title": "titolo bundle",
    "description": "cosa contiene (tripwire + core + extra)",
    "price_usd": 8.99,
    "items_included": ["item 1", "item 2", "item 3"],
    "value_prop": "perché comprare il bundle"
  }
}
```

**Regole nel prompt:**
- `tripwire.price_usd` ≤ 2.50 (vincolo assoluto)
- `ladder.core.price_usd` == `pricing.conversion_sweet_spot_usd` (coerenza)
- `bundle_blueprint` è un blueprint pre-ricercato ≠ `BundleStrategy` (Blocco D)
- `ai_producibility.score="low"` → prodotto altamente specializzato, non autonomo

`RESEARCH_SCHEMA_VERSION = "3"` → invalida tutti i cache v2 al momento del deploy.

---

## Scoring ribilanciato (Part 2 — 0.50 totale)

| Check | Peso v3 | Peso v2 | Δ |
|---|---|---|---|
| 13 tag presenti | 0.15 | 0.15 | = |
| selling_signals completi | **0.10** | 0.15 | -0.05 |
| pricing (sweet_spot + launch) | 0.10 | 0.10 | = |
| seasonality (peak_months + timing) | **0.02** | 0.05 | -0.03 |
| audience_target presente (>10 chars) | **+0.08** | (solo cap) | nuovo |
| expansion_potential ≥ 20 | **+0.05** | — | nuovo boost |
| expansion_potential < 10 | → `viable=False` (discard) | — | nuovo gate |
| **Totale massimo** | **0.50** | 0.50 | = |

**Cap esistente mantenuto:** `audience_target` assente → `score = min(score, 0.40)`

---

## Comportamento `requires_human_review`

```python
for niche_data in output.get("niches", []):
    ai_prod = niche_data.get("ai_producibility", {})
    niche_data["requires_human_review"] = (ai_prod.get("score") == "low")
    if niche_data["requires_human_review"]:
        await self._notify_telegram(
            f"⚠️ Revisione umana richiesta: '{niche_data['name']}'\n"
            f"Motivo: {ai_prod.get('reasoning', '')}\n"
            f"Fattori: {', '.join(ai_prod.get('risk_factors', []))}"
        )
```

Il listing con `requires_human_review=True` entra in `production_queue` con
`status='pending_human_review'` invece di `status='pending'`. Non procede
automaticamente alla pipeline Design Agent.

---

## Telegram: bundle approval flow

**Invio** (dopo Step 3c, se `ladder.bundle_blueprint` presente):
```
📦 Bundle blueprint pronto: 'ADHD Planner'
Bundle: [title] @ $8.99
Items: [item1, item2, item3]
Approvare il bundle blueprint?
[✅ Approva]  [❌ Declina]
```

**Handler callback:**
- `bundle_approve:{cluster_id}` → ChromaDB: `{type: "bundle_approval", status: "approved"}`
- `bundle_decline:{cluster_id}` → ChromaDB: `{type: "bundle_approval", status: "declined"}`

**Reminder 48h:** scheduler job query `production_queue` per `status='pending_approval'` +
`release_order=6` (bundle) + `updated_at < now - 48h` → re-invia messaggio Telegram.

---

## Error handling & edge cases

| Scenario | Comportamento |
|---|---|
| Tripwire LLM call fallisce | `ladder.tripwire` assente nel merge; log warning; flow continua |
| Bundle LLM call fallisce | `ladder.bundle_blueprint` assente; nessun Telegram notification; log warning |
| `ai_producibility` assente nel JSON | `requires_human_review = False` (default safe) |
| `expansion_potential` = stringa ("high", "medium") | `int()` conversion fail → `expansion_int = 0` → discard silenzioso |
| `expansion_potential` = `None` | Trattato come 0 → discard silenzioso |
| Cache v2 esistente | `_is_cache_valid()` ritorna False (schema_version != "3") → re-fetch |
| Bundle pending 48h+ | Scheduler re-invia Telegram con stessi inline buttons |
| `requires_human_review=True` | Listing in `status='pending_human_review'` — NON procede automaticamente |

---

## `ProductionQueueItem` — campo product_tier

**Aggiunta al dataclass** (dopo `etsy_listing_id`):
```python
product_tier: str  # "tripwire" | "core" | "core_premium" | "bundle"
```

**Validazione application-level** in `create_item()` (SQLite non supporta ALTER + CHECK):
```python
_VALID_PRODUCT_TIERS = {"tripwire", "core", "core_premium", "bundle"}
if product_tier not in _VALID_PRODUCT_TIERS:
    raise ValueError(f"product_tier '{product_tier}' non valido.")
```

**`from_row`**: `product_tier = d.get("product_tier") or "core"`

---

## File da modificare

| File | Cambiamento |
|---|---|
| `apps/backend/agents/_research/prompts.py` | Schema v3: `ladder` + `ai_producibility`; `RESEARCH_SCHEMA_VERSION = "3"` |
| `apps/backend/agents/_research/analysis_mixin.py` | 3 LLM calls, `requires_human_review`, bundle Telegram |
| `apps/backend/agents/_research/scoring_mixin.py` | Pesi ribilanciati, expansion_potential gate |
| `apps/backend/core/production_queue.py` | `product_tier` field + validazione |
| `apps/backend/api/routers/telegram.py` | Handler `bundle_approve:` + `bundle_decline:` callbacks |

---

## Test coverage obbligatorio (TDD — da scrivere prima del codice)

```
test_c1_product_ladder.py — 15 test minimi:
1.  RESEARCH_SCHEMA_VERSION == "3"
2.  SYSTEM_PROMPT contiene "ladder", "tripwire", "bundle_blueprint", "ai_producibility"
3.  expansion_potential < 10 → niche.viable = False (discard, no impact on other niches)
4.  expansion_potential ≥ 20 → boost +0.05
5.  expansion_potential = 15 → no boost, no scarto
6.  expansion_potential = None → discard silenzioso
7.  audience_target presente (>10 chars) → +0.08
8.  selling_signals pieno → contribuisce max 0.10 (non 0.15)
9.  seasonality piena → contribuisce max 0.02 (non 0.05)
10. requires_human_review=True quando ai_producibility.score="low"
11. requires_human_review=False quando ai_producibility.score="high"
12. product_tier="invalid" in create_item → ValueError
13. product_tier="tripwire" in create_item → ok
14. ProductionQueueItem.from_row con product_tier presente
15. ProductionQueueItem.from_row con product_tier assente → default "core"
```
