# BLOCCO C — Product Intelligence
**Branch:** `feature/blocco-c`
**Worktree:** `.worktrees/feature/blocco-c`
**Data:** 2026-05-07
**Dipendenze:** BLOCCO A ✅ BLOCCO B ✅ (840 test passing su main)

---

## Problema e approccio

Il Research Agent oggi produce 1 listing per nicchia. BLOCCO C lo trasforma in una
**macchina da cluster**: per ogni nicchia vincente genera 5-6 listing audience-specific
con Product Ladder completo (tripwire → core → bundle), analisi competitiva a livello
shop (non listing), e cross-referencing automatico tra listing dello stesso cluster.

**Sequenza lineare obbligatoria:** C.1 → C.2 → C.3 → C.4

---

## Stato corrente — cosa è GIÀ implementato

| Componente | File | Stato |
|---|---|---|
| `_DISCOVERY_CATEGORIES_BY_SECTION` | `_research/discovery_mixin.py:32` | ✅ già presente |
| `audience_target` nel SYSTEM_PROMPT | `_research/prompts.py:33` | ✅ già presente |
| `expansion_potential` nel SYSTEM_PROMPT | `_research/prompts.py:34` | ✅ già presente |
| `RESEARCH_SCHEMA_VERSION = "2"` | `_research/prompts.py:3` | ✅ presente (diventa "3" in C.1) |
| `audience_target` cap a 0.40 in scoring | `_research/scoring_mixin.py:132-144` | ✅ già presente |
| `product_tier` colonna DB (prep A.0) | `core/_memory/_base.py:560` | ✅ colonna senza CHECK |
| `etsy_listing_id` in `ProductionQueueItem` | `core/production_queue.py:80` | ✅ già presente |
| `product_tier` badge in `ProductionPipeline.tsx` | `apps/frontend/.../ProductionPipeline.tsx:67` | ✅ già presente |
| `audience_target` + `expansion_potential` in `NicheTable.tsx` | `apps/frontend/.../NicheTable.tsx:43-216` | ✅ già presente |

**Tutto il resto va implementato da zero.**

---

## Skill protocol obbligatorio

- `brainstorming` → **prima di C.1** (cambiamento più rischioso: 3 LLM calls sequenziali in `_single_niche_research`)
- `writing-plans` → micro-plan per ciascun sub-blocco C.1, C.2, C.3, C.4
- `context-engineering-collection` → C.1 (context partitioning core→tripwire→bundle), C.2 (ChromaDB cluster graph), C.3 (cache retrieval)
- `dispatching-parallel-agents` → C.3 (4 sezioni analisi shop in parallelo), C.4 (PATCH multi-listing)
- `test-driven-development` → obbligatorio per C.1, C.2, C.3
- `production-code-audit` → dopo C.1 completato, prima di iniziare C.2
- `verification-before-completion` → alla fine: tutti i criteri E2E di §A.3

---

## C.1 — Product Ladder + Audience Specificity

### Obiettivo
`_single_niche_research` passa da 1 LLM call a 3 call sequenziali
(core → tripwire → bundle_blueprint). Output arricchito con `ladder` dict
e `ai_producibility`. Scoring ribilanciato.

### File da toccare

| File | Cambiamento |
|---|---|
| `apps/backend/agents/_research/prompts.py` | Aggiunge `ladder` dict + `ai_producibility` allo schema; bump a v3 |
| `apps/backend/agents/_research/analysis_mixin.py` | 3 LLM call sequenziali; `requires_human_review` flag |
| `apps/backend/agents/_research/scoring_mixin.py` | `expansion_potential` integer check; pesi ribilanciati |
| `apps/backend/core/_memory/_base.py` | Validazione `product_tier` a livello app (SQLite non supporta ALTER + CHECK) |

### C.1.1 — prompts.py: schema v3

**Incrementa** `RESEARCH_SCHEMA_VERSION = "3"`

**Aggiunge** al blocco per-niche nello `SCHEMA OBBLIGATORIO` (dopo `failure_analysis_applied`):

```python
      "ai_producibility": {
        "score": "high|medium|low",
        "reasoning": "perché è producibile o non producibile con AI",
        "risk_factors": ["fattore rischio 1", "fattore rischio 2"]
      },
      "ladder": {
        "tripwire": {
          "title": "titolo prodotto tripwire",
          "description": "descrizione specifica — cosa contiene",
          "price_usd": 1.50,
          "format": "1-page printable PDF",
          "value_prop": "perché compra questo prima del core"
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
          "description": "cosa contiene il bundle (tripwire + core + extra)",
          "price_usd": 8.99,
          "items_included": ["item 1", "item 2", "item 3"],
          "value_prop": "perché comprare il bundle anziché i singoli"
        }
      }
```

**Aggiunge** alle istruzioni del prompt (dopo il blocco failure analysis):
```
ai_producibility:
- score="high": formato standard (PDF multipagina testi/grafici), Design Agent autonomo
- score="medium": richiede template customizzato, supervisione raccomandata
- score="low": contenuto altamente specializzato (SVG complesso, illustrazioni custom) → richiede revisione umana

ladder:
- tripwire.price_usd ≤ 2.50 (entry point psicologico — mai sopra)
- bundle_blueprint NON è il BundleStrategy (Blocco D). È un blueprint pre-ricercato per futura esecuzione.
- ladder.core.price_usd deve coincidere con pricing.conversion_sweet_spot_usd
```

### C.1.2 — analysis_mixin.py: 3 LLM calls sequenziali

**Signature nuova:**
```python
async def _single_niche_research(
    self,
    task: AgentTask,
    niche: str,
    product_tier: str = "core",
    core_context: dict | None = None,
) -> AgentResult:
```

**Quando `product_tier == "core"` (chiamata principale):**

Dopo Step 3 (LLM analysis core) e Step 4 (parse+validate):

```python
# Step 3b — Tripwire call (usa core output come context)
tripwire_analysis = await self._call_llm(
    messages=[{
        "role": "user",
        "content": (
            f"Basandoti sull'analisi core per '{niche}' qui sotto, "
            f"progetta il prodotto TRIPWIRE (entry-point, max $2.50).\n\n"
            f"## Core analysis\n{json.dumps(core_output, indent=2)}\n\n"
            f"Produce JSON con: ladder.tripwire (title, description, price_usd≤2.50, "
            f"format, value_prop) e nessun altro campo."
        ),
    }],
    system_prompt=SYSTEM_PROMPT,
    model=MODEL_HAIKU,
)

# Step 3c — Bundle blueprint call (usa core output come context)
bundle_analysis = await self._call_llm(
    messages=[{
        "role": "user",
        "content": (
            f"Basandoti sull'analisi core per '{niche}' qui sotto, "
            f"progetta il BUNDLE BLUEPRINT (tripwire + core + extra).\n\n"
            f"## Core analysis\n{json.dumps(core_output, indent=2)}\n\n"
            f"## Tripwire\n{json.dumps(tripwire_output, indent=2)}\n\n"
            f"Produce JSON con: ladder.bundle_blueprint (title, description, "
            f"price_usd, items_included, value_prop) e nessun altro campo."
        ),
    }],
    system_prompt=SYSTEM_PROMPT,
    model=MODEL_HAIKU,
)
```

