import { useEffect, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { useStore } from '../store'

/* ── uptime hook ─────────────────────────────────────────────── */
function fmt(connectedAt: number | null): string {
  if (!connectedAt) return '00:00:00'
  const ms = Date.now() - connectedAt
  const s  = Math.floor(ms / 1000) % 60
  const m  = Math.floor(ms / 60_000) % 60
  const h  = Math.floor(ms / 3_600_000)
  return [h, m, s].map((n) => String(n).padStart(2, '0')).join(':')
}

function useUptime(connectedAt: number | null): string {
  const [uptime, setUptime] = useState(() => fmt(connectedAt))

  useEffect(() => {
    setUptime(fmt(connectedAt))
    const id = setInterval(() => setUptime(fmt(connectedAt)), 1000)
    return () => clearInterval(id)
  }, [connectedAt])

  return uptime
}

/* ── component ───────────────────────────────────────────────── */
export function Header() {
  const { wsConnected, queueSize, activeTasks, runCost, connectedAt, activeDomain, setActiveDomain } =
    useStore(
      useShallow((s) => ({
        wsConnected:    s.wsConnected,
        queueSize:      s.systemStatus.queueSize,
        activeTasks:    s.systemStatus.activeTasks,
        runCost:        s.llmStats.runCost,
        connectedAt:    s.connectedAt,
        activeDomain:   s.activeDomain,
        setActiveDomain: s.setActiveDomain,
      }))
    )

  const uptime  = useUptime(connectedAt)
  const queue   = activeTasks > 0 ? activeTasks : queueSize
  const costStr = runCost === 0 ? '€0' : runCost < 0.01 ? `€${runCost.toFixed(4)}` : `€${runCost.toFixed(3)}`

  return (
    <header>
      {/* logo */}
      <span className="h-logo">AgentPeXI</span>

      {/* live dot + label */}
      <div className="h-item">
        <span className={`live-dot${wsConnected ? '' : ' off'}`} />
        <span>{wsConnected ? 'live' : 'offline'}</span>
      </div>

      <span className="h-sep">·</span>

      {/* queue */}
      <div className="h-item">
        queue:&nbsp;
        <span style={{ color: queue > 0 ? 'var(--acc)' : 'var(--tf)' }}>{queue}</span>
      </div>

      <span className="h-sep">·</span>

      {/* cost */}
      <div className="h-item">{costStr}</div>

      <span className="h-sep">·</span>

      {/* uptime */}
      <div className="h-item" style={{ color: 'var(--tf)' }}>
        {uptime}
      </div>

      {/* domain badge — zone-aware color */}
      <button
        className="h-badge"
        onClick={() => setActiveDomain(activeDomain === 'etsy' ? 'personal' : 'etsy')}
        style={activeDomain === 'etsy' ? {
          color:       'var(--zone-etsy)',
          borderColor: 'rgba(245,166,35,.32)',
          background:  'rgba(245,166,35,.09)',
        } : {
          color:       'var(--zone-personal)',
          borderColor: 'rgba(27,255,94,.20)',
          background:  'var(--adim)',
        }}
      >
        {activeDomain === 'etsy' ? 'ETY' : 'PSN'}
      </button>
    </header>
  )
}
