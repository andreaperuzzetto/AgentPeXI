"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import useSWR from "swr"
import { Activity, LogOut, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { StatsOverview } from "@/components/dashboard/stats-overview"
import { GateAlerts } from "@/components/dashboard/gate-alerts"
import { AgentActivityFeed } from "@/components/dashboard/agent-activity-feed"
import { DealKanban } from "@/components/dashboard/deal-kanban"
import { PipelineChart } from "@/components/dashboard/pipeline-chart"
import type { Agent, Deal as LabDeal, PipelineEvent, Zone } from "@/lib/lab-data"
import type { Deal, PipelineStats, RunSummary } from "@/lib/api"
import { listDeals, getStats, listRuns } from "@/lib/api"
import { logout } from "@/lib/auth"

// ---------------------------------------------------------------------------
// Adapters: API types → lab-data types (per i componenti esistenti)
// ---------------------------------------------------------------------------

const STATUS_TO_ZONE: Record<string, Zone> = {
  lead_identified:    "discovery",
  analysis_complete:  "discovery",
  proposal_ready:     "proposal",
  proposal_sent:      "proposal",
  negotiating:        "proposal",
  client_approved:    "proposal",
  in_delivery:        "delivery",
  delivered:          "delivery",
  active:             "post_sale",
}

const STATUS_PROGRESS: Record<string, number> = {
  lead_identified:    10,
  analysis_complete:  25,
  proposal_ready:     40,
  proposal_sent:      50,
  negotiating:        60,
  client_approved:    65,
  in_delivery:        80,
  delivered:          92,
  active:             100,
}

function dealToLabDeal(d: Deal, leadName: string): LabDeal {
  return {
    id:           d.id,
    leadName,
    serviceType:  d.service_type,
    status:       d.status,
    currentPhase: STATUS_TO_ZONE[d.status] ?? "discovery",
    gates: {
      proposal_approved: d.proposal_human_approved,
      kickoff_confirmed: d.kickoff_confirmed,
      delivery_approved: d.delivery_approved,
    },
    progress: STATUS_PROGRESS[d.status] ?? 0,
  }
}

// Agenti statici — la dashboard mostra gli agent tag in base agli SSE ricevuti
const AGENT_LABELS: Record<string, { name: string; accent: string; zone: Zone }> = {
  scout:                { name: "Scout",            accent: "#3b82f6", zone: "discovery"  },
  analyst:              { name: "Analyst",          accent: "#8b5cf6", zone: "discovery"  },
  lead_profiler:        { name: "Lead Profiler",    accent: "#06b6d4", zone: "discovery"  },
  design:               { name: "Design",           accent: "#f59e0b", zone: "proposal"   },
  proposal:             { name: "Proposal",         accent: "#10b981", zone: "proposal"   },
  sales:                { name: "Sales",            accent: "#ef4444", zone: "proposal"   },
  delivery_orchestrator:{ name: "Delivery Orch.",   accent: "#6366f1", zone: "delivery"   },
  doc_generator:        { name: "Doc Generator",    accent: "#7c3aed", zone: "delivery"   },
  delivery_tracker:     { name: "Delivery Tracker", accent: "#059669", zone: "delivery"   },
  account_manager:      { name: "Account Manager",  accent: "#0ea5e9", zone: "post_sale"  },
  billing:              { name: "Billing",          accent: "#d97706", zone: "post_sale"  },
  support:              { name: "Support",          accent: "#dc2626", zone: "post_sale"  },
}

function buildAgents(activeAgentIds: string[]): Agent[] {
  return Object.entries(AGENT_LABELS).map(([id, meta]) => ({
    id,
    name:        meta.name,
    icon:        meta.name.slice(0, 2).toUpperCase(),
    status:      activeAgentIds.includes(id) ? "running" : "idle",
    zone:        meta.zone,
    task:        null,
    accent:      meta.accent,
    description: meta.name,
    logs:        [],
  })) as Agent[]
}

// ---------------------------------------------------------------------------
// SWR fetcher
// ---------------------------------------------------------------------------

const fetcher = async <T,>(fn: () => Promise<T>): Promise<T> => fn()

// ---------------------------------------------------------------------------
// Dashboard page
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  const router  = useRouter()
  const [dismissedGates, setDismissedGates] = useState<string[]>([])
  const [events, setEvents] = useState<PipelineEvent[]>([])
  const [activeRunId, setActiveRunId] = useState<string | null>(null)

  // ── Polling dati reali ────────────────────────────────────────────────────
  const { data: stats, mutate: mutateStats } = useSWR<PipelineStats>(
    "stats",
    () => fetcher(getStats),
    { refreshInterval: 10_000, shouldRetryOnError: false },
  )
  const { data: dealsData, mutate: mutateDeals } = useSWR<{ items: Deal[] }>(
    "deals",
    () => fetcher(() => listDeals()),
    { refreshInterval: 10_000, shouldRetryOnError: false },
  )
  const { data: runsData } = useSWR<{ items: RunSummary[]; total: number }>(
    "runs",
    () => fetcher(() => listRuns({ status: "running" })),
    { refreshInterval: 8_000, shouldRetryOnError: false },
  )

  // ── Aggiorna il run attivo per SSE ────────────────────────────────────────
  useEffect(() => {
    const running = runsData?.items?.[0]
    setActiveRunId(running?.run_id ?? null)
  }, [runsData])

  // ── SSE — eventi real-time ────────────────────────────────────────────────
  useEffect(() => {
    if (!activeRunId) return

    const src = new EventSource(`/api/events?run_id=${activeRunId}`)

    src.onmessage = (e) => {
      if (!e.data || e.data.startsWith(":")) return
      try {
        const msg = JSON.parse(e.data)
        const event: PipelineEvent = {
          id:        crypto.randomUUID(),
          type:      msg.event_type ?? "task_started",
          agent:     msg.agent ?? "system",
          timestamp: Date.now(),
          message:   msg.payload ? JSON.stringify(msg.payload) : msg.event_type,
        }
        setEvents((prev) => [event, ...prev].slice(0, 100))
        // Aggiorna stats e deals dopo ogni evento completato
        if (msg.event_type === "task_completed" || msg.event_type === "run_completed") {
          mutateStats()
          mutateDeals()
        }
      } catch {
        // Ignora keepalive o JSON malformato
      }
    }

    return () => src.close()
  }, [activeRunId, mutateStats, mutateDeals])

  // ── Dati derivati ─────────────────────────────────────────────────────────
  const labDeals: LabDeal[] = (dealsData?.items ?? [])
    .filter((d) => !["lost", "cancelled"].includes(d.status))
    .map((d) => dealToLabDeal(d, d.id.slice(0, 8)))  // usa ID come placeholder—viene sostituito dal dettaglio

  const activeAgentIds = events
    .filter((e) => e.type === "task_started")
    .slice(0, 12)
    .map((e) => e.agent)
  const agents = buildAgents(activeAgentIds)

  const statsProps = {
    leads:        stats?.leads_total        ?? 0,
    activeDeals:  stats?.deals_active       ?? 0,
    inDelivery:   stats?.deals_in_delivery  ?? 0,
    gatesPending: stats?.deals_awaiting_gate ?? 0,
  }

  const pendingGates = (dealsData?.items ?? [])
    .flatMap((d): { dealId: string; dealName: string; gateType: "proposal" | "kickoff" | "delivery" }[] => {
      const gates = []
      if (!d.proposal_human_approved && ["proposal_ready","proposal_sent","negotiating"].includes(d.status))
        gates.push({ dealId: d.id, dealName: d.id.slice(0, 8), gateType: "proposal" as const })
      if (!d.kickoff_confirmed && d.status === "client_approved")
        gates.push({ dealId: d.id, dealName: d.id.slice(0, 8), gateType: "kickoff" as const })
      if (!d.delivery_approved && d.status === "delivered")
        gates.push({ dealId: d.id, dealName: d.id.slice(0, 8), gateType: "delivery" as const })
      return gates
    })
    .filter((g) => !dismissedGates.includes(`${g.dealId}-${g.gateType}`))

  const handleLogout = useCallback(async () => {
    await fetch("/api/auth/logout-proxy", { method: "POST", credentials: "include" }).catch(() => null)
    logout()
    router.replace("/login")
  }, [router])

  return (
    <div className="min-h-screen text-foreground p-4 md:p-6 bg-background">
      <div className="max-w-7xl mx-auto space-y-6">

        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl flex items-center gap-3 text-white font-normal font-mono">
              <Activity className="w-8 h-8 text-foreground" strokeWidth={1} />
              AgentPeXI
            </h1>
            <p className="text-slate-400 mt-1 font-mono text-sm">
              Pipeline operativa — {new Date().toLocaleDateString("it-IT")}
              {activeRunId && (
                <span className="ml-3 text-green-400">● Run attivo</span>
              )}
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => { mutateStats(); mutateDeals() }}
              className="gap-2 bg-transparent text-white"
            >
              <RefreshCw className="w-4 h-4" />
              Aggiorna
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleLogout}
              className="gap-2 text-muted-foreground hover:text-white"
            >
              <LogOut className="w-4 h-4" />
              Esci
            </Button>
          </div>
        </div>

        {/* Metriche */}
        <StatsOverview {...statsProps} />

        {/* Gate alerts */}
        {pendingGates.length > 0 && (
          <GateAlerts
            pendingGates={pendingGates}
            onDismiss={(dealId, gateType) =>
              setDismissedGates((prev) => [...prev, `${dealId}-${gateType}`])
            }
          />
        )}

        {/* Tabs principali */}
        <Tabs defaultValue="live" className="space-y-4">
          <TabsList className="grid w-full grid-cols-3 max-w-md">
            <TabsTrigger value="live">Live Feed</TabsTrigger>
            <TabsTrigger value="pipeline">Pipeline</TabsTrigger>
            <TabsTrigger value="metriche">Metriche</TabsTrigger>
          </TabsList>

          <TabsContent value="live" className="space-y-4">
            {events.length === 0 ? (
              <div className="p-8 text-center text-sm text-muted-foreground font-mono border border-dashed border-white/10 rounded-lg">
                Nessun evento — avvia un run dalla CLI o dall&#39;API
              </div>
            ) : (
              <AgentActivityFeed events={events} agents={agents} />
            )}
          </TabsContent>

          <TabsContent value="pipeline" className="space-y-4">
            <DealKanban deals={labDeals} />
          </TabsContent>

          <TabsContent value="metriche" className="space-y-4">
            <PipelineChart agents={agents} />
          </TabsContent>
        </Tabs>

      </div>
    </div>
  )
}
