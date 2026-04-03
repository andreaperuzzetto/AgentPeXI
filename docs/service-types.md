# Tipi di servizio — Riferimento unificato

Questo documento consolida il comportamento di **ogni agente** in base al `service_type` del deal.
È il documento di riferimento unico per la differenziazione per servizio.

`service_type` può essere: `"consulting"` | `"web_design"` | `"digital_maintenance"`

---

## Panoramica per servizio

| Dimensione | Consulenza | Web Design | Manutenzione Digitale |
|------------|-----------|-----------|----------------------|
| **Gap target** | Inefficienze operative, mancanza competenze | Sito assente/obsoleto, brand poco curata | Sistemi datati, performance, sicurezza |
| **Deliverable core** | Report, workshop, roadmap | Mockup, pagine, branding | Audit, update cycle, setup monitoring |
| **Timeline tipica** | 4–8 settimane | 2–6 settimane | 2 settimane (una tantum) o ricorrente |
| **Modello pricing** | Per progetto | Per progetto | Per progetto o canone mensile |
| **Fatturazione** | 30/60/10 | 30/60/10 | 30/60/10 o mensile |
| **Gate 3 label** | `consulting_approved` | `delivery_approved` | `delivery_approved` |

---

## 1 — Scout Agent

Il sector è indipendente dal service_type — lo Scout cerca per settore.
Il `service_type` viene suggerito dall'Analyst dopo la scoperta, non dallo Scout.

**Nessuna differenziazione per service_type** — il payload è sempre:
```python
{"zone": str, "sector": str, "radius_km": int, "max_results": int}
```

---

## 2 — Market Analyst Agent

Usa i segnali specifici per servizio da `config/scoring.yaml`. Il `suggested_service_type`
viene assegnato in base ai segnali prevalenti. Se più servizi sono applicabili,
si sceglie quello con gap più evidente.

**Gap signals per servizio** (vedi `config/scoring.yaml` per i pesi):

| Consulenza | Web Design | Manutenzione Digitale |
|-----------|-----------|----------------------|
| Inefficienze operative | Sito assente | Sistemi software datati |
| Crescita senza struttura | Sito obsoleto | Performance problemi |
| Mancanza competenze | Nessuna presenza social | Vulnerabilità sicurezza |

**`estimated_value_eur`** suggerito per fascia:
- Consulenza: 2.000–8.000 EUR (media 4.000)
- Web Design: 1.500–6.000 EUR (media 3.000)
- Manutenzione: 500–2.000 EUR una tantum, o 150–600 EUR/mese (annualizzare ×12 per stima)

---

## 3 — Lead Profiler Agent

Nessuna differenziazione per service_type.
Arricchisce sempre: P.IVA, ATECO, company_size, profili social.

---

## 4 — Design Agent

Produce artefatti visivi diversi per ogni service_type.

### Consulenza

**`artifact_pages`**: `["roadmap", "workshop_structure", "process_schema", "presentation"]`

| Pagina | Template | Descrizione |
|--------|----------|-------------|
| `roadmap` | `consulting/roadmap.html` | Timeline fasi consulenza con attività |
| `workshop_structure` | `consulting/workshop_structure.html` | Agenda e struttura workshop |
| `process_schema` | `consulting/process_schema.html` | Schema AS-IS / TO-BE |
| `presentation` | `consulting/presentation.html` | Slide riassuntiva del piano |

### Web Design

**`artifact_pages`**: `["landing", "about", "services", "contact"]`

| Pagina | Template | Descrizione |
|--------|----------|-------------|
| `landing` | `web_design/landing.html` | Homepage con hero, servizi, CTA |
| `about` | `web_design/about.html` | Pagina Chi Siamo |
| `services` | `web_design/services.html` | Lista servizi offerti |
| `contact` | `web_design/contact.html` | Pagina contatti con form |

Viewport: desktop 1440×900 + mobile 390×844 (entrambi per ogni pagina).

### Manutenzione Digitale

**`artifact_pages`**: `["architecture", "update_plan", "monitoring_dashboard"]`

| Pagina | Template | Descrizione |
|--------|----------|-------------|
| `architecture` | `digital_maintenance/architecture.html` | Schema sistemi attuali + criticità |
| `update_plan` | `digital_maintenance/update_plan.html` | Piano aggiornamenti con roadmap |
| `monitoring_dashboard` | `digital_maintenance/monitoring_dashboard.html` | Dashboard KPI e stato servizi |

**Nota:** per manutenzione, le pagine sono più dati/analytics e meno marketing.
Viewport solo desktop 1440×900 (non mobile-first per questo tipo di artefatti).

---

## 5 — Proposal Agent

La struttura PDF cambia per service_type (vedi `config/templates/proposal/base.html`).

### Consulenza

