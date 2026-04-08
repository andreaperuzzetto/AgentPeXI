const statusConfig: Record<string, { label: string; className: string }> = {
  idle:          { label: "Idle",           className: "bg-zinc-800 text-zinc-400 border-zinc-700" },
  pending:       { label: "In attesa",      className: "bg-amber-950 text-amber-400 border-amber-800" },
  running:       { label: "In esecuzione",  className: "bg-purple-950 text-purple-400 border-purple-800" },
  blocked:       { label: "Bloccato",       className: "bg-red-950 text-red-400 border-red-800" },
  completed:     { label: "Completato",     className: "bg-green-950 text-green-400 border-green-800" },
  awaiting_gate: { label: "Attende gate",   className: "bg-amber-950 text-amber-400 border-amber-800" },
  in_delivery:   { label: "In erogazione",  className: "bg-purple-950 text-purple-400 border-purple-800" },
}

export interface StatusBadgeProps {
  status: string
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const config = statusConfig[status] ?? { label: status, className: "bg-zinc-800 text-zinc-400 border-zinc-700" }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-mono border ${config.className}`}>
      {config.label}
    </span>
  )
}
