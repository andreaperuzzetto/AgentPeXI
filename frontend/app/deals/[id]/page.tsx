"use client"

import { useParams, useRouter } from "next/navigation"
import useSWR from "swr"
import { ArrowLeft, CheckCircle, Clock, XCircle, AlertCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { getDeal, listRuns, listProposals } from "@/lib/api"
import type { Deal, RunSummary, Proposal } from "@/lib/api"

const STATUS_LABEL: Record<string, string> = {
  lead_identified:    "Lead identificato",
  analysis_complete:  "Analisi completata",
  proposal_ready:     "Proposta pronta",
  proposal_sent:      "Proposta inviata",
  negotiating:        "In negoziazione",
  client_approved:    "Cliente ha approvato",
  in_delivery:        "In erogazione",
  delivered:          "Consegnato",
  active:             "Attivo",
  lost:               "Perso",
  cancelled:          "Annullato",
}

const SERVICE_LABEL: Record<string, string> = {
  web_design:           "Web Design",
  consulting:           "Consulenza",
  digital_maintenance:  "Manutenzione Digitale",
}

function GateRow({ label, approved, at }: { label: string; approved: boolean; at?: string | null }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-white/5 last:border-0">
      <span className="text-sm font-mono text-muted-foreground">{label}</span>
      {approved ? (
        <div className="flex items-center gap-1.5 text-green-400 text-xs font-mono">
          <CheckCircle className="w-3.5 h-3.5" />
          {at ? new Date(at).toLocaleDateString("it-IT") : "Approvato"}
        </div>
      ) : (
        <div className="flex items-center gap-1.5 text-amber-400 text-xs font-mono">
          <Clock className="w-3.5 h-3.5" />
          In attesa
        </div>
      )}
    </div>
  )
}

function TaskRow({ task }: { task: Record<string, unknown> }) {
  const status = String(task.status ?? "")
  const icon =
    status === "completed" ? <CheckCircle className="w-3.5 h-3.5 text-green-400 flex-shrink-0" /> :
    status === "failed"    ? <XCircle     className="w-3.5 h-3.5 text-red-400   flex-shrink-0" /> :
    status === "blocked"   ? <AlertCircle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" /> :
                             <Clock       className="w-3.5 h-3.5 text-blue-400  flex-shrink-0" />

  return (
    <div className="flex items-center gap-3 py-2 border-b border-white/5 last:border-0">
      {icon}
      <div className="flex-1 min-w-0">
        <p className="text-xs font-mono text-foreground truncate">{String(task.type ?? "")}</p>
        <p className="text-xs text-muted-foreground font-mono">{String(task.agent ?? "")}</p>
      </div>
      <span className="text-xs text-muted-foreground font-mono">
        {task.created_at ? new Date(String(task.created_at)).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) : ""}
      </span>
    </div>
  )
}

export default function DealPage() {
  const { id } = useParams<{ id: string }>()
  const router  = useRouter()

  const { data: deal } = useSWR<Deal>(
    id ? `deal/${id}` : null,
    () => getDeal(id),
    { refreshInterval: 8_000 },
  )
  const { data: runsData } = useSWR<{ items: RunSummary[] }>(
    id ? `runs/${id}` : null,
    () => listRuns({ deal_id: id }),
    { refreshInterval: 8_000 },
  )
  const { data: proposalsData } = useSWR<{ proposals: Proposal[] }>(
    id ? `proposals/${id}` : null,
    () => listProposals(id),
  )

  const latestProposal = proposalsData?.proposals?.[0]
  const runs = runsData?.items ?? []
  const latestRun = runs[0]

  return (
    <div className="min-h-screen bg-background text-foreground p-4 md:p-6">
      <div className="max-w-4xl mx-auto space-y-6">

        {/* Header */}
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="sm" onClick={() => router.push("/dashboard")} className="gap-2 text-muted-foreground">
            <ArrowLeft className="w-4 h-4" />
            Dashboard
          </Button>
          <h1 className="text-xl font-mono text-white">Deal {id?.slice(0, 8)}</h1>
        </div>

        {!deal ? (
          <p className="text-muted-foreground font-mono text-sm">Caricamento...</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

            {/* Info deal */}
            <Card className="bg-card/50 border-white/10">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-mono text-muted-foreground uppercase tracking-wider">
                  Info deal
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground font-mono">Servizio</span>
                  <span className="font-mono">{SERVICE_LABEL[deal.service_type] ?? deal.service_type}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground font-mono">Stato</span>
                  <span className="font-mono text-blue-300">{STATUS_LABEL[deal.status] ?? deal.status}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground font-mono">Settore</span>
                  <span className="font-mono">{deal.sector}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground font-mono">Valore est.</span>
                  <span className="font-mono">
                    {deal.estimated_value_eur != null ? `€ ${(deal.estimated_value_eur / 100).toFixed(2)}` : "—"}
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground font-mono">Creato</span>
                  <span className="font-mono">{new Date(deal.created_at).toLocaleDateString("it-IT")}</span>
                </div>
                {latestProposal?.presigned_url && (
                  <a
                    href={latestProposal.presigned_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block mt-3 text-xs font-mono text-blue-400 hover:text-blue-300 underline"
                  >
                    Scarica proposta PDF →
                  </a>
                )}
              </CardContent>
            </Card>

            {/* Gate */}
            <Card className="bg-card/50 border-white/10">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-mono text-muted-foreground uppercase tracking-wider">
                  Gate
                </CardTitle>
              </CardHeader>
              <CardContent>
                <GateRow
                  label="Gate 1 — Proposta approvata"
                  approved={deal.proposal_human_approved}
                />
                <GateRow
                  label="Gate 2 — Kickoff confermato"
                  approved={deal.kickoff_confirmed}
                />
                <GateRow
                  label="Gate 3 — Consegna approvata"
                  approved={deal.delivery_approved || deal.consulting_approved}
                />
              </CardContent>
            </Card>

            {/* Task history */}
            {latestRun && (
              <Card className="bg-card/50 border-white/10 md:col-span-2">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-mono text-muted-foreground uppercase tracking-wider">
                    Task history — Run {latestRun.run_id.slice(0, 8)}
                    <span className="ml-2 text-blue-400">{latestRun.status}</span>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {(latestRun as unknown as { task_history?: Record<string, unknown>[] }).task_history?.map((t, i) => (
                    <TaskRow key={i} task={t} />
                  )) ?? (
                    <p className="text-xs text-muted-foreground font-mono">Nessun task</p>
                  )}
                </CardContent>
              </Card>
            )}

          </div>
        )}
      </div>
    </div>
  )
}
