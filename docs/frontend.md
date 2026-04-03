# Frontend — Dashboard operativa

Next.js 14 App Router. Dark theme fisso. Monospace typography per dati operativi.
La dashboard è lo strumento interno dell'operatore — non è il portale cliente (vedere `docs/portal.md`).

---

## Struttura pagine — MVP

5 route MVP. Nessuna route aggiuntiva finché non espressamente richiesta.

```
frontend/app/
├── layout.tsx                  ← Root layout: dark theme, font vars
├── page.tsx                    ← Redirect → /dashboard
│
├── login/
│   └── page.tsx                ← Form email + password → POST /auth/token
│
├── dashboard/
│   └── page.tsx                ← Overview pipeline: metriche, task in-flight, AgentActivityFeed
│
├── deals/
│   └── [id]/
│       └── page.tsx            ← Dettaglio deal: proposta, gate, erogazione
│
├── portal/                     ← Portale cliente (docs/portal.md)
│   ├── [token]/page.tsx        ← GATE 1 (approvazione proposta) o GATE 3 (approvazione consegna)
│   └── expired/page.tsx        ← Token scaduto o già usato
│
└── api/                        ← Next.js API routes (solo SSE)
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

### Typography — `next/font`

Caricamento font tramite `next/font` (zero layout-shift, self-hosted automatico).

```typescript
// app/layout.tsx
import { Inter } from "next/font/google"
import localFont from "next/font/local"

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
})

// JetBrains Mono Variable — file richiesto: public/fonts/JetBrainsMono[wght].woff2
// Opzione 1 (npm): npm install @fontsource-variable/jetbrains-mono
//   poi copia da node_modules/@fontsource-variable/jetbrains-mono/files/
// Opzione 2 (manuale): scarica da https://www.jetbrains.com/lp/mono/
//   → scegli "Variable font" → estrai JetBrainsMono[wght].woff2 → copia in public/fonts/
const jetbrainsMono = localFont({
  src: "../public/fonts/JetBrainsMono[wght].woff2",
  variable: "--font-mono",
})

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="it" className={`${inter.variable} ${jetbrainsMono.variable}`}>
      <body className="font-sans">{children}</body>
    </html>
  )
}
```

```typescript
// tailwind.config.ts
fontFamily: {
  sans: ["var(--font-inter)", "system-ui", "sans-serif"],
  mono: ["var(--font-mono)", "monospace"],  // usato per UUID, timestamp, log
},
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
  in_delivery:      { label: "In erogazione", color: "text-purple-300 bg-purple-900" },
}
```

---

## Componenti chiave

### `components/ui/` — puri, zero fetch

```
components/ui/
├── StatusBadge.tsx     ← badge colorato per TaskStatus e DealStatus
├── AgentTag.tsx        ← pill con nome agente ("Scout", "Document Generator", ...)
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

**Canale Redis:** `agentpexi:events:{run_id}` (un canale per run).

**7 event type:**

| `type` | Emesso da | Significato |
|--------|-----------|-------------|
| `task_started` | BaseAgent | Agente inizia task |
| `task_completed` | BaseAgent | Agente completa task con successo |
| `task_failed` | BaseAgent | Agente fallisce task |
| `task_blocked` | BaseAgent | Agente bloccato (mancano dati o errore recuperabile) |
| `gate_pending` | Orchestrator | Run in attesa di gate umano |
| `gate_approved` | API gate endpoint | Gate approvato dall'operatore/cliente |
| `run_completed` | Orchestrator | Run terminato (tutte le fasi completate) |

**Hook di pubblicazione (lato Python):**

| `type` | Pubblicato in | Quando |
|--------|--------------|--------|
| `task_started` | `BaseAgent.run()` | Prima di chiamare `execute()` |
| `task_completed` | `BaseAgent.run()` | Dopo `execute()` con successo |
| `task_failed` | `BaseAgent.run()` | In catch di `AgentToolError` / `Exception` |
| `task_blocked` | `BaseAgent.run()` | In catch di `GateNotApprovedError` |
| `gate_pending` | `orchestrator/nodes/gates.py` | `await_*` node al momento della pausa |
| `gate_approved` | `api/routers/deals.py` | POST `gates/proposal-approve`, `kickoff-confirm`, `delivery-approve` |
| `run_completed` | `orchestrator/graph.py` | Nodo terminale `END` node |

