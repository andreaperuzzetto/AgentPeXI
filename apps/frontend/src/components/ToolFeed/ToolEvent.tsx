import type { ToolEvent as ToolEventType } from '../../types'

const AGENT_ABBR: Record<string, string> = {
  research: 'RES', design: 'DES', publisher: 'PUB', analytics: 'ANA',
}

export function ToolEventRow({ evt }: { evt: ToolEventType }) {
  const isErr = evt.status === 'error'
  const abbr  = AGENT_ABBR[evt.agent] ?? evt.agent.slice(0, 3).toUpperCase()

  return (
    <div
      className="animate-fade-slide-in"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '4px 13px',
        borderBottom: '1px solid var(--b0)',
        transition: 'background .15s',
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'rgba(45,232,106,.03)' }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
    >
      {/* Status dot */}
      <span className={isErr ? 'status-dot status-dot--err' : 'status-dot status-dot--ok'} />

      {/* Agent badge — .ad-tag style */}
      <span
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 11,
          padding: '1px 5px',
          borderRadius: 3,
          background: 'var(--s3)',
          color: 'var(--tm)',
          flexShrink: 0,
          letterSpacing: '0.02em',
        }}
      >
        {abbr}
      </span>

      {/* Tool + action */}
      <div style={{ minWidth: 0, flex: 1 }}>
        <span
          style={{
            fontFamily: 'var(--fd)',
            fontSize: 14,
            color: 'var(--tp)',
            fontWeight: 500,
          }}
        >
          {evt.tool}
        </span>
        <span style={{ fontFamily: 'var(--fd)', fontSize: 13, color: 'var(--tf)', marginLeft: 4 }}>
          · {evt.action}
        </span>
      </div>

      {/* Cost */}
      {evt.cost_usd != null && (
        <span
          style={{
            fontFamily: 'var(--fd)',
            fontSize: 12,
            color: 'var(--tf)',
            flexShrink: 0,
          }}
        >
          ${evt.cost_usd.toFixed(4)}
        </span>
      )}

      {/* Duration */}
      <span
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 12,
          color: 'var(--tm)',
          flexShrink: 0,
        }}
      >
        {evt.duration_ms}ms
      </span>
    </div>
  )
}
