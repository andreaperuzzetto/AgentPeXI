import { useStore } from '../../store'

interface MiniItem {
  label: string
  value: string | number
  sub: string
  accent?: boolean
  err?: boolean
  faint?: boolean
}

export function AnalyticsMiniPanel({ onOpen }: { onOpen?: () => void }) {
  const agents = useStore((s) => s.agents)
  const summary = useStore((s) => s.analyticsSummary)

  // Derive counts from store
  // AgentStatusValue = 'idle' | 'running' | 'error' — 'done' is never set; idle = completed for display
  const allStatuses = Object.values(agents).map((a) => a?.status ?? 'idle')
  const running  = allStatuses.filter((s) => s === 'running').length

  const pipelineTotal = summary?.total ?? 0
  const completedTotal = summary?.completed ?? 0
  const failedTotal = summary?.failed ?? 0
  const successPct = pipelineTotal > 0 ? Math.round((completedTotal / pipelineTotal) * 100) : 0

  const items: MiniItem[] = [
    { label: 'Pipeline', value: pipelineTotal,   sub: running > 0 ? `${running} in corso` : '—',              accent: running > 0 },
    { label: 'Successi', value: completedTotal,   sub: pipelineTotal > 0 ? `${successPct}%` : '—',            accent: true },
    { label: 'Failures', value: failedTotal,      sub: pipelineTotal > 0 ? `${((failedTotal / pipelineTotal) * 100).toFixed(1)}%` : '0%', err: true },
    { label: 'Design',   value: summary?.production_queue?.completed ?? 0, sub: 'completati',              faint: true },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>

      {/* Mini title */}
      <div style={{
        padding: '7px 13px 6px',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <span style={{
          fontFamily: 'var(--fh)',
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.08em',
          textTransform: 'uppercase' as const,
          color: 'var(--tm)',
        }}>
          Analytics
        </span>
        <button
          style={{
            fontFamily: 'var(--fh)',
            fontWeight: 700,
            fontSize: 11,
            letterSpacing: '0.05em',
            textTransform: 'uppercase' as const,
            background: 'none',
            border: '1px solid var(--b0)',
            borderRadius: 4,
            padding: '3px 9px',
            color: 'var(--tm)',
            cursor: 'pointer',
            transition: 'border-color .22s var(--e-io), color .22s var(--e-io), background .22s var(--e-io)',
          }}
          onClick={onOpen}
          onMouseEnter={(e) => {
            const el = e.currentTarget as HTMLElement
            el.style.borderColor = 'var(--b1)'
            el.style.color = 'var(--accent)'
            el.style.background = 'rgba(45,232,106,.05)'
          }}
          onMouseLeave={(e) => {
            const el = e.currentTarget as HTMLElement
            el.style.borderColor = 'var(--b0)'
            el.style.color = 'var(--tm)'
            el.style.background = 'none'
          }}
        >
          Apri →
        </button>
      </div>

      {/* 2×2 grid */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: 6,
        padding: '0 13px 8px',
      }}>
        {items.map((item) => (
          <div
            key={item.label}
            style={{
              background: 'var(--s2)',
              border: '1px solid var(--b0)',
              borderRadius: 8,
              padding: '7px 10px',
              transition: 'border-color .25s var(--e-io), transform .25s var(--e-out)',
              cursor: 'default',
            }}
            onMouseEnter={(e) => {
              const el = e.currentTarget as HTMLElement
              el.style.borderColor = 'var(--b1)'
              el.style.transform = 'translateY(-1px)'
            }}
            onMouseLeave={(e) => {
              const el = e.currentTarget as HTMLElement
              el.style.borderColor = 'var(--b0)'
              el.style.transform = 'none'
            }}
          >
            <div style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)', letterSpacing: '0.03em', textTransform: 'uppercase' as const }}>
              {item.label}
            </div>
            <div style={{
              fontFamily: 'var(--fd)',
              fontSize: 22,
              fontWeight: 500,
              marginTop: 3,
              color: item.err ? 'var(--err)' : item.faint ? 'var(--tf)' : item.accent ? 'var(--accent)' : 'var(--tp)',
            }}>
              {item.value}
            </div>
            <div style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)', marginTop: 1 }}>
              {item.sub}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