- **Sezione problema:** gap operativi, inefficienze, mancanza struttura
- **Sezione soluzione:** piano di consulenza, numero sessioni, workshop previsti
- **Sezione artefatti:** roadmap + schema processo (titolo: "Il nostro piano di lavoro")
- **ROI:** stima miglioramento efficienza operativa (es. "Riduzione del 30% dei tempi di processo")
- **Milestone timeline:**
  - Settimana 1: Analisi e diagnostica
  - Settimana 2-3: Workshop e raccolta dati
  - Settimana 4: Roadmap e presentazione risultati

### Web Design

- **Sezione problema:** assenza online / brand poco curata / competitor più visibili
- **Sezione soluzione:** sito web professionale, mockup personalizzati
- **Sezione artefatti:** mockup UI (titolo: "Come apparirà il vostro sito")
- **ROI:** stima aumento visibilità online / lead generation
- **Milestone timeline:**
  - Settimana 1-2: Wireframe e approvazione struttura
  - Settimana 3-4: Sviluppo e design
  - Settimana 5: Revisione finale e pubblicazione

### Manutenzione Digitale

- **Sezione problema:** rischi sicurezza, performance degradata, sistemi obsoleti
- **Sezione soluzione:** piano di manutenzione continuativa con SLA
- **Sezione artefatti:** schema architetturale + piano aggiornamenti (titolo: "Lo stato attuale e il piano")
- **ROI:** stima risparmio (downtime evitato, costo incidenti di sicurezza)
- **Milestone/pricing:**
  - Se modello mensile: mostrare canone mensile + deliverable inclusi
  - Se una tantum: usare split 30/60/10 standard

---

## 6 — Sales Agent

Nessuna differenziazione significativa per service_type.
Il template email `proposal_send` è uniforme — il `{{proposal_summary}}`
viene adattato dall'agente in base al service_type del deal.

**`proposal_summary` per servizio:**
- Consulenza: descrizione del piano di lavoro e dei risultati attesi
- Web Design: descrizione del sito proposto con menzione dei mockup
- Manutenzione: descrizione del piano di protezione e SLA inclusi

**Negoziazione autonoma (confini per service_type):**

| Modifica | Consulenza | Web Design | Manutenzione |
|---------|-----------|-----------|--------------|
| Sconto ≤ 15% | ✅ autonomo | ✅ autonomo | ✅ autonomo |
| Timeline +2 settimane | ✅ autonomo | ✅ autonomo | ✅ autonomo |
| Rimozione deliverable minor | Rimozione di 1 sessione workshop | Rimozione di 1 pagina secondaria | Riduzione frequenza backup |
| Escalation | Modifica scope core (report, roadmap) | Modifica numero pagine core | Modifica SLA response time |

---

## 7 — Delivery Orchestrator Agent

Decompone il servizio in `service_deliveries` in base al `service_type`.

### Consulenza — decomposizione standard

```python
service_deliveries = [
    {"type": "report",     "title": "Report diagnostico iniziale",     "milestone_name": None,               "depends_on": []},
    {"type": "workshop",   "title": "Workshop operativo #1",           "milestone_name": None,               "depends_on": ["report"]},
    {"type": "workshop",   "title": "Workshop operativo #2",           "milestone_name": None,               "depends_on": ["workshop_1"]},
    {"type": "process_schema", "title": "Schema processi AS-IS/TO-BE", "milestone_name": None,               "depends_on": ["workshop_2"]},
    {"type": "roadmap",    "title": "Roadmap operativa finale",        "milestone_name": None,               "depends_on": ["process_schema"]},
    {"type": "presentation","title": "Presentazione risultati",        "milestone_name": "consulting_approved","depends_on": ["roadmap"]},
]
```

Gate 3 si chiama `consulting_approved` — verificato in `checkpoint.py` come `deal.consulting_approved`.

### Web Design — decomposizione standard

```python
service_deliveries = [
    {"type": "wireframe",  "title": "Wireframe struttura sito",        "milestone_name": "struttura_approvata",  "depends_on": []},
    {"type": "mockup",     "title": "Mockup homepage",                 "milestone_name": None,                   "depends_on": ["wireframe"]},
    {"type": "branding",   "title": "Elementi branding",               "milestone_name": None,                   "depends_on": []},
    {"type": "page",       "title": "Sviluppo pagine",                 "milestone_name": None,                   "depends_on": ["mockup", "branding"]},
    {"type": "responsive_check","title": "Verifica responsive",        "milestone_name": "mockup_finale",        "depends_on": ["page"]},
]
```

### Manutenzione Digitale — decomposizione standard

```python
service_deliveries = [
    {"type": "performance_audit","title": "Audit performance e sicurezza","milestone_name": None,            "depends_on": []},
    {"type": "update_cycle","title": "Piano aggiornamenti e bonifica",   "milestone_name": None,             "depends_on": ["performance_audit"]},
    {"type": "security_patch","title": "Applicazione patch sicurezza",   "milestone_name": "primo_ciclo",    "depends_on": ["update_cycle"]},
    {"type": "monitoring_setup","title": "Setup monitoraggio continuativo","milestone_name": "primo_ciclo",  "depends_on": ["security_patch"]},
]
```

