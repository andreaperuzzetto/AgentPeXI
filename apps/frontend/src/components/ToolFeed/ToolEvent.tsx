import type { ToolEvent as ToolEventType } from '../../types'

export function ToolEventRow({ evt }: { evt: ToolEventType }) {
  const isErr = evt.status === 'error'

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '6px 1fr auto',
        alignItems: 'center',
        gap: 8,
        padding: '4px 10px',
        fontSize: '0.75rem',
      }}
    >
      <span
        className={isErr ? 'status-dot status-dot--err' : 'status-dot status-dot--ok'}
      />
      <div style={{ minWidth: 0 }}>
        <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{evt.tool}</span>
        <span style={{ color: 'var(--text-faint)', marginLeft: 4 }}>/{evt.action}</span>
      </div>
      <span className="font-data" style={{ color: 'var(--text-muted)', fontSize: '0.6875rem' }}>
        {evt.duration_ms}ms
      </span>
    </div>
  )
}