**Merge:** unisci `tripwire_analysis` e `bundle_analysis` nel `output["niches"][0]["ladder"]`

**`requires_human_review` flag:**
```python
for niche_data in output.get("niches", []):
    producibility = niche_data.get("ai_producibility", {})
    if producibility.get("score") == "low":
        niche_data["requires_human_review"] = True
    else:
        niche_data["requires_human_review"] = False
```

**Routing listing a manual queue** (quando `requires_human_review: true`):
Dopo confidence gate, prima di `return AgentResult(COMPLETED)`:
```python
for niche_data in output.get("niches", []):
    if niche_data.get("requires_human_review"):
        await self._notify_telegram(
            f"⚠️ Revisione umana richiesta per '{niche_data['name']}'\n"
            f"Motivo ai_producibility: {niche_data.get('ai_producibility', {}).get('reasoning', '')}\n"
            f"Fattori di rischio: {', '.join(niche_data.get('ai_producibility', {}).get('risk_factors', []))}"
        )
```

**Bundle approval Telegram inline buttons** (dopo il tripwire+bundle merge):
```python
# invia Telegram con inline buttons per bundle approval
cluster_id = _compute_cluster_id(niche)  # sha256[:12] (importato da research.py)
await self._notify_telegram(
    f"📦 Bundle blueprint pronto per '{niche}'\n"
    f"Bundle: {bundle_title} @ ${bundle_price}\n"
    f"Approvare il bundle blueprint?",
    inline_keyboard=[
        [
            {"text": "✅ Approva", "callback_data": f"bundle_approve:{cluster_id}"},
            {"text": "❌ Declina", "callback_data": f"bundle_decline:{cluster_id}"},
        ]
    ],
)
```

> Nota: `_notify_telegram` va esteso per supportare `inline_keyboard` param (usa
> `send_message` con `reply_markup=InlineKeyboardMarkup`). Aggiunge handler per
> `bundle_approve:{cluster_id}` e `bundle_decline:{cluster_id}` nel Telegram router
> (vedi C.1.5).

### C.1.3 — scoring_mixin.py: pesi ribilanciati + expansion_potential gate

**Parte 2 ribilanciata** (target totale: 0.50):

| Check | Peso corrente | Peso C.1 |
|---|---|---|
| 13 tag (pieno) | 0.15 | 0.15 (invariato) |
| selling_signals (pieno) | 0.15 | 0.10 (-0.05) |
| pricing (conversion_sweet_spot + launch) | 0.10 | 0.10 (invariato) |
| seasonality (peak_months + timing) | 0.05 | 0.02 (-0.03) |
| **audience_target** (peso dedicato) | (solo cap) | **+0.08** (nuovo) |
| **expansion_potential** (boost) | — | **+0.05** (nuovo, se ≥ 20) |

**Codice da aggiungere in `_calculate_confidence` (dopo seasonality check):**

```python
# audience_target dedicated weight (C.1) — peso 0.08
audience_target = sample.get("audience_target", "")
if isinstance(audience_target, str) and len(audience_target.strip()) > 10:
    completeness += 0.08
else:
    sample_missing.append("audience_target mancante o troppo generico (< 10 chars)")

# expansion_potential gate (C.1) — hard min 10, soft boost +0.05 if ≥ 20
expansion = sample.get("expansion_potential")
try:
    expansion_int = int(expansion) if expansion is not None else 0
except (ValueError, TypeError):
    expansion_int = 0

if expansion_int < 10:
    # Nicchia scartata — confidence cap a 0.0 per questa nicchia
    sample_missing.append(
        f"expansion_potential={expansion_int} sotto hard minimum 10 — nicchia scartata"
    )
    completeness = 0.0
elif expansion_int >= 20:
    completeness += 0.05  # soft target boost
```

**Cap `audience_target` già esistente rimane** (linea 142-144 di scoring_mixin.py):
```python
if worst_audience_capped:
    score = min(score, 0.40)
```

**Selling signals max** ridotto da 0.15 a 0.10:
```python
# Era:
if selling_complete:
    completeness += 0.15  # → diventa 0.10
elif selling:
    completeness += 0.07  # → diventa 0.05 (proporzionale)
```

**Seasonality max** ridotto da 0.05 a 0.02:
```python
# Era:
if peak_months and timing_advice:
    completeness += 0.05  # → diventa 0.02
else:
    completeness += 0.01  # → invariato
```

### C.1.4 — _base.py: product_tier validation (application-level)

SQLite non supporta `ALTER TABLE ... ADD CONSTRAINT`. Il CHECK su `product_tier`
va fatto a livello Python in `ProductionQueueService.create_item()`:

```python
# In create_item(), validazione esplicita:
_VALID_PRODUCT_TIERS = {"tripwire", "core", "core_premium", "bundle"}
if product_tier not in _VALID_PRODUCT_TIERS:
    raise ValueError(
        f"product_tier '{product_tier}' non valido. "
        f"Valori accettati: {sorted(_VALID_PRODUCT_TIERS)}"
    )
```

Non serve nuova migration DB per il CHECK — la colonna esiste già (A.0).
Aggiungere `product_tier: str = "core"` a `ProductionQueueItem` dataclass
e `from_row` (d.get("product_tier") or "core").

### C.1.5 — Telegram: inline buttons bundle approve/decline

**In `apps/backend/api/routers/telegram.py`** (o equivalente handler callback):

```python
# Handler per bundle_approve:{cluster_id} e bundle_decline:{cluster_id}
if callback_data.startswith("bundle_approve:"):
    cluster_id = callback_data.split(":", 1)[1]
    # salva in ChromaDB: tipo "bundle_approval", cluster_id, status="approved"
    await memory.add_to_chromadb(
        document=f"Bundle approved for cluster {cluster_id}",
        metadata={"type": "bundle_approval", "cluster_id": cluster_id, "status": "approved"},
    )
    await bot.answer_callback_query(callback_query_id, "Bundle approvato ✅")
    await bot.edit_message_text("✅ Bundle blueprint approvato.", ...)

elif callback_data.startswith("bundle_decline:"):
    cluster_id = callback_data.split(":", 1)[1]
    await memory.add_to_chromadb(
        document=f"Bundle declined for cluster {cluster_id}",
        metadata={"type": "bundle_approval", "cluster_id": cluster_id, "status": "declined"},
    )
    await bot.answer_callback_query(callback_query_id, "Bundle rifiutato")
    await bot.edit_message_text("❌ Bundle blueprint rifiutato.", ...)
```

