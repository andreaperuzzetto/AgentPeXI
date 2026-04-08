import { Search, Activity, Zap, AlertTriangle } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"

export interface StatsOverviewProps {
  leads: number
  activeDeals: number
  inDelivery: number
  gatesPending: number
}

const metrics = [
  { key: "leads",        label: "Lead totali",      Icon: Search,        color: "text-blue-400" },
  { key: "activeDeals",  label: "Deal attivi",       Icon: Activity,      color: "text-green-400" },
  { key: "inDelivery",   label: "In erogazione",     Icon: Zap,           color: "text-purple-400" },
  { key: "gatesPending", label: "Gate in attesa",    Icon: AlertTriangle, color: "text-amber-400" },
] as const

export function StatsOverview({ leads, activeDeals, inDelivery, gatesPending }: StatsOverviewProps) {
  const values: Record<string, number> = { leads, activeDeals, inDelivery, gatesPending }

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {metrics.map(({ key, label, Icon, color }) => (
        <Card key={key} className="backdrop-blur-sm bg-card/50 border-white/10">
          <CardContent className="p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">{label}</p>
                <p className="text-2xl font-bold font-mono mt-1">{values[key]}</p>
              </div>
              <Icon className={`w-8 h-8 ${color}`} strokeWidth={1} />
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
