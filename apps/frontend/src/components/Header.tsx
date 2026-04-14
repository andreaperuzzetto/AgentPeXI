import { useStore } from '../store'

export function Header() {
  const wsConnected = useStore((s) => s.wsConnected)
  const { queueSize, activeTasks, uptime, dailyCost } = useStore((s) => s.systemStatus)

  return (
    <header
      style={{
        height: 48,
        background: 'var(--bg-surface-1)',
        borderBottom: '1px solid var(--border-strong)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 16px',
        gap: 24,
        flexShrink: 0,
      }}
    >
      {/* Brand */}
      <span
        style={{
          fontFamily: 'var(--font-display)',
          fontWeight: 800,
          fontSize: '1rem',
          letterSpacing: '-0.02em',
          color: 'var(--accent)',
        }}
      >
        AgentPeXI
      </span>

      {/* Connection status */}
      <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span
          className={wsConnected ? 'status-dot status-dot--ok' : 'status-dot status-dot--err'}
        />
        <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>
          {wsConnected ? 'Connesso' : 'Disconnesso'}
        </span>
      </span>

      {/* Spacer */}
      <span style={{ flex: 1 }} />

      {/* Metrics */}
      <MetricChip label="Coda" value={String(queueSize)} />
      <MetricChip label="Attivi" value={String(activeTasks)} />
      <MetricChip label="Uptime" value={uptime} />
      <MetricChip label="Costo oggi" value={`$${dailyCost.toFixed(3)}`} />
    </header>
  )
}

function MetricChip({ label, value }: { label: string; value: string }) {
  return (
    <span style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
      <span style={{ color: 'var(--text-faint)', fontSize: '0.6875rem' }}>{label}</span>
      <span
        className="font-data"
        style={{ color: 'var(--text-primary)', fontSize: '0.75rem' }}
      >
        {value}
      </span>
    </span>
  )
}