### C.1 — TDD: test file

**`tests/test_c1_product_ladder.py`** (da scrivere prima del codice):

```python
# Test 1: prompts.py — RESEARCH_SCHEMA_VERSION = "3"
# Test 2: prompts.py — SYSTEM_PROMPT contiene "ladder", "tripwire", "bundle_blueprint", "ai_producibility"
# Test 3: scoring — expansion_potential < 10 → completeness = 0.0 (nicchia scartata)
# Test 4: scoring — expansion_potential ≥ 20 → boost +0.05
# Test 5: scoring — expansion_potential = 15 → no boost, no scarto
# Test 6: scoring — audience_target presente (>10 chars) → +0.08
# Test 7: scoring — selling_signals pieno → max 0.10 (non 0.15)
# Test 8: scoring — seasonality piena → max 0.02 (non 0.05)
# Test 9: analysis_mixin — requires_human_review=True quando ai_producibility.score="low"
# Test 10: analysis_mixin — requires_human_review=False quando ai_producibility.score="high"
# Test 11: production_queue — product_tier="invalid" → ValueError
# Test 12: production_queue — product_tier="tripwire" → accettato
# Test 13: production_queue — ProductionQueueItem.from_row con product_tier
# Test 14: ladder.tripwire.price_usd assertion (schema: deve essere ≤ 2.50)
# Test 15: ladder.core.price_usd == pricing.conversion_sweet_spot_usd (coerenza)
```

---

## C.2 — Cluster Strategy

### Obiettivo
Quando `_autonomous_discovery` trova un vincitore con confidence ≥ 0.75,
costruisce automaticamente un cluster di 5-6 listing (1 tripwire + 1 core +
3 variazioni core + 1 bundle_blueprint) con `release_order` 1-6 e
`cluster_id` deterministico.

### File da toccare

| File | Cambiamento |
|---|---|
| `apps/backend/agents/research.py` | Nuovi metodi `_build_cluster`, `_generate_core_variations`; modifica `_autonomous_discovery` |
| `apps/backend/core/_memory/_base.py` | Migrations C.2: `cluster_id`, `release_order`; C.4: `etsy_listing_url` |
| `apps/backend/core/production_queue.py` | `ProductionQueueItem` esteso; nuovi metodi `create_cluster_items`, `get_cluster_items`, `get_next_releasable` |

### C.2.1 — cluster_id deterministico

**Funzione helper** (usata sia in research.py che in analysis_mixin.py per i bundle buttons):

```python
# in research.py (o _research/utils.py se si vuole estrarlo)
import hashlib

def _compute_cluster_id(niche: str) -> str:
    """cluster_id deterministico: sha256(niche.lower().strip())[:12]"""
    return hashlib.sha256(niche.lower().strip().encode()).hexdigest()[:12]
```

### C.2.2 — research.py: `_build_cluster` e `_generate_core_variations`

```python
async def _build_cluster(
    self,
    winner_niche: str,
    section_key: str,
    core_result: AgentResult,
) -> list[dict]:
    """
    Costruisce cluster di 5-6 listing da un vincitore:
      - position 1: tripwire (da ladder.tripwire)
      - position 2: core (da analisi principale)
      - positions 3-5: core variations (Haiku, _generate_core_variations)
      - position 6: bundle_blueprint (da ladder.bundle_blueprint, pending approval)

    Restituisce lista di dict pronti per create_cluster_items().
    """
    cluster_id = _compute_cluster_id(winner_niche)
    core_data = (core_result.output_data or {}).get("niches", [{}])[0]
    ladder = core_data.get("ladder", {})

    cluster_items = []

    # Item 1 — tripwire
    tripwire = ladder.get("tripwire", {})
    cluster_items.append({
        "niche": winner_niche,
        "product_tier": "tripwire",
        "release_order": 1,
        "cluster_id": cluster_id,
        "section_key": section_key,
        "design_brief": tripwire,
        "product_type": core_data.get("recommended_product_type", "printable_pdf"),
    })

    # Item 2 — core
    cluster_items.append({
        "niche": winner_niche,
        "product_tier": "core",
        "release_order": 2,
        "cluster_id": cluster_id,
        "section_key": section_key,
        "design_brief": core_data,
        "product_type": core_data.get("recommended_product_type", "printable_pdf"),
    })

    # Items 3-5 — core variations (Haiku)
    variations = await self._generate_core_variations(winner_niche, core_data, n=3)
    for i, var in enumerate(variations, start=3):
        cluster_items.append({
            "niche": winner_niche,
            "product_tier": "core",
            "release_order": i,
            "cluster_id": cluster_id,
            "section_key": section_key,
            "design_brief": var,
            "product_type": core_data.get("recommended_product_type", "printable_pdf"),
        })

    # Item 6 — bundle_blueprint (pending Telegram approval)
    bundle_bp = ladder.get("bundle_blueprint", {})
    cluster_items.append({
        "niche": winner_niche,
        "product_tier": "bundle",
        "release_order": 6,
        "cluster_id": cluster_id,
        "section_key": section_key,
        "design_brief": bundle_bp,
        "product_type": core_data.get("recommended_product_type", "printable_pdf"),
        "requires_approval": True,  # non entra in queue finché non approvato
    })

    return cluster_items
```

```python
async def _generate_core_variations(
    self,
    niche: str,
    core_spec: dict,
    n: int = 3,
) -> list[dict]:
    """
    Genera N variazioni adaptive del core listing usando Haiku.
    Tipi: STYLE (visual style), AUDIENCE (buyer persona), FORMAT (content format).
    Constraint: max 2 variazioni dello stesso tipo.
    """
    prompt = (
        f"Genera {n} variazioni del listing core per '{niche}'.\n\n"
        f"## Core spec\n{json.dumps(core_spec, indent=2)}\n\n"
        f"Per ogni variazione specifica:\n"
        f"- variation_type: STYLE | AUDIENCE | FORMAT\n"
        f"- title: titolo variante\n"
        f"- audience_target: buyer persona differente dal core\n"
        f"- key_difference: cosa cambia rispetto al core\n"
        f"- etsy_tags_13: 13 tag specifici per questa variante\n\n"
        f"Constraint: max 2 variazioni dello stesso variation_type.\n"
        f"Produce JSON: {{\"variations\": [...]}}"
    )

    response = await self._call_llm(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=SYSTEM_PROMPT,
        model=MODEL_HAIKU,
    )

    parsed = await self._parse_and_validate(response, SYSTEM_PROMPT)
    variations = (parsed or {}).get("variations", [])

    # Enforce max 2 per type
    type_counts: dict[str, int] = {}
    filtered = []
    for var in variations:
        vt = var.get("variation_type", "STYLE")
        if type_counts.get(vt, 0) < 2:
            filtered.append(var)
            type_counts[vt] = type_counts.get(vt, 0) + 1
        if len(filtered) == n:
            break

    return filtered[:n]
```

