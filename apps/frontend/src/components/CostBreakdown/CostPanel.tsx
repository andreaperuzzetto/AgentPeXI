import { useStore } from '../../store'

const FALLBACK_SPARKLINE = [35, 60, 45, 75, 38, 22, 18]

export function CostPanel() {
  const dailyCost  = useStore((s) => s.systemStatus.dailyCost)
  const llmStats   = useStore((s) => s.llmStats)
  const budgetMonthlyUsd = useStore((s) => s.budgetMonthlyUsd)

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

  /* sessionCost = accumulo WS da quando la tab è aperta (si azzera al refresh)
     dailyCost   = totale oggi dal DB (aggiornato ogni 30s dal REST) */
  const sessionCost = llmStats.runCost
  const todayCost = dailyCost ?? 0

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
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase' as const,
            color: 'var(--tm)',
          }}
        >
          Costo
        </span>
        <span
          style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)' }}
        >
          Budget: {budgetMonthlyUsd ? `$${budgetMonthlyUsd.toFixed(0)}/mese` : '—'}
        </span>
      </div>

      {/* .cost-row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          {/* sessione corrente — si azzera al refresh */}
          <div
            style={{
              fontFamily: 'var(--fd)',
              fontSize: 26,
              color: 'var(--accent)',
              fontWeight: 500,
            }}
          >
            ${sessionCost.toFixed(3)}
          </div>
          <div
            style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tf)', marginTop: 2 }}
          >
            questa sessione
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tf)' }}>
            oggi
          </div>
          <div style={{ fontFamily: 'var(--fd)', fontSize: 16, color: 'var(--tm)', marginTop: 1 }}>
            ${todayCost.toFixed(3)}
          </div>
          <div style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)', marginTop: 2 }}>
            proj. ${(todayCost * 30).toFixed(2)}/mese
          </div>
        </div>
      </div>

      {/* Sparkline */}
      <div
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 11,
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
