import { useStore } from '../store'

export function Header() {
  const wsConnected = useStore((s) => s.wsConnected)
  const { queueSize, activeTasks, uptime, dailyCost } = useStore((s) => s.systemStatus)
  const mockMode = useStore((s) => s.systemStatus?.mock_mode)
  const activeDomain = useStore((s) => s.activeDomain)
  const setActiveDomain = useStore((s) => s.setActiveDomain)

  function handleDomainSwitch(domain: 'etsy' | 'personal') {
    if (domain === activeDomain) return
    setActiveDomain(domain)
    fetch('/api/domain', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domain }),
    }).catch(() => {})
  }

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
          fontSize: 18,
          letterSpacing: '-0.02em',
          color: 'var(--accent)',
        }}
      >
        AgentPeXI
      </span>

      {/* Status pill */}
      <span style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        background: 'var(--s2)',
        border: '1px solid var(--b0)',
        borderRadius: 99,
        padding: '3px 10px 3px 8px',
        transition: 'background .3s var(--e-io)',
      }}>
        <span className={wsConnected ? 'live-dot' : 'status-dot status-dot--err'} />
        <span style={{ fontFamily: 'var(--fb)', fontSize: 11, color: 'var(--tm)', lineHeight: 1 }}>
          {wsConnected ? 'Connesso' : 'Disconnesso'}
        </span>
        {mockMode && (
          <>
            <span style={{ width: 1, height: 10, background: 'var(--b0)', margin: '0 2px' }} />
            <span style={{
              fontFamily: 'var(--fd)',
              fontSize: 9,
              letterSpacing: '0.08em',
              color: 'var(--warn)',
              lineHeight: 1,
            }}>
              MOCK
            </span>
          </>
        )}
      </span>

      {/* Spacer */}
      <span style={{ flex: 1 }} />

      {/* Metrics */}
      <MetricChip label="Coda"       value={String(queueSize)} />
      <MetricChip label="Attivi"     value={String(activeTasks)} accent={activeTasks > 0} />
      <MetricChip label="Uptime"     value={uptime} />
      <MetricChip label="Costo oggi" value={`$${dailyCost.toFixed(3)}`} />

      {/* Domain toggle */}
      <span style={{
        display: 'inline-flex',
        alignItems: 'center',
        background: 'var(--s3)',
        border: '1px solid var(--b0)',
        borderRadius: 99,
        padding: 2,
        gap: 2,
        marginLeft: 8,
      }}>
        {(['personal', 'etsy'] as const).map((d) => (
          <button
            key={d}
            onClick={() => handleDomainSwitch(d)}
            style={{
              height: 24,
              padding: '0 12px',
              borderRadius: 99,
              border: 'none',
              cursor: activeDomain === d ? 'default' : 'pointer',
              fontFamily: 'var(--fd)',
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: '0.08em',
              textTransform: 'uppercase' as const,
              transition: 'background .25s var(--e-io), color .25s var(--e-io)',
              background: activeDomain === d ? 'var(--accent)' : 'transparent',
              color: activeDomain === d ? 'var(--base)' : 'var(--tf)',
            }}
          >
            {d === 'personal' ? 'PSN' : 'ETY'}
          </button>
        ))}
      </span>
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
          fontSize: 13,
          color: 'var(--tf)',
          letterSpacing: '0.03em',
        }}
      >
        {label}
      </span>
      <span
        className="font-data"
        style={{
          fontSize: 15,
          color: accent ? 'var(--accent)' : 'var(--tp)',
        }}
      >
        {value}
      </span>
    </span>
  )
}
