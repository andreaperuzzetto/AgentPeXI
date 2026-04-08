import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { Agent } from "@/lib/lab-data"

export interface PipelineChartProps {
  agents: Agent[]
}

export function PipelineChart({ agents }: PipelineChartProps) {
  const data = [
    { name: "Idle",          value: agents.filter((a) => a.status === "idle").length,      fill: "var(--color-chart-3)" },
    { name: "In esecuzione", value: agents.filter((a) => a.status === "running").length,   fill: "var(--color-chart-1)" },
    { name: "Completato",    value: agents.filter((a) => a.status === "completed").length, fill: "var(--color-chart-2)" },
    { name: "Bloccato",      value: agents.filter((a) => a.status === "blocked").length,   fill: "var(--color-chart-5)" },
  ]

  return (
    <Card className="backdrop-blur-sm bg-card/50 border-white/10">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-mono text-muted-foreground uppercase tracking-wider">
          Distribuzione stati agenti
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis
              dataKey="name"
              tick={{ fill: "var(--color-muted-foreground)", fontSize: 11, fontFamily: "var(--font-mono)" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              allowDecimals={false}
              tick={{ fill: "var(--color-muted-foreground)", fontSize: 11, fontFamily: "var(--font-mono)" }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "var(--color-card)",
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: "var(--radius)",
                fontFamily: "var(--font-mono)",
                fontSize: 12,
              }}
              labelStyle={{ color: "var(--color-foreground)" }}
              itemStyle={{ color: "var(--color-muted-foreground)" }}
              cursor={{ fill: "rgba(255,255,255,0.03)" }}
            />
            <Bar dataKey="value" radius={[4, 4, 0, 0]}>
              {data.map((entry, index) => (
                <Cell key={index} fill={entry.fill} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