### C.2.3 — `_autonomous_discovery`: trigger cluster post-winner

**Alla fine del metodo `_autonomous_discovery`**, dopo la selezione del vincitore
(quando `confidence ≥ 0.75`), aggiungere:

```python
# C.2 — cluster build se confidence sufficiente
winner_confidence = output_data.get("winner", {}).get("confidence", 0.0)
winner_niche = output_data.get("winner", {}).get("niche", "")
winner_section = output_data.get("winner", {}).get("section_key", "")

if winner_confidence >= 0.75 and winner_niche:
    try:
        cluster_items = await self._build_cluster(
            winner_niche=winner_niche,
            section_key=winner_section,
            core_result=winner_result,
        )
        output_data["cluster"] = cluster_items
        output_data["cluster_id"] = _compute_cluster_id(winner_niche)
        await self._notify_telegram(
            f"🔗 Cluster '{winner_niche}' creato\n"
            f"📦 {len(cluster_items)} listing · cluster_id: {_compute_cluster_id(winner_niche)}\n"
            f"🚦 Ordine: tripwire→core→3 variazioni→bundle"
        )
    except Exception as _cluster_err:
        logger.warning("research: _build_cluster fallito per '%s': %s", winner_niche, _cluster_err)
        # Non blocca il flow — cluster opzionale
```

**Section 0-viable handling:**
Quando `_mine_opportunity_candidates` ritorna 0 candidati validi per una sezione:

```python
# In _mine_opportunity_candidates(), dopo dedup:
if not deduped:
    section_name = self._current_section_key or "unknown"
    await self._notify_telegram(
        f"⚠️ Sezione '{section_name}' — 0 nicchie viable trovate\n"
        f"Azione richiesta:",
        inline_keyboard=[
            [
                {"text": "🔄 Retry", "callback_data": f"section_retry:{section_name}"},
                {"text": "⏭ Skip", "callback_data": f"section_skip:{section_name}"},
                {"text": "🔀 Sostituisci", "callback_data": f"section_replace:{section_name}"},
            ]
        ],
    )
```

Aggiungere handlers Telegram per `section_retry:`, `section_skip:`, `section_replace:`
in `apps/backend/api/routers/telegram.py`.

### C.2.4 — DB migrations: cluster_id + release_order + etsy_listing_url

**In `apps/backend/core/_memory/_base.py`**, aggiungere al blocco migrations:

```python
# --- C.2: cluster columns ---
"ALTER TABLE production_queue ADD COLUMN cluster_id TEXT",
"ALTER TABLE production_queue ADD COLUMN release_order INTEGER NOT NULL DEFAULT 0",
# --- C.4: cross-ref columns ---
"ALTER TABLE production_queue ADD COLUMN etsy_listing_url TEXT",
# Indici per cluster queries
"CREATE INDEX IF NOT EXISTS idx_pq_cluster ON production_queue(cluster_id)",
"CREATE INDEX IF NOT EXISTS idx_pq_release_order ON production_queue(cluster_id, release_order)",
```

### C.2.5 — ProductionQueueItem + ProductionQueueService: cluster fields

**`ProductionQueueItem`** (in `core/production_queue.py`):
Aggiungere dopo `etsy_listing_id`:
```python
etsy_listing_url: str | None
cluster_id: str | None
release_order: int  # 0 = non in cluster; 1-6 = posizione nel cluster
```

Aggiungere nel `from_row`:
```python
etsy_listing_url=d.get("etsy_listing_url"),
cluster_id=d.get("cluster_id"),
release_order=d.get("release_order") or 0,
```

**`ProductionQueueService`** — nuovi metodi:

```python
async def create_cluster_items(
    self,
    cluster_items: list[dict],
) -> list[int]:
    """Inserisce tutti gli item di un cluster in production_queue.
    Item con requires_approval=True vengono inseriti con status='pending_approval'.
    Restituisce lista di item_id creati.
    """

async def get_cluster_items(
    self,
    cluster_id: str,
) -> list[ProductionQueueItem]:
    """Ritorna tutti gli item di un cluster ordinati per release_order."""

async def get_next_releasable(
    self,
    cluster_id: str,
) -> ProductionQueueItem | None:
    """
    Ritorna il prossimo item del cluster pronto per entrare in pipeline.
    Regola: release_order N può partire solo se release_order N-1 è 'completed'.
    release_order=1 (tripwire) parte immediatamente.
    """
    items = await self.get_cluster_items(cluster_id)
    completed_orders = {i.release_order for i in items if i.status == "completed"}

    for item in sorted(items, key=lambda x: x.release_order):
        if item.status != "pending":
            continue
        if item.release_order == 1:
            return item  # tripwire parte sempre
        if (item.release_order - 1) in completed_orders:
            return item
    return None
```

### C.2 — TDD: test file

**`tests/test_c2_cluster_strategy.py`** (da scrivere prima del codice):

```python
# Test 1: _compute_cluster_id("ADHD Planner") == _compute_cluster_id("adhd planner") (case-insensitive)
# Test 2: _compute_cluster_id("adhd planner") ha len 12
# Test 3: _generate_core_variations → 3 variazioni
# Test 4: _generate_core_variations → max 2 di stesso variation_type
# Test 5: _build_cluster → 6 item (1 tripwire + 1 core + 3 var + 1 bundle)
# Test 6: _build_cluster → release_order [1,2,3,4,5,6]
# Test 7: _build_cluster → item 6 ha requires_approval=True
# Test 8: get_next_releasable → order=1 sempre disponibile
# Test 9: get_next_releasable → order=3 NON disponibile se order=2 non è 'completed'
# Test 10: get_next_releasable → order=3 disponibile dopo order=2 'completed'
# Test 11: DB migration → colonne cluster_id, release_order presenti
# Test 12: ProductionQueueItem.from_row → cluster_id, release_order deserializzati
# Test 13: _autonomous_discovery confidence ≥ 0.75 → output_data["cluster"] presente
# Test 14: _autonomous_discovery confidence < 0.75 → output_data["cluster"] assente
```

---

## C.3 — Shop-level Competitive Analysis

### Obiettivo
Per ogni nicchia analizzata, estrarre i top seller a livello shop (non listing),
analizzare i loro pattern con Haiku, e produrre un `gap_to_exploit` aggregato
con ChromaDB cache 30 giorni.

### File da toccare

