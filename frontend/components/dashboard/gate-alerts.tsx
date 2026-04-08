import { AlertTriangle, X } from "lucide-react"
import { Button } from "@/components/ui/button"

const gateLabels: Record<string, string> = {
  proposal: "Gate 1 — Proposta da approvare",
  kickoff:  "Gate 2 — Kickoff da confermare",
  delivery: "Gate 3 — Consegna da approvare",
}

export interface GateAlertsProps {
  pendingGates: { dealId: string; dealName: string; gateType: "proposal" | "kickoff" | "delivery" }[]
  onDismiss: (dealId: string, gateType: string) => void
}

export function GateAlerts({ pendingGates, onDismiss }: GateAlertsProps) {
  if (pendingGates.length === 0) return null
  return (
    <div className="space-y-2">
      {pendingGates.map((gate) => (
        <div
          key={`${gate.dealId}-${gate.gateType}`}
          className="flex items-center justify-between p-3 rounded-lg bg-amber-500/10 border border-amber-500/30"
        >
          <div className="flex items-center gap-3">
            <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0" />
            <div>
              <p className="text-sm font-mono text-amber-300">{gateLabels[gate.gateType]}</p>
              <p className="text-xs text-muted-foreground">{gate.dealName}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="text-xs h-7 bg-transparent border-amber-500/50 text-amber-300 hover:bg-amber-500/20"
            >
              Rivedi
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 text-muted-foreground"
              onClick={() => onDismiss(gate.dealId, gate.gateType)}
            >
              <X className="w-3 h-3" />
            </Button>
          </div>
        </div>
      ))}
    </div>
  )
}
