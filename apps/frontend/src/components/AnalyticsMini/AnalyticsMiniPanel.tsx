import { useStore } from '../../store'

/* ── helpers ─────────────────────────────────────────────────── */
function fmtTok(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000)     return `${(n / 1_000).toFixed(0)}K`
  return String(n)
}

/* ── Sparkline ───────────────────────────────────────────────── */
function Sparkline({ perDay }: { perDay: Record<string, number> }) {
  const days   = Object.keys(perDay).sort().slice(-14)
  if (days.length < 2) return null
  const values = days.map((d) => perDay[d] ?? 0)
  const max    = Math.max(...values, 0.001)
  const W = 320, H = 30
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * W
    const y = H - (v / max) * (H - 2) - 1
    return `${x},${y}`
  }).join(' ')

  return (
    <svg
      width="100%"
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      style={{ display: 'block', overflow: 'visible' }}
    >
      <polyline
        points={pts}
        fill="none"
        stroke="var(--acc)"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        opacity={0.6}
      />
    </svg>
  )
}

const costStr = (n: number) =>
  n === 0 ? '€0' : n < 0.01 ? `€${n.toFixed(4)}` : `€${n.toFixed(3)}`

/* ── component ───────────────────────────────────────────────── */
export function AnalyticsMiniPanel({ onOpen }: { onOpen?: () => void }) {
  const agents  = useStore((s) => s.agents)
  const llm     = useStore((s) => s.llmStats)
  const summary = useStore((s) => s.analyticsSummary)

  const runCost   = llm.runCost
  const totalCost = llm.totalCost
  const sparkData = llm.perDay   // Record<YYYY-MM-DD, number>

  const running   = Object.values(agents).filter((a) => a?.status === 'running').length
  const completed = summary?.completed ?? 0
  const failed    = summary?.failed    ?? 0
  const hasGlow   = running > 0

  const totalTok   = llm.tokenStats.total
  const cachePct   = llm.cacheStats.efficiencyPct
  const topAgent   = Object.entries(llm.perAgent).sort(([, a], [, b]) => b - a)[0]

  return (
    <>
      {/* ── header ── */}
      <div className="qcard-head">
        <span className="qcard-title">Analytics</span>
        <button className="qcard-action" onClick={onOpen}>Espandi →</button>
      </div>

      {/* ── body ── */}
      <div className="qcard-body">

        {/* Cost */}
        <div>
          <div className="a-cost-lbl" style={{ marginBottom: 4 }}>Costo sessione</div>
          <div className="a-cost-big">
            <span className="a-cost-val">{costStr(runCost)}</span>
            {totalCost > 0 && (
              <span className="a-cost-total">totale {costStr(totalCost)}</span>
            )}
          </div>
        </div>

        {/* 3-stat grid */}
        <div className="a-stats">
          <div className="a-stat">
            <span className="a-stat-lbl">Completati</span>
            <span className="a-stat-val ok">{completed}</span>
          </div>
          <div className="a-stat">
            <span className="a-stat-lbl">Falliti</span>
            <span className="a-stat-val err">{failed}</span>
          </div>
          <div className="a-stat">
            <span className="a-stat-lbl">Running</span>
            <span className="a-stat-val dim">{running}</span>
          </div>
        </div>

        {/* Sparkline */}
        {Object.keys(sparkData).length > 1 && (
          <div className="sparkline-wrap">
            <span className="sparkline-lbl">14 giorni</span>
            <Sparkline perDay={sparkData} />
          </div>
        )}

        {/* Token + cache row */}
        {totalTok > 0 && (
          <div className="a-agents-row" style={{ justifyContent: 'space-between' }}>
            <span className="a-agents-txt">{fmtTok(totalTok)} token</span>
            {cachePct > 0 && (
              <span style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--ok)' }}>
                {cachePct.toFixed(0)}% cache
              </span>
            )}
          </div>
        )}

        {/* Top agent cost */}
        {topAgent && topAgent[1] > 0 && (
          <div className="a-agents-row" style={{ justifyContent: 'space-between' }}>
            <span className="a-agents-txt" style={{ textTransform: 'uppercase', fontSize: 10, letterSpacing: '.04em' }}>
              {topAgent[0]}
            </span>
            <span style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tm)' }}>
              ${topAgent[1].toFixed(3)}
            </span>
          </div>
        )}

        {/* Agents row */}
        <div className="a-agents-row">
          <span
            className="a-agents-dot"
            style={{
              background: hasGlow ? 'var(--ok)' : 'var(--tf)',
              boxShadow:  hasGlow ? '0 0 6px rgba(27,255,94,.6)' : 'none',
              animation:  hasGlow ? 'pdot 1.6s ease-in-out infinite' : 'none',
            }}
          />
          <span className="a-agents-txt">
            {running > 0
              ? `${running} agente${running > 1 ? 'i' : ''} attivo${running > 1 ? 'i' : ''}`
              : 'Nessun agente attivo'}
          </span>
        </div>

      </div>
    </>
  )
}
