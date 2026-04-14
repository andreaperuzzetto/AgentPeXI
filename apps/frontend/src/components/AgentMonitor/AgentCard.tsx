import type { AgentState } from '../../types'

const AGENT_LABELS: Record<string, string> = {
  research: 'Research',
  design: 'Design',
  publisher: 'Publisher',
  analytics: 'Analytics',
  customer_service: 'Customer Service',
  finance: 'Finance',
}

const STATUS_LABELS: Record<string, string> = {
  idle: 'Inattivo',
  running: 'In esecuzione',
  error: 'Errore',
}

export function AgentCard({ name, state }: { name: string; state: AgentState }) {
  const dotClass =
    state.status === 'running'
      ? 'status-dot status-dot--warn'
      : state.status === 'error'
        ? 'status-dot status-dot--err'
        : 'status-dot status-dot--ok'

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '6px 12px',
        background: state.status === 'error' ? 'oklch(12% 0.02 20 / 0.3)' : 'transparent',
        borderRadius: 2,
        transition: 'background 150ms var(--ease-out-quart)',
      }}
    >
      <span className={dotClass} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: '0.8125rem', fontWeight: 500, color: 'var(--text-primary)' }}>
          {AGENT_LABELS[name] ?? name}
        </div>
        {state.lastTask && (
          <div
            style={{
              fontSize: '0.6875rem',
              color: 'var(--text-faint)',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {state.lastTask}
          </div>
        )}
      </div>
      <span
        style={{
          fontSize: '0.625rem',
          color: 'var(--text-muted)',
          textTransform: 'uppercase' as const,
          letterSpacing: '0.06em',
          fontFamily: 'var(--font-display)',
          fontWeight: 700,
          flexShrink: 0,
        }}
      >
        {STATUS_LABELS[state.status] ?? state.status}
      </span>
    </div>
  )
}
