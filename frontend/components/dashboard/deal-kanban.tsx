import { Card } from "@/components/ui/card"
import type { Deal, Zone } from "@/lib/lab-data"
import { ZONE_LABELS } from "@/lib/lab-data"
import { StatusBadge } from "./status-badge"

export interface DealKanbanProps {
  deals: Deal[]
}

const phases: Zone[] = ["discovery", "proposal", "delivery", "post_sale"]

export function DealKanban({ deals }: DealKanbanProps) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {phases.map((phase) => {
        const dealsInPhase = deals.filter((d) => d.currentPhase === phase)
        return (
          <div key={phase} className="space-y-2">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-wider">
                {ZONE_LABELS[phase]}
              </span>
              <span className="text-xs font-mono bg-white/10 px-1.5 py-0.5 rounded">
                {dealsInPhase.length}
              </span>
            </div>
            {dealsInPhase.map((deal) => (
              <Card
                key={deal.id}
                className="p-3 backdrop-blur-sm bg-card/50 border-white/10 hover:bg-white/5 cursor-pointer transition-colors"
              >
                <p className="text-sm font-mono truncate">{deal.leadName}</p>
                <p className="text-xs text-muted-foreground mt-0.5">{deal.serviceType}</p>
                <div className="flex items-center justify-between mt-2">
                  <StatusBadge status={deal.status.toLowerCase()} />
                  <div className="flex items-center gap-1">
                    <div className="w-16 h-1 bg-white/10 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-blue-500 rounded-full"
                        style={{ width: `${deal.progress}%` }}
                      />
                    </div>
                    <span className="text-xs text-muted-foreground font-mono">{deal.progress}%</span>
                  </div>
                </div>
              </Card>
            ))}
            {dealsInPhase.length === 0 && (
              <div className="p-4 text-center text-xs text-muted-foreground font-mono border border-dashed border-white/10 rounded-lg">
                Nessun deal
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
