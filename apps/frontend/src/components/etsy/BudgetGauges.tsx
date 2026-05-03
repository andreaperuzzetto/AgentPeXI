/**
 * BudgetGauges — pannello 3 gauge radiali: LLM · Image · Fee
 *
 * FE-Blocco 4, Step 4.2
 *
 * Dati (dallo store, popolati da Shell.tsx ogni 30s via /api/costs):
 *   LLM:   llmStats.runCost      vs dailySlice (budgetMonthlyUsd / 30)
 *   Image: imageCostToday        vs dailySlice
 *   Fee:   feeCostToday          vs dailySlice
 *
 * Layout asimmetrico: LLM (flex 1.4) | Image (flex 1.0) | Fee (flex 0.8)
 * Il gauge con pct più alta ha scale(1.02) applicato.
 * Warning: colore #FF4444 quando pct >= 80%.
 *
 * States:
 *   budgetMonthlyUsd === null → skeleton (dati non ancora arrivati)
 *   budgetMonthlyUsd > 0     → gauge live
 *   budgetMonthlyUsd === 0   → gauge con limite — (no budget configurato)
 */

import { useMemo }       from 'react'
import { motion }        from 'framer-motion'
import { useStore }      from '../../store'
import { RadialGauge }   from '../ui/RadialGauge'

/* ── Gauge config ─────────────────────────────────────────────────────────── */

interface GaugeConfig {
  key:      string
  label:    string
  color:    string
  flex:     number   // layout weight (not equal columns)
  size:     number   // SVG size px
  delay:    number   // mount stagger
}

const GAUGES: GaugeConfig[] = [
  { key: 'llm',   label: 'LLM',   color: '#F5A623', flex: 1.4, size: 128, delay: 0.0 },
  { key: 'image', label: 'Image', color: '#B57BFF', flex: 1.0, size: 110, delay: 0.08 },
  { key: 'fee',   label: 'Fee',   color: '#C8C8FF', flex: 0.8, size: 98,  delay: 0.16 },
]

/* ── Skeleton gauge ───────────────────────────────────────────────────────── */
function SkeletonGauge({ size, flex, delay }: { size: number; flex: number; delay: number }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay, duration: 0.3 }}
      style={{
        flex:           flex,
        display:        'flex',
        flexDirection:  'column',
        alignItems:     'center',
        gap:            8,
      }}
    >
      <div style={{
        width:        size,
        height:       size,
        borderRadius: '50%',
        background:   'rgba(255,255,255,0.04)',
        border:       '5px solid rgba(255,255,255,0.06)',
      }} />
      <div style={{
        height: 8, width: size * 0.45, borderRadius: 3,
        background: 'rgba(255,255,255,0.05)',
      }} />
    </motion.div>
  )
}

/* ── Helpers ──────────────────────────────────────────────────────────────── */

function usdStr(v: number): string {
  if (v <= 0) return '$0'
  if (v < 0.01) return '<$0.01'
  return `$${v.toFixed(2)}`
}

function pctOf(value: number, limit: number): number {
  if (limit <= 0) return 0
  return Math.round((value / limit) * 100)
}

/* ── BudgetGauges ─────────────────────────────────────────────────────────── */
export function BudgetGauges() {
  const llmRunCost       = useStore(s => s.llmStats.runCost)
  const budgetMonthlyUsd = useStore(s => s.budgetMonthlyUsd)
  const imageCostToday   = useStore(s => s.imageCostToday)
  const feeCostToday     = useStore(s => s.feeCostToday)

  /* Daily budget slice — the common reference for all three gauges */
  const dailySlice = budgetMonthlyUsd != null && budgetMonthlyUsd > 0
    ? budgetMonthlyUsd / 30
    : 0

  /* Computed pct values */
  const pcts = useMemo(() => ({
    llm:   pctOf(llmRunCost,     dailySlice),
    image: pctOf(imageCostToday, dailySlice),
    fee:   pctOf(feeCostToday,   dailySlice),
  }), [llmRunCost, imageCostToday, feeCostToday, dailySlice])

  /* Which gauge has the highest pct — gets scale(1.02) */
  const maxKey = useMemo(() => {
    const entries = Object.entries(pcts) as [string, number][]
    const max = entries.reduce((a, b) => (b[1] > a[1] ? b : a))
    return max[1] > 0 ? max[0] : null
  }, [pcts])

  const values: Record<string, number> = {
    llm:   llmRunCost,
    image: imageCostToday,
    fee:   feeCostToday,
  }

  const isSkeleton = budgetMonthlyUsd === null

  /* Sub-label: daily limit */
  const subLabelFor = (key: string): string => {
    if (dailySlice <= 0) return 'no budget'
    const limitStr = `$${dailySlice.toFixed(2)}/g`
    return key === 'llm' ? `limit ${limitStr}` : `vs ${limitStr}`
  }

  return (
    <div style={{
      background:     'rgba(13,15,18,0.72)',
      border:         '1px solid rgba(255,255,255,0.07)',
      borderRadius:   10,
      padding:        '16px 18px 14px',
      backdropFilter: 'blur(12px)',
      boxShadow:      'inset 0 1px 0 rgba(255,255,255,0.07)',
    }}>
      {/* ── Section header ─────────────────────────────────────────────── */}
      <div className="hud-label" style={{ marginBottom: 16 }}>
        [ BUDGET ]
      </div>

      {/* ── Gauges row ─────────────────────────────────────────────────── */}
      <div style={{
        display:     'flex',
        alignItems:  'center',
        gap:         12,
        justifyContent: 'space-around',
      }}>
        {isSkeleton ? (
          /* ── Skeleton ─────────────────────────────────────────────── */
          GAUGES.map(g => (
            <SkeletonGauge key={g.key} size={g.size} flex={g.flex} delay={g.delay} />
          ))
        ) : (
          /* ── Live gauges ───────────────────────────────────────────── */
          GAUGES.map(g => {
            const pct  = pcts[g.key as keyof typeof pcts]
            const val  = values[g.key]
            const isMax = g.key === maxKey

            return (
              <motion.div
                key={g.key}
                animate={{ scale: isMax ? 1.02 : 1 }}
                transition={{ type: 'spring', stiffness: 200, damping: 20 }}
                style={{
                  flex:          g.flex,
                  display:       'flex',
                  justifyContent:'center',
                }}
              >
                <RadialGauge
                  label={g.label}
                  valueStr={usdStr(val)}
                  pct={pct}
                  color={g.color}
                  size={g.size}
                  delay={g.delay}
                  subLabel={subLabelFor(g.key)}
                />
              </motion.div>
            )
          })
        )}
      </div>

      {/* ── Footer: daily slice reference ──────────────────────────────── */}
      {!isSkeleton && dailySlice > 0 && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.4, duration: 0.3 }}
          style={{
            display:        'flex',
            justifyContent: 'flex-end',
            marginTop:      10,
            paddingTop:     8,
            borderTop:      '1px solid rgba(255,255,255,0.05)',
          }}
        >
          <span className="mono-num" style={{
            fontSize:      9,
            color:         'rgba(255,255,255,0.22)',
            letterSpacing: '0.05em',
          }}>
            budget {budgetMonthlyUsd != null ? `$${budgetMonthlyUsd.toFixed(0)}/mo` : '—'}
            {' · '}
            daily slice ${dailySlice.toFixed(2)}
          </span>
        </motion.div>
      )}
    </div>
  )
}