| File | Cambiamento |
|---|---|
| `apps/backend/agents/_market_data/_shop_analysis_mixin.py` | Nuovo file — logica C.3 completa |
| `apps/backend/agents/market_data.py` | Importa e assembla `_ShopAnalysisMixin` |
| `apps/backend/agents/_research/analysis_mixin.py` | Integra `_get_competitor_shop_analysis` dopo Step 3 |
| `apps/backend/api/routers/etsy.py` | Nuovo endpoint GET `/api/etsy/niches/{niche}/competitor-analysis` |
| `apps/frontend/src/components/etsy/NicheTable.tsx` | Colonna "Gap" colorata |

### C.3.1 — `_shop_analysis_mixin.py`: logica completa

```python
"""MarketDataAgent — shop-level competitive analysis mixin (C.3)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("agentpexi.market_data")


class _ShopAnalysisMixin:

    async def _get_competitor_shop_analysis(
        self,
        niche: str,
        section_key: str,
    ) -> dict | None:
        """
        Analisi competitiva a livello shop per una nicchia.

        Flow:
        1. Cache check ChromaDB (30 giorni) — se hit, ritorna cached
        2. Step 1 (free): estrae shop names dai top_sellers già in ChromaDB
        3. Step 2 (conditional): Tavily se < 3 shop unici trovati
        4. Step 3: Haiku analysis per max 5 shop → structured JSON
        5. Step 4: Sonnet synthesis → gap_to_exploit aggregato
        6. Salva in ChromaDB con cache 30gg
        """
        # Step 0 — Cache check
        cached = await self._memory.query_chromadb(
            query=f"competitor shop analysis {niche}",
            n_results=1,
            where={"type": "competitor_shop_analysis", "niche": niche},
        )
        if cached:
            meta = cached[0].get("metadata", {})
            cache_until_str = meta.get("cache_until", "")
            if cache_until_str:
                try:
                    cache_until = datetime.fromisoformat(cache_until_str)
                    if cache_until.tzinfo is None:
                        cache_until = cache_until.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) < cache_until:
                        logger.info("market_data: cache hit competitor_shop_analysis '%s'", niche)
                        return cached[0].get("document_json")  # ritorna dict salvato
                except (ValueError, TypeError):
                    pass

        # Step 1 — Estrai shop names da ChromaDB (top_sellers dai research reports)
        research_cached = await self._memory.query_chromadb(
            query=f"Research report per nicchia '{niche}'",
            n_results=1,
            where={"type": "research_report", "niche": niche},
        )
        shop_names: list[str] = []
        if research_cached:
            doc = research_cached[0].get("document_json", {})
            niches_list = doc.get("niches", [])
            for n_data in niches_list:
                for shop in n_data.get("competition", {}).get("top_sellers", []):
                    if shop and shop not in shop_names:
                        shop_names.append(shop)

        # Step 2 — Tavily se < 3 shop unici
        if len(shop_names) < 3:
            from apps.backend.tools import tavily as tavily_tool
            try:
                tavily_result = await tavily_tool.search(
                    query=f"etsy top sellers digital printables {niche} shop",
                    max_results=5,
                )
                # Estrai shop names da URL etsy.com/shop/ShopName
                import re
                urls = [r.get("url", "") for r in (tavily_result or {}).get("results", [])]
                for url in urls:
                    match = re.search(r"etsy\.com/shop/([^/?#]+)", url)
                    if match:
                        sname = match.group(1)
                        if sname not in shop_names:
                            shop_names.append(sname)
            except Exception as e:
                logger.warning("market_data: Tavily fallito per C.3 '%s': %s", niche, e)

        if not shop_names:
            return None

        shop_names = shop_names[:5]  # max 5 shop

        # Step 3 — Haiku analysis per ogni shop
        from apps.backend.core.config import MODEL_HAIKU
        shop_analyses: list[dict] = []
        for shop_name in shop_names:
            try:
                from apps.backend.tools import tavily as tavily_tool
                shop_data = await tavily_tool.search(
                    query=f"etsy shop {shop_name} digital products reviews listing",
                    max_results=3,
                )
                analysis = await self._call_haiku_shop_analysis(shop_name, shop_data, niche)
                if analysis:
                    shop_analyses.append(analysis)
            except Exception as e:
                logger.warning("market_data: shop analysis fallita per '%s': %s", shop_name, e)

        if not shop_analyses:
            return None

        # Step 4 — Sonnet cross-shop synthesis
        from apps.backend.core.config import MODEL_SONNET
        gap_to_exploit = await self._synthesize_shop_gaps(shop_analyses, niche)

        result = {
            "niche": niche,
            "section_key": section_key,
            "shops_analyzed": len(shop_analyses),
            "shops": shop_analyses,
            "gap_to_exploit": gap_to_exploit,
            "gap_summary": gap_to_exploit[:200] if gap_to_exploit else "",
        }

        # Step 5 — Salva in ChromaDB (cache 30gg)
        now = datetime.now(timezone.utc)
        cache_until = now + timedelta(days=30)
        await self._memory.add_to_chromadb(
            document=gap_to_exploit or f"Competitor shop analysis for {niche}",
            metadata={
                "type": "competitor_shop_analysis",
                "niche": niche,
                "section_key": section_key,
                "shops_analyzed": str(len(shop_analyses)),
                "gap_summary": result["gap_summary"],
                "created_at": now.isoformat(),
                "cache_until": cache_until.isoformat(),
            },
            document_json=result,  # salva anche JSON completo se MemoryManager supporta
        )

        return result

    async def _call_haiku_shop_analysis(
        self,
        shop_name: str,
        shop_data: dict,
        niche: str,
    ) -> dict | None:
        """Haiku analizza 1 shop → structured JSON."""
        # Nota: questo mixin non eredita da AgentBase direttamente — usa self._call_llm_raw
        # che chiama il client Anthropic direttamente (pattern già usato in _competitive_mixin.py)
        prompt = (
            f"Analizza questo shop Etsy: '{shop_name}' nel contesto della nicchia '{niche}'.\n\n"
            f"## Dati shop da web\n{str(shop_data)[:2000]}\n\n"
            f"Produce JSON valido con schema:\n"
            f'{{"shop_name": "", "estimated_listing_count": 0, '
            f'"primary_niches": [], "section_structure": "", '
            f'"estimated_aov_usd": 0.0, "audience_served": "", '
            f'"what_they_do_well": "", "what_they_dont_do": "", '
            f'"threat_level": "high|medium|low"}}'
        )
        try:
            from apps.backend.core.config import MODEL_HAIKU
            import anthropic
            # usa client diretto se disponibile, altrimenti mock
            # NOTA: implementazione dipende da come _competitive_mixin.py chiama il LLM
            # → adattare al pattern esistente
            return None  # placeholder — implementazione reale nel codice
        except Exception as e:
            logger.warning("market_data: Haiku shop analysis fallita: %s", e)
            return None

    async def _synthesize_shop_gaps(
        self,
        shop_analyses: list[dict],
        niche: str,
    ) -> str:
        """Sonnet cross-shop synthesis → gap_to_exploit aggregato."""
        prompt = (
            f"Analizza questi {len(shop_analyses)} shop Etsy competitor per '{niche}'.\n\n"
            f"## Shop analyses\n{str(shop_analyses)[:3000]}\n\n"
            f"Produce UN SOLO testo (max 300 chars) che descrive "
            f"il GAP principale non coperto dai competitor che PexiomStudio può sfruttare.\n"
            f"Esempio: 'Nessuno offre bundle audience-specific per mamme ADHD. "
            f"Gap price point €2-3 su tripwire entry. Opportunity: formato A5 non utilizzato.'"
        )
        try:
            # placeholder — usa pattern da _competitive_mixin.py
            return "Gap analysis non disponibile"
        except Exception as e:
            logger.warning("market_data: Sonnet synthesis fallita: %s", e)
            return ""
```

