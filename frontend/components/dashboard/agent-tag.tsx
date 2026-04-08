import type { Agent } from "@/lib/lab-data"

export function AgentTag({ agent }: { agent: Agent }) {
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-mono bg-white/5 border border-white/10">
      <span
        className="w-1.5 h-1.5 rounded-full flex-shrink-0"
        style={{ backgroundColor: agent.accent }}
      />
      {agent.name}
    </span>
  )
}
