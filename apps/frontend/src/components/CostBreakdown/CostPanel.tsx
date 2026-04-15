import { useStore } from '../../store'

const FALLBACK_SPARKLINE = [35, 60, 45, 75, 38, 22, 18]

export function CostPanel() {
  const dailyCost  = useStore((s) => s.systemStatus.dailyCost)
  const llmStats   = useStore((s) => s.llmStats)

  /* Use REST-fetched total; fall back to rough estimate */
  const totalCost  = llmStats.totalCost > 0
    ? llmStats.totalCost
    : (dailyCost ?? 0) * 30

  /* Build sparkline from per_day data (last 7 days) */
  const sparkline = (() => {
    const days = Object.entries(llmStats.perDay)
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-7)
      .map(([, v]) => v)
    if (days.length === 0) return FALLBACK_SPARKLINE
    const max = Math.max(...days, 0.001)
    return days.map((v) => Math.round((v / max) * 100))
  })()

  /* Today's cost: prefer accumulated run cost if we have it, else dailyCost */
  const todayCost = llmStats.runCost > 0 ? llmStats.runCost : (dailyCost ?? 0)

  return (
    <div
      style={{
        padding: '8px 13px',
        display: 'flex',
        flexDirection: 'column',
        gap: 0,
        flex: 1,
        minHeight: 0,
        overflow: 'hidden',
      }}
    >
      {/* Mini title — matches .mini-title */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 6,
        }}
      >
        <span
          style={{
            fontFamily: 'var(--fh)',
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase' as const,
            color: 'var(--tm)',
          }}
        >
          Costo oggi
        </span>
        <span
          style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)' }}
        >
          Budget: $5.00/mese
        </span>
      </div>

      {/* .cost-row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          {/* .cost-val */}
          <div
            style={{
              fontFamily: 'var(--fd)',
              fontSize: 24,
              color: 'var(--accent)',
              fontWeight: 500,
            }}
          >
            ${todayCost.toFixed(3)}
          </div>
          {/* .cost-sub */}
          <div
            style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tf)', marginTop: 2 }}
          >
            Proiezione: ${((dailyCost ?? todayCost) * 30).toFixed(2)}/mese
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tf)' }}>
            Totale progetto
          </div>
          <div style={{ fontFamily: 'var(--fd)', fontSize: 14, color: 'var(--tm)', marginTop: 1 }}>
            ${totalCost.toFixed(2)}
          </div>
        </div>
      </div>

      {/* Sparkline */}
      <div
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 9,
          color: 'var(--tf)',
          marginTop: 8,
          marginBottom: 4,
        }}
      >
        Ultimi 7 giorni
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: 2,
          height: 22,
        }}
      >
        {sparkline.map((h, i) => {
          const isToday = i === sparkline.length - 1
          return (
            <div
              key={i}
              style={{
                flex: 1,
                borderRadius: '1px 1px 0 0',
                minWidth: 5,
                height: `${h}%`,
                background: isToday
                  ? 'rgba(45,232,106,.55)'
                  : 'rgba(45,232,106,.25)',
                transition: 'height .4s var(--e-out), background .2s',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.background = 'var(--accent)'
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.background = isToday
                  ? 'rgba(45,232,106,.55)'
                  : 'rgba(45,232,106,.25)'
              }}
            />
          )
        })}
      </div>
    </div>
  )
}