> **Nota implementazione:** I metodi `_call_llm` in `_ShopAnalysisMixin` devono usare
> il client LLM in modo consistente con `_CompetitiveMixin` già esistente.
> Verificare come `_competitive_mixin.py` chiama il LLM e replicare lo stesso pattern.

### C.3.2 — market_data.py: assembla il nuovo mixin

```python
from apps.backend.agents._market_data import (
    ...,
    _ShopAnalysisMixin,  # aggiungere all'import
)

class MarketDataAgent(
    ...,
    _ShopAnalysisMixin,  # aggiungere prima di _MockMixin
    _MockMixin,
    object,
):
    ...
```

Aggiornare anche `_market_data/__init__.py` per esportare `_ShopAnalysisMixin`.

### C.3.3 — analysis_mixin.py: integrazione post-Step 3

In `_single_niche_research`, dopo Step 4 (parse+validate), aggiungere:

```python
# Step 3c — Competitor shop analysis (C.3)
try:
    from apps.backend.agents.market_data import MarketDataAgent
    mock_mode = getattr(self.memory, "mock_mode", False)
    market_data_agent = MarketDataAgent(memory=self.memory, mock_mode=mock_mode)
    section_key = (task.input_data or {}).get("section_key", "")
    shop_analysis = await market_data_agent._get_competitor_shop_analysis(niche, section_key)
    if shop_analysis:
        output["competitor_shop_analysis"] = shop_analysis
        # Arricchisce gap_to_exploit nel primo viable niche
        for n_data in output.get("niches", []):
            if n_data.get("viable"):
                existing_gap = n_data.get("competition", {}).get("gap_to_exploit", "")
                shop_gap = shop_analysis.get("gap_summary", "")
                if shop_gap:
                    n_data.setdefault("competition", {})["gap_to_exploit"] = (
                        f"{existing_gap} [shop-level: {shop_gap}]"
                    ).strip()
                break
except Exception as _c3_err:
    logger.warning("research[%s]: C.3 shop analysis fallita: %s", niche, _c3_err)
    # Non blocca il flow — C.3 è enrichment opzionale
```

### C.3.4 — API endpoint: competitor-analysis

**In `apps/backend/api/routers/etsy.py`:**

```python
@router.get("/niches/{niche}/competitor-analysis")
async def get_niche_competitor_analysis(
    niche: str,
    memory: MemoryManager = Depends(get_memory),
):
    """Ritorna l'analisi shop-level per una nicchia (da ChromaDB cache)."""
    from apps.backend.agents.market_data import MarketDataAgent
    agent = MarketDataAgent(memory=memory)
    # Query ChromaDB direttamente (no re-fetch)
    cached = await memory.query_chromadb(
        query=f"competitor shop analysis {niche}",
        n_results=1,
        where={"type": "competitor_shop_analysis", "niche": niche},
    )
    if not cached:
        return {"available": False, "niche": niche}
    return {"available": True, "niche": niche, "analysis": cached[0].get("document_json")}
```

### C.3.5 — Frontend: NicheTable.tsx — colonna "Gap"

Dopo la colonna "Score" in `NicheTable.tsx`, aggiungere:

```tsx
{/* Gap column — C.3 */}
<th>Gap</th>
...
<td>
  {item.gap_to_exploit ? (
    <span
      title={item.gap_to_exploit}
      style={{
        background: item.gap_to_exploit.length > 20 ? '#d1fae5' : '#fef9c3',
        color: item.gap_to_exploit.length > 20 ? '#065f46' : '#713f12',
        borderRadius: 4,
        padding: '2px 6px',
        fontSize: 11,
        cursor: 'help',
        maxWidth: 120,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        display: 'inline-block',
        whiteSpace: 'nowrap',
      }}
    >
      {item.gap_to_exploit.slice(0, 25)}{item.gap_to_exploit.length > 25 ? '…' : ''}
    </span>
  ) : (
    <span style={{ color: '#9ca3af', fontSize: 11 }}>—</span>
  )}
</td>
```

**Score column border enhancement** (quando `competitor_shop_analysis` presente):
Aggiungere `borderLeft: item.has_competitor_analysis ? '3px solid #10b981' : '3px dashed #d1d5db'`
al container della colonna Score.

### C.3 — TDD: test file

**`tests/test_c3_shop_analysis.py`** (da scrivere prima del codice):

```python
# Test 1: cache hit — 0 Tavily calls quando analysis < 30 giorni
# Test 2: cache miss — Tavily chiamato
# Test 3: < 3 shop in top_sellers → Tavily chiamato (Step 2)
# Test 4: ≥ 3 shop in top_sellers → nessun Tavily extra (solo 1/shop)
# Test 5: output ha shop_analyses list con max 5 item
# Test 6: gap_to_exploit len > 0
# Test 7: ChromaDB storage — metadata type="competitor_shop_analysis"
# Test 8: ChromaDB storage — cache_until è ~30gg nel futuro
# Test 9: integration test — _single_niche_research output["competitor_shop_analysis"] presente
# Test 10: API endpoint /niches/{niche}/competitor-analysis — cached=True response
# Test 11: API endpoint — niche not found → {"available": false}
# Test 12: mock mode — Tavily calls skipped, nessun errore
```

---

## C.4 — Cross-referencing Automatico

### Obiettivo
Dopo ogni publish su Etsy, aggiornare la descrizione di tutti i listing dello stesso
cluster aggiungendo un blocco cross-reference in fondo (plain text, URLs Etsy cliccabili).

### File da toccare

| File | Cambiamento |
|---|---|
| `apps/backend/agents/_publisher/_crossref_mixin.py` | Nuovo file — logica C.4 completa |
| `apps/backend/agents/publisher.py` | Import mixin; trigger post-publish |
| `apps/backend/api/routers/etsy.py` | Nuovi endpoint GET `/api/etsy/clusters` e `/{cluster_id}` |
| `apps/frontend/src/components/etsy/ProductionPipeline.tsx` | Icona 🔗, cluster grouping |

