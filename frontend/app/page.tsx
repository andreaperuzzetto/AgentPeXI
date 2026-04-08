"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import { Activity, Pause, Play } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  type Agent,
  type Deal,
  type EventType,
  type PipelineEvent,
  initialAgents,
  initialDeal,
  pipelineSteps,
} from "@/lib/lab-data"
import { StatsOverview } from "@/components/dashboard/stats-overview"
import { GateAlerts } from "@/components/dashboard/gate-alerts"
import { AgentActivityFeed } from "@/components/dashboard/agent-activity-feed"
import { DealKanban } from "@/components/dashboard/deal-kanban"
import { PipelineChart } from "@/components/dashboard/pipeline-chart"

function makeEvent(type: EventType, agentId: string, message: string): PipelineEvent {
  return {
    id:        crypto.randomUUID(),
    type,
    agent:     agentId,
    timestamp: Date.now(),
    message,
  }
}

export default function DashboardPage() {
  const [agents, setAgents]               = useState<Agent[]>(initialAgents)
  const [deal, setDeal]                   = useState<Deal>(initialDeal)
  const [events, setEvents]               = useState<PipelineEvent[]>([])
  const [isSimulating, setIsSimulating]   = useState(true)
  const [dismissedGates, setDismissedGates] = useState<string[]>([])

  const timeoutsRef = useRef<ReturnType<typeof setTimeout>[]>([])

  const clearAllTimeouts = useCallback(() => {
    timeoutsRef.current.forEach(clearTimeout)
    timeoutsRef.current = []
  }, [])

  const addTimeout = useCallback((fn: () => void, delay: number) => {
    const id = setTimeout(fn, delay)
    timeoutsRef.current.push(id)
    return id
  }, [])

  // Pipeline simulation
  useEffect(() => {
    if (!isSimulating) return

    const runStep = (stepIndex: number) => {
      if (stepIndex >= pipelineSteps.length) {
        addTimeout(() => {
          setAgents(initialAgents)
          setDeal(initialDeal)
          setEvents((prev) =>
            [makeEvent("run_completed", "scout", "Simulazione completata — riavvio"), ...prev].slice(0, 50)
          )
          runStep(0)
        }, 6000)
        return
      }

      const step = pipelineSteps[stepIndex]

      setAgents((prev) =>
        prev.map((a) => {
          if (step.agentIds.includes(a.id)) {
            return { ...a, status: "running" as const, task: step.tasks[a.id] ?? a.task }
          }
          if (a.status === "running") {
            return { ...a, status: "completed" as const }
          }
          return a
        })
      )

      step.agentIds.forEach((agentId) => {
        const agentName = initialAgents.find((a) => a.id === agentId)?.name ?? agentId
        const taskDesc  = step.tasks[agentId] ?? "task in esecuzione"
        setEvents((prev) =>
          [makeEvent("task_started", agentId, `${agentName}: ${taskDesc}`), ...prev].slice(0, 50)
        )
      })

      setDeal((prev) => ({
        ...prev,
        status:       step.dealStatus,
        currentPhase: step.dealPhase,
        progress:     step.dealProgress,
        gates:        step.gates,
      }))

      addTimeout(() => {
        setAgents((prev) =>
          prev.map((a) => {
            if (step.agentIds.includes(a.id)) {
              return {
                ...a,
                status: "completed" as const,
                logs:   [...a.logs, step.logs[a.id] ?? "Task completato"],
              }
            }
            if (a.status === "completed" && !step.agentIds.includes(a.id)) {
              return { ...a, status: "idle" as const, task: null }
            }
            return a
          })
        )

        step.agentIds.forEach((agentId) => {
          const logMessage = step.logs[agentId] ?? "Task completato"
          setEvents((prev) =>
            [makeEvent("task_completed", agentId, logMessage), ...prev].slice(0, 50)
          )
        })

        addTimeout(() => {
          runStep(stepIndex + 1)
        }, 1500)
      }, 4000)
    }

    addTimeout(() => {
      runStep(0)
    }, 2000)

    return () => clearAllTimeouts()
  }, [isSimulating, addTimeout, clearAllTimeouts])

  const stats = {
    leads:        agents.filter((a) => a.status !== "idle").length,
    activeDeals:  deal.progress < 100 ? 1 : 0,
    inDelivery:   deal.currentPhase === "delivery" ? 1 : 0,
    gatesPending: [
      !deal.gates.proposal_approved && deal.progress >= 45,
      !deal.gates.kickoff_confirmed  && deal.progress >= 55,
      !deal.gates.delivery_approved  && deal.progress >= 88,
    ].filter(Boolean).length,
  }

  const pendingGates = [
    (!deal.gates.proposal_approved && deal.progress >= 45)
      ? { dealId: deal.id, dealName: deal.leadName, gateType: "proposal" as const } : null,
    (!deal.gates.kickoff_confirmed  && deal.progress >= 55)
      ? { dealId: deal.id, dealName: deal.leadName, gateType: "kickoff"  as const } : null,
    (!deal.gates.delivery_approved  && deal.progress >= 88)
      ? { dealId: deal.id, dealName: deal.leadName, gateType: "delivery" as const } : null,
  ]
    .filter((g): g is NonNullable<typeof g> => g !== null)
    .filter((g) => !dismissedGates.includes(`${g.dealId}-${g.gateType}`))

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
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsSimulating(!isSimulating)}
              className="gap-2 bg-transparent text-white"
            >
              {isSimulating ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
              {isSimulating ? "Pausa" : "Avvia"}
            </Button>
          </div>
        </div>

        {/* Metriche */}
        <StatsOverview {...stats} />

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
            <AgentActivityFeed events={events} agents={agents} />
          </TabsContent>

          <TabsContent value="pipeline" className="space-y-4">
            <DealKanban deals={[deal]} />
          </TabsContent>

          <TabsContent value="metriche" className="space-y-4">
            <PipelineChart agents={agents} />
          </TabsContent>
        </Tabs>

      </div>
    </div>
  )
}

