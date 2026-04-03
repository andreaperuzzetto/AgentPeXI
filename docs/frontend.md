# Frontend — Dashboard operativa

Next.js 14 App Router. Dark theme fisso. Monospace typography per dati operativi.
La dashboard è lo strumento interno dell'operatore — non è il portale cliente (vedere `docs/portal.md`).

---

## Struttura pagine

```
frontend/app/
├── layout.tsx                  ← Root layout: dark theme, sidebar, nav
├── page.tsx                    ← Redirect → /dashboard
│
├── dashboard/
│   └── page.tsx                ← Overview pipeline: metriche, task in-flight
│
├── leads/
│   ├── page.tsx                ← Lista leads con filtri
│   └── [id]/page.tsx           ← Dettaglio lead + trigger analisi
│
├── deals/
│   ├── page.tsx                ← Kanban/lista deals per status
│   └── [id]/
│       ├── page.tsx            ← Dettaglio deal
│       ├── proposal/page.tsx   ← Review proposta → GATE 1
│       └── development/page.tsx ← Stato sviluppo → GATE 2/3
│
├── clients/
│   ├── page.tsx                ← Lista clienti attivi
│   └── [id]/page.tsx           ← Dettaglio cliente + storico + NPS
│
├── portal/                     ← Portale cliente (docs/portal.md)
│   ├── [token]/page.tsx
│   └── expired/page.tsx
│
└── api/                        ← Next.js API routes (solo per SSE/streaming)
    └── events/route.ts         ← Server-Sent Events per aggiornamenti real-time
```

---

## Design system

### Tema e colori

```typescript
// tailwind.config.ts
// Dark theme fisso — nessun toggle light/dark
// La classe `dark` è sempre presente su <html>

const colors = {
  background: {
    primary:   "#0a0a0a",   // sfondo pagina
    secondary: "#111111",   // card, sidebar
    tertiary:  "#1a1a1a",   // input, hover
    elevated:  "#222222",   // dropdown, modal
  },
  border: {
    subtle: "#2a2a2a",      // bordi card
    default: "#333333",     // bordi interattivi
    active: "#444444",      // focus, hover
  },
  text: {
    primary:   "#f0f0f0",
    secondary: "#a0a0a0",
    muted:     "#606060",
  },
  accent: {
    blue:   "#3b82f6",      // azioni principali
    green:  "#22c55e",      // success, gate approved
    amber:  "#f59e0b",      // warning, awaiting gate
    red:    "#ef4444",      // error, failed
    purple: "#a855f7",      // agenti in esecuzione
  }
}
```

### Typography

```css
/* layout.tsx */
body {
  font-family: 'Inter', system-ui, sans-serif;  /* testo narrativo */
}

.mono {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  /* usato per: UUID, timestamp, status badge, codice, log output */
}
```

### Status badge

```typescript
// components/ui/StatusBadge.tsx
const statusConfig = {
  pending:          { label: "In attesa",    color: "text-gray-400  bg-gray-800"   },
  running:          { label: "In esecuzione",color: "text-purple-300 bg-purple-900" },
  blocked:          { label: "Bloccato",     color: "text-amber-300 bg-amber-900"  },
  completed:        { label: "Completato",   color: "text-green-300 bg-green-900"  },
  failed:           { label: "Fallito",      color: "text-red-300   bg-red-900"    },
  awaiting_gate:    { label: "Attende approvazione", color: "text-amber-300 bg-amber-900" },
  proposal_ready:   { label: "Proposta pronta",       color: "text-blue-300  bg-blue-900"  },
  client_approved:  { label: "Cliente ha approvato",  color: "text-green-300 bg-green-900" },
  in_development:   { label: "In sviluppo",  color: "text-purple-300 bg-purple-900" },
}
```

---

## Componenti chiave

### `components/ui/` — puri, zero fetch

