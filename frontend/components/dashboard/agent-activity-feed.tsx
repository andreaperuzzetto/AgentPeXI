import {
  Play,
  CheckCircle,
  XCircle,
  PauseCircle,
  AlertTriangle,
  CheckCircle2,
  Flag,
} from "lucide-react"
import { Card } from "@/components/ui/card"
import type { Agent, EventType, PipelineEvent } from "@/lib/lab-data"
import { AgentTag } from "./agent-tag"
import { StatusBadge } from "./status-badge"

export interface AgentActivityFeedProps {
  events: PipelineEvent[]
  agents: Agent[]
}

function EventTypeIcon({ type }: { type: EventType }) {
  switch (type) {
    case "task_started":   return <Play         className="w-4 h-4 text-blue-400  flex-shrink-0" />
    case "task_completed": return <CheckCircle  className="w-4 h-4 text-green-400 flex-shrink-0" />
    case "task_failed":    return <XCircle      className="w-4 h-4 text-red-400   flex-shrink-0" />
    case "task_blocked":   return <PauseCircle  className="w-4 h-4 text-amber-400 flex-shrink-0" />
    case "gate_pending":   return <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0" />
    case "gate_approved":  return <CheckCircle2 className="w-4 h-4 text-green-400 flex-shrink-0" />
    case "run_completed":  return <Flag         className="w-4 h-4 text-blue-400  flex-shrink-0" />
  }
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString("it-IT", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

function eventTypeToStatus(type: EventType): string {
  switch (type) {
    case "task_failed":    return "blocked"
    case "task_completed":
    case "gate_approved":
    case "run_completed":  return "completed"
    case "task_blocked":
    case "gate_pending":   return "pending"
    case "task_started":   return "running"
  }
}

export function AgentActivityFeed({ events, agents }: AgentActivityFeedProps) {
  return (
    <Card
      className="overflow-auto backdrop-blur-sm bg-card/50 border-white/10"
      style={{ maxHeight: 520 }}
    >
      <div className="divide-y divide-white/5">
        {events.map((event) => {
          const agent = agents.find((a) => a.id === event.agent)
          const isWarning = event.type === "task_blocked" || event.type === "gate_pending"
          const isFailed  = event.type === "task_failed"
          return (
            <div
              key={event.id}
              className={`p-3 hover:bg-white/5 transition-colors ${
                isWarning
                  ? "bg-amber-500/5 border-l-2 border-amber-500"
                  : isFailed
                  ? "bg-red-500/5 border-l-2 border-red-500"
                  : ""
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3 flex-1 min-w-0">
                  <EventTypeIcon type={event.type} />
                  <span className="text-xs text-muted-foreground font-mono whitespace-nowrap">
                    {formatTime(event.timestamp)}
                  </span>
                  {agent && <AgentTag agent={agent} />}
                  <span className="text-sm text-foreground/80 truncate">{event.message}</span>
                </div>
                <StatusBadge status={eventTypeToStatus(event.type)} />
              </div>
            </div>
          )
        })}
        {events.length === 0 && (
          <div className="p-8 text-center text-muted-foreground font-mono text-sm">
            Nessun evento — avvia un run per iniziare.
          </div>
        )}
      </div>
    </Card>
  )
}