### C.4.1 — `_crossref_mixin.py`: logica completa

```python
"""PublisherAgent — cross-reference update mixin (C.4)."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger("agentpexi.publisher")

_CROSSREF_SEPARATOR = "─" * 25


class _CrossrefMixin:

    async def _update_cluster_crossrefs(
        self,
        cluster_id: str,
        new_listing_id: str,
        new_listing_title: str,
        new_listing_url: str,
    ) -> None:
        """
        Aggiorna le descrizioni di tutti i listing pubblicati dello stesso cluster
        aggiungendo/sostituendo il blocco cross-reference.

        Gates:
        - cluster_id NOT NULL
        - ≥ 2 listing del cluster con status='completed' e etsy_listing_id NOT NULL
        - Non in mock mode (no real Etsy IDs disponibili)
        """
        # Gate: mock mode
        if getattr(self.memory, "mock_mode", False):
            logger.debug("publisher: cross-ref skip — mock mode")
            return

        # Fetch cluster items pubblicati
        from apps.backend.core.production_queue import ProductionQueueService
        pq = ProductionQueueService(await self.memory.get_db())
        cluster_items = await pq.get_cluster_items(cluster_id)
        published = [
            i for i in cluster_items
            if i.status == "completed" and i.etsy_listing_id
        ]

        # Gate: ≥ 2 pubblicati
        if len(published) < 2:
            logger.debug(
                "publisher: cross-ref skip — solo %d listing pubblicati nel cluster %s",
                len(published), cluster_id,
            )
            return

        # Genera cross-ref block per ogni listing
        updated_count = 0
        for item in published:
            other_listings = [p for p in published if p.etsy_listing_id != item.etsy_listing_id]
            if not other_listings:
                continue

            crossref_lines = [
                "",
                _CROSSREF_SEPARATOR,
                "You might also like from our shop:",
            ]
            for other in other_listings[:5]:  # max 5 cross-ref
                crossref_lines.append(
                    f"→ {other.listing_title or 'Related item'} "
                    f"— etsy.com/listing/{other.etsy_listing_id}"
                )
            crossref_lines.append(_CROSSREF_SEPARATOR)
            crossref_block = "\n".join(crossref_lines)

            # Sostituisce (non appende) blocco cross-ref nella descrizione
            current_desc = item.listing_description or ""
            # Rimuovi vecchio blocco se presente
            if _CROSSREF_SEPARATOR in current_desc:
                current_desc = current_desc[:current_desc.index(_CROSSREF_SEPARATOR)].rstrip()
            new_desc = current_desc + crossref_block

            # PATCH Etsy API
            try:
                await self._etsy_api.patch_listing_description(
                    listing_id=item.etsy_listing_id,
                    description=new_desc,
                )
                # Aggiorna etsy_listing_url in production_queue
                if not item.etsy_listing_url:
                    await pq.set_etsy_listing_url(
                        item_id=item.id,
                        url=f"etsy.com/listing/{item.etsy_listing_id}",
                    )
                updated_count += 1
            except Exception as e:
                logger.error(
                    "publisher: PATCH cross-ref fallito per listing %s: %s",
                    item.etsy_listing_id, e,
                )

        # Telegram notification
        if updated_count > 0:
            links_per_listing = min(len(published) - 1, 5)
            await self._notify_telegram(
                f"🔗 Cross-ref aggiornato — cluster {cluster_id[:6]}\n"
                f"{updated_count} listing aggiornati · {links_per_listing} link correlati per listing"
            )
```

### C.4.2 — publisher.py: import mixin + trigger post-publish

**Import** (aggiungere ai mixin in cima a publisher.py):
```python
from apps.backend.agents._publisher._crossref_mixin import _CrossrefMixin
```

**Classe** (aggiungere `_CrossrefMixin` ai mixin):
```python
class PublisherAgent(_CrossrefMixin, _PublishMixin, _ResolveMixin, _ThumbnailMixin, _SeoMixin, AgentBase):
```

**Trigger post-publish** (in `_publish_mixin.py`, dopo publish success):
```python
# C.4 — cross-reference update
if etsy_listing_id and item.cluster_id:
    try:
        await self._update_cluster_crossrefs(
            cluster_id=item.cluster_id,
            new_listing_id=etsy_listing_id,
            new_listing_title=item.listing_title or "",
            new_listing_url=f"etsy.com/listing/{etsy_listing_id}",
        )
    except Exception as _xref_err:
        logger.warning("publisher: cross-ref update fallito: %s", _xref_err)
        # Non blocca il publish — cross-ref è enrichment
```

**`set_etsy_listing_url`** — nuovo metodo in `ProductionQueueService`:
```python
async def set_etsy_listing_url(self, item_id: int, url: str) -> None:
    now = self._now()
    async with self._db.execute(
        "UPDATE production_queue SET etsy_listing_url = ?, updated_at = ? WHERE id = ?",
        (url, now, item_id),
    ):
        await self._db.commit()
```

### C.4.3 — API endpoints: clusters

**In `apps/backend/api/routers/etsy.py`:**

```python
@router.get("/clusters")
async def get_clusters(memory: MemoryManager = Depends(get_memory)):
    """Ritorna tutti i cluster attivi con il loro stato."""
    from apps.backend.core.production_queue import ProductionQueueService
    pq = ProductionQueueService(await memory.get_db())
    # Query diretta: raggruppa per cluster_id
    rows = await pq._fetchall(
        "SELECT cluster_id, COUNT(*) as total, "
        "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as completed, "
        "MIN(niche) as niche "
        "FROM production_queue WHERE cluster_id IS NOT NULL GROUP BY cluster_id"
    )
    return {"clusters": [dict(r) for r in rows]}


@router.get("/clusters/{cluster_id}")
async def get_cluster_detail(
    cluster_id: str,
    memory: MemoryManager = Depends(get_memory),
):
    """Ritorna dettaglio di un cluster: tutti i listing con stato e cross-ref."""
    from apps.backend.core.production_queue import ProductionQueueService
    pq = ProductionQueueService(await memory.get_db())
    items = await pq.get_cluster_items(cluster_id)
    if not items:
        raise HTTPException(status_code=404, detail=f"Cluster '{cluster_id}' non trovato")
    return {
        "cluster_id": cluster_id,
        "items": [
            {
                "id": i.id,
                "niche": i.niche,
                "product_tier": i.product_tier,
                "release_order": i.release_order,
                "status": i.status,
                "etsy_listing_id": i.etsy_listing_id,
                "etsy_listing_url": i.etsy_listing_url,
                "listing_title": i.listing_title,
            }
            for i in items
        ],
    }
```