```
components/ui/
├── StatusBadge.tsx     ← badge colorato per TaskStatus e DealStatus
├── AgentTag.tsx        ← pill con nome agente ("Scout", "QA Agent", ...)
├── GateButton.tsx      ← pulsante approvazione gate (con confirm dialog)
├── Timeline.tsx        ← lista verticale eventi pipeline
├── MetricCard.tsx      ← card numero grande + label (dashboard)
├── DataTable.tsx       ← tabella con sorting e filtri lato client
├── Modal.tsx           ← overlay modale (proposta rejection notes, ecc.)
└── EmptyState.tsx      ← stato vuoto con CTA
```

### `components/pipeline/` — connessi ai dati

```
components/pipeline/
├── RunStatusPanel.tsx       ← stato run corrente con task_history
├── GatePanel.tsx            ← pannello approvazione gate con anteprima
├── AgentActivityFeed.tsx    ← feed real-time attività agenti (SSE)
└── DealKanban.tsx           ← colonne per DealStatus
```

---

## Fetch e real-time

### Server Components per dati iniziali

```typescript
// app/deals/[id]/page.tsx
export default async function DealPage({ params }: { params: { id: string } }) {
  const deal = await getDeal(params.id)          // fetch server-side
  const tasks = await getTasks({ deal_id: params.id })

  return <DealView deal={deal} initialTasks={tasks} />
}
```

### SWR per aggiornamenti polling

```typescript
// components/pipeline/RunStatusPanel.tsx — Client Component
"use client"
import useSWR from "swr"

export function RunStatusPanel({ runId }: { runId: string }) {
  const { data } = useSWR(`/api/runs/${runId}`, fetcher, {
    refreshInterval: 3000,    // poll ogni 3 secondi durante run attivo
  })
  // ...
}
```

### Server-Sent Events per aggiornamenti live

```typescript
// app/api/events/route.ts
export async function GET(request: Request) {
  const stream = new ReadableStream({
    start(controller) {
      // Connetti a Redis subscriber
      // Invia eventi al client quando arrivano AgentResult
    }
  })
  return new Response(stream, {
    headers: { "Content-Type": "text/event-stream" }
  })
}
```

---

## Pagina Dashboard — layout

```
┌─────────────────────────────────────────────────┐
│  4 MetricCard: Leads | Deals attivi | In dev | Revenue  │
├────────────────────┬────────────────────────────┤
│  AgentActivityFeed │  DealKanban                │
│  (SSE real-time)   │  (colonne per status)      │
│                    │                            │
│  Ultimi 20 eventi  │  Lead → Proposal → Dev →  │
│  con agent + stato │  Post-sale                 │
└────────────────────┴────────────────────────────┘
```

---

## Pagina Deal — review proposta (GATE 1)

```
┌─────────────────────────────────────────────────┐
│  Deal: Bar Centrale Treviso        [AWAITING]   │
├─────────────────────────────────────────────────┤
│  Proposta v1 — generata il 01/01/2025           │
│  ┌─────────────────────────────────────────┐   │
│  │  [PDF inline viewer - iframe]           │   │
│  │                                         │   │
│  └─────────────────────────────────────────┘   │
│                                                 │
│  Note rifiuto (opzionale):                      │
│  ┌─────────────────────────────────────────┐   │
│  │  textarea...                            │   │
│  └─────────────────────────────────────────┘   │
│                                                 │
│  [Approva e invia al cliente]  [Rifiuta e rifare]│
└─────────────────────────────────────────────────┘
```

Il click su "Approva" chiama `POST /deals/{id}/gates/proposal-approve`.
Il click su "Rifiuta e rifare" chiama `POST /deals/{id}/gates/proposal-reject` con le note.

---

## Variabili d'ambiente frontend

```bash
# .env.local (non committare)
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_APP_NAME=AgentPeXI
```

Le variabili senza `NEXT_PUBLIC_` non sono mai esposte al browser.
`PORTAL_SECRET_KEY` non va mai in `.env.local` frontend — vive solo nel backend.