```python
# backend/src/agents/base.py — funzione di supporto (chiamata da run())
import redis.asyncio as aioredis
import json
import os

async def _publish_sse(run_id: str, event_type: str, agent: str, data: dict) -> None:
    """Pubblica un evento SSE sul canale Redis del run."""
    r = aioredis.from_url(os.environ["REDIS_URL"])
    payload = json.dumps({
        "type":      event_type,
        "run_id":    run_id,
        "agent":     agent,
        "timestamp": datetime.utcnow().isoformat(),
        "data":      data,
    }, default=str)
    await r.publish(f"agentpexi:events:{run_id}", payload)
    await r.aclose()
```

> `run_id` arriva come `task.payload["run_id"]` — l'Orchestrator lo inietta
> in ogni payload prima del dispatch (in `orchestrator/nodes/delegate.py`).

**Payload evento:**

```typescript
interface AgentPeXIEvent {
  type: "task_started" | "task_completed" | "task_failed" | "task_blocked" |
        "gate_pending" | "gate_approved" | "run_completed"
  run_id: string
  agent: string           // nome agente, es. "proposal"
  timestamp: string       // ISO 8601
  data: Record<string, unknown>  // payload specifico per type
}
```

```typescript
// app/api/events/route.ts
import Redis from "ioredis"

export const dynamic = "force-dynamic"

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const runId = searchParams.get("run_id")
  if (!runId) return new Response("run_id required", { status: 400 })

  const channel = `agentpexi:events:${runId}`
  // Crea subscriber dedicato (non condividere connessione globale)
  const subscriber = new Redis(process.env.REDIS_URL!)

  const stream = new ReadableStream({
    async start(controller) {
      await subscriber.subscribe(channel)
      subscriber.on("message", (_ch: string, message: string) => {
        controller.enqueue(new TextEncoder().encode(`data: ${message}\n\n`))
      })
      request.signal.addEventListener("abort", () => {
        subscriber.unsubscribe(channel)
        subscriber.disconnect()
        controller.close()
      })
    },
  })

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
    },
  })
}
```

**Uso lato client:**

```typescript
// components/pipeline/AgentActivityFeed.tsx
"use client"
import { useEffect, useState } from "react"

export function AgentActivityFeed({ runId }: { runId: string }) {
  const [events, setEvents] = useState<AgentPeXIEvent[]>([])

  useEffect(() => {
    const es = new EventSource(`/api/events?run_id=${runId}`)
    es.onmessage = (e) => {
      const event: AgentPeXIEvent = JSON.parse(e.data)
      setEvents((prev) => [event, ...prev].slice(0, 50))
    }
    return () => es.close()
  }, [runId])

  // ...
}
```

---

## Pagina Dashboard — layout

```
┌─────────────────────────────────────────────────┐
│  4 MetricCard: Leads | Deals attivi | In erogazione | Revenue  │
├────────────────────┬────────────────────────────┤
│  AgentActivityFeed │  DealKanban                │
│  (SSE real-time)   │  (colonne per status)      │
│                    │                            │
│  Ultimi 20 eventi  │  Lead → Proposal → Deliv → │
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

---

## Dipendenze npm (`frontend/package.json`)

| Pacchetto | Versione | Scopo |
|-----------|----------|-------|
| `next` | 14.2.x | Framework React App Router |
| `react` | 18.x | UI runtime |
| `react-dom` | 18.x | DOM render |
| `typescript` | 5.x | Tipizzazione |
| `tailwindcss` | 3.x | Utility CSS |
| `@headlessui/react` | 2.x | Componenti accessibili (dialog, menu) |
| `@heroicons/react` | 2.x | Icone SVG |
| `recharts` | 2.x | Grafici pipeline / stats |
| `react-pdf` | 7.x | PDF viewer inline (review proposta) |
| `swr` | 2.x | Data fetching + revalidation |
| `jose` | 5.x | Verifica JWT portale cliente (server-side) |
| `date-fns` | 3.x | Formattazione date (fuso EU/IT) |
| `clsx` | 2.x | Composizione className condizionale |

**Dev:**
`@types/react`, `@types/node`, `eslint`, `eslint-config-next`, `postcss`, `autoprefixer`.