---

## 8 — Document Generator Agent

Produce il deliverable specificato nel `service_delivery.type`.

| Type | Formato output | Tool usato |
|------|----------------|-----------|
| `report` | PDF (A4) | pdf_generator.py |
| `workshop` | PDF (A4) | pdf_generator.py |
| `roadmap` | PDF (A4) | pdf_generator.py |
| `process_schema` | PNG 1440px + PDF | mockup_renderer.py |
| `presentation` | PNG 1440px + PDF | mockup_renderer.py |
| `wireframe` | PNG 1440px | mockup_renderer.py |
| `mockup` | PNG 1440×900 + PNG 390×844 | mockup_renderer.py |
| `branding` | PDF (A4) | pdf_generator.py |
| `page` | PNG 1440×900 + HTML | mockup_renderer.py |
| `responsive_check` | PDF report | pdf_generator.py |
| `performance_audit` | PDF (A4) | pdf_generator.py |
| `update_cycle` | PDF (A4) | pdf_generator.py |
| `security_patch` | PDF (A4) | pdf_generator.py |
| `monitoring_setup` | PNG 1440px + PDF | mockup_renderer.py |

---

## 9 — Delivery Tracker Agent

La checklist di review varia per service_type (vedi [agents/delivery_tracker/CLAUDE.md](../agents/delivery_tracker/CLAUDE.md)).

**Criteri specifici:**

### Consulenza
- Il report contiene almeno 3 raccomandazioni actionable con priorità definita?
- Le slide workshop includono: obiettivi, agenda, esercizi o esempi concreti?
- La roadmap ha: timeline (+/- 2 settimane), responsabile per ogni milestone, KPI misurabili?

### Web Design
- Il mockup usa i colori brand del cliente (rilevati dal Lead Profiler)?
- Il layout è responsive: si legge correttamente a 390px senza scrolling orizzontale?
- Il copy è in italiano e fa riferimento esplicito al settore del cliente?

### Manutenzione Digitale
- L'audit riporta: versioni software attuali, CVE rilevanti, Lighthouse score?
- Il piano aggiornamenti ha: priorità (critical/high/medium), data prevista, rischio se non eseguito?
- La documentazione è sufficiente perché l'operatore esegua gli update autonomamente?

---

## 10 — Account Manager Agent

Adatta il tono e il contenuto dell'onboarding al servizio ricevuto.

| Trigger | Consulenza | Web Design | Manutenzione |
|---------|-----------|-----------|--------------|
| Onboarding | Recap deliverable, come usare la roadmap | Link al sito, credenziali CMS | Riepilogo piano, canali di supporto, SLA |
| Check-in 7d | "La roadmap sta guidando il team?" | "Il sito sta performando?" | "Tutto funziona correttamente?" |
| NPS 30d | Standard | Standard | "Il piano di manutenzione sta rispettando le aspettative?" |
| Upsell 90d | Se consulenza → proporre web design o manutenzione | Se web design → proporre manutenzione o nuova consulenza | Se manutenzione → proporre upgrade piano o consulenza operativa |

---

## 11 — Billing Agent

| service_type | Modello default | Override |
|-------------|----------------|---------|
| `consulting` | 30/60/10 (kickoff / delivery / +30d) | Tramite `deal.deposit_pct` |
| `web_design` | 30/60/10 | Tramite `deal.deposit_pct` |
| `digital_maintenance` | 30/60/10 se una tantum; mensile se canone | `invoice.milestone = "monthly"` se `pricing.billing_model = "monthly"` |

**Milestone mapping:**
- `consulting`: deposit=kickoff_confirmed, delivery=consulting_approved
- `web_design`: deposit=kickoff_confirmed, delivery=delivery_approved
- `digital_maintenance` canone: primo invoice= kickoff_confirmed, poi mensile il 1° del mese

**Override billing split:** l'operatore può modificare `deal.deposit_pct` (e implicitamente le altre percentuali) prima del kickoff. Il Billing Agent usa sempre i valori dal deal, non i default di `config/pricing.yaml`.

---

## 12 — Support Agent

Nessuna differenziazione per service_type nella classificazione del ticket.
Il contesto del servizio erogato è disponibile leggendo il workspace cliente.

Quando il Support Agent crea un nuovo `service_delivery` per un intervento:
- `consulting`: type = `"report"` (documento di risposta) o `"workshop"` (sessione extra)
- `web_design`: type = `"page"` (modifica pagina) o `"responsive_check"` (fix layout)
- `digital_maintenance`: type = `"security_patch"` o `"update_cycle"` (intervento extra)
