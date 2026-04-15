import { useStore } from '../store'

export function Header() {
  const wsConnected = useStore((s) => s.wsConnected)
  const { queueSize, activeTasks, uptime, dailyCost } = useStore((s) => s.systemStatus)

  return (
    <header
      style={{
        height: 48,
        background: 'var(--s1)',
        borderBottom: '1px solid var(--b0)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 16px',
        gap: 16,
        flexShrink: 0,
      }}
    >
      {/* Brand */}
      <span
        style={{
          fontFamily: 'var(--fh)',
          fontWeight: 800,
          fontSize: 16,
          letterSpacing: '-0.02em',
          color: 'var(--accent)',
        }}
      >
        AgentPeXI
      </span>

      {/* Connection status */}
      <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        <span className={wsConnected ? 'live-dot' : 'status-dot status-dot--err'} />
        <span style={{ fontFamily: 'var(--fb)', fontSize: 13, color: 'var(--tm)' }}>
          {wsConnected ? 'Connesso' : 'Disconnesso'}
        </span>
      </span>

      {/* Spacer */}
      <span style={{ flex: 1 }} />

      {/* Metrics */}
      <MetricChip label="Coda"       value={String(queueSize)} />
      <MetricChip label="Attivi"     value={String(activeTasks)} accent={activeTasks > 0} />
      <MetricChip label="Uptime"     value={uptime} />
      <MetricChip label="Costo oggi" value={`$${dailyCost.toFixed(3)}`} />
    </header>
  )
}

function MetricChip({
  label,
  value,
  accent = false,
}: {
  label: string
  value: string
  accent?: boolean
}) {
  return (
    <span style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
      <span
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 11,
          color: 'var(--tf)',
          letterSpacing: '0.03em',
        }}
      >
        {label}
      </span>
      <span
        className="font-data"
        style={{
          fontSize: 13,
          color: accent ? 'var(--accent)' : 'var(--tp)',
        }}
      >
        {value}
      </span>
    </span>
  )
}