### C.4.4 — Frontend: ProductionPipeline.tsx

**Icona 🔗** su listing con cross-ref attivo:
```tsx
{item.etsy_listing_id && item.cluster_id && (
  <span
    title="Cross-ref attivo"
    style={{ marginLeft: 4, cursor: 'pointer' }}
    onClick={() => { /* espandi cross-ref links */ }}
  >
    🔗
  </span>
)}
```

**Cluster grouping** (opzionale per UI clarity):
Aggiungere un toggle "Raggruppa per cluster" che ordina gli item per `cluster_id`
invece che per data. Quando attivo, mostra `cluster_id[:6]` come separatore visivo.

### C.4 — TDD: test file

**`tests/test_c4_crossref.py`** (da scrivere prima del codice):

```python
# Test 1: < 2 listing published → nessun PATCH, nessuna notifica Telegram
# Test 2: ≥ 2 listing published → PATCH chiamato su entrambi
# Test 3: cross-ref block format corretto (separatori, → arrows, etsy.com URL)
# Test 4: cross-ref blocco sostituito (non appeso) se già presente in descrizione
# Test 5: mock mode → skip silenzioso, nessun errore
# Test 6: Etsy PATCH failure → log error, non raise (publish non viene annullato)
# Test 7: max 5 cross-ref per listing (cluster con 7 listing → solo 5 link)
# Test 8: set_etsy_listing_url → colonna aggiornata in DB
# Test 9: GET /api/etsy/clusters → lista cluster con counts
# Test 10: GET /api/etsy/clusters/{id} → dettaglio con items
# Test 11: GET /api/etsy/clusters/nonexistent → 404
```

---

## C.5 — E2E Verification (tutti i criteri A.3 per BLOCCO C)

**Skill da usare:** `verification-before-completion`

### Criteri E2E da ETSY_STRATEGY.md

| Test | Atteso |
|---|---|
| Research produce `audience_target` | presente, `len > 10` |
| Research produce `ladder.tripwire.price_usd` | `≤ 2.50` |
| `expansion_potential` presente | `≥ 10` oppure nicchia scartata |
| `_generate_core_variations` produce 3 variazioni | tutte con `variation_type` distinto (max 2 stesso tipo) |
| `cluster_id` deterministico | `sha256(niche)[:12]` identico su run ripetuto |
| `release_order` in production_queue | tripwire=1, core=2, variation A=3, … bundle=6 |
| `_get_competitor_shop_analysis` cache hit 30gg | 0 Tavily calls extra su run successivo |
| Cross-ref update dopo 2 listing published | Etsy PATCH chiamato su entrambi |
| Cross-ref in mock mode | skip silenzioso, nessun errore |
| Scoring con `expansion_potential=8` | nicchia scartata (confidence ≈ 0.0) |
| Scoring con `expansion_potential=25` | boost +0.05 applicato |
| `ai_producibility.score="low"` | `requires_human_review=True` + Telegram notifica |

---

## DB Migration Summary

```sql
-- C.1: product_tier CHECK = application-level (colonna già esiste da A.0)
-- C.2:
ALTER TABLE production_queue ADD COLUMN cluster_id TEXT;
ALTER TABLE production_queue ADD COLUMN release_order INTEGER NOT NULL DEFAULT 0;
-- C.4:
ALTER TABLE production_queue ADD COLUMN etsy_listing_url TEXT;
-- Indici:
CREATE INDEX IF NOT EXISTS idx_pq_cluster ON production_queue(cluster_id);
CREATE INDEX IF NOT EXISTS idx_pq_release_order ON production_queue(cluster_id, release_order);
```

---

## Nuovi file da creare

| File | Scopo |
|---|---|
| `apps/backend/agents/_market_data/_shop_analysis_mixin.py` | C.3 — shop competitive analysis |
| `apps/backend/agents/_publisher/_crossref_mixin.py` | C.4 — cross-reference update |
| `tests/test_c1_product_ladder.py` | C.1 TDD |
| `tests/test_c2_cluster_strategy.py` | C.2 TDD |
| `tests/test_c3_shop_analysis.py` | C.3 TDD |
| `tests/test_c4_crossref.py` | C.4 TDD |

---

## File modificati (non creati)

| File | Cambiamento principale |
|---|---|
| `apps/backend/agents/_research/prompts.py` | Schema v3: `ladder` dict + `ai_producibility` |
| `apps/backend/agents/_research/analysis_mixin.py` | 3 LLM calls, C.3 integration |
| `apps/backend/agents/_research/scoring_mixin.py` | expansion_potential gate, pesi ribilanciati |
| `apps/backend/agents/research.py` | `_build_cluster`, `_generate_core_variations`, cluster trigger |
| `apps/backend/agents/market_data.py` | Assembla `_ShopAnalysisMixin` |
| `apps/backend/agents/_market_data/__init__.py` | Export `_ShopAnalysisMixin` |
| `apps/backend/agents/publisher.py` | Assembla `_CrossrefMixin`, trigger C.4 |
| `apps/backend/core/_memory/_base.py` | Migrations C.2 + C.4 |
| `apps/backend/core/production_queue.py` | `ProductionQueueItem` esteso, nuovi metodi cluster |
| `apps/backend/api/routers/etsy.py` | 3 nuovi endpoint: clusters, cluster_detail, competitor-analysis |
| `apps/frontend/src/components/etsy/NicheTable.tsx` | Colonna "Gap" + score border |
| `apps/frontend/src/components/etsy/ProductionPipeline.tsx` | Icona 🔗, cluster grouping toggle |

---

## Stima costi per nicchia (run completo, cache miss)

| Componente | Modello | Costo/nicchia |
|---|---|---|
| 3 ladder calls: core + tripwire + bundle | Haiku × 3 | ~€0.02 |
| Core variations × 3 | Haiku × 1 batch | ~€0.01 |
| Competitor shop analysis (N shop + Sonnet) | Haiku × 3 + Sonnet × 1 | ~€0.04 |
| **Totale per nicchia** | | **~€0.07** |
| **Warmup C completo (24 nicchie, cache miss)** | | **~€1.68** |
| Post-warmup (cache C.3 hit) | | **~€0.03/nicchia** |

---

## Timeline stimata

```
C.1 Ladder + SYSTEM_PROMPT v3 + scoring ribilanciato  (4-5g)
  → production-code-audit
C.2 Cluster flow + _build_cluster + release_order      (4-5g)
C.3 Shop competitive + _ShopAnalysisMixin + cache 30gg (3-4g)
C.4 Cross-ref Publisher + PATCH + Telegram             (2-3g)
E2E test BLOCCO C completo                             (1-2g)
─────────────────────────────────────────────────────
Totale: ~14-19 giorni
```
