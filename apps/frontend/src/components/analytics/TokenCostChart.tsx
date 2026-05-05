/**
 * TokenCostChart — FE-Blocco 5.1
 *
 * Due chart recharts in una GlassCard:
 *   1. AreaChart 7gg — costo LLM giornaliero (da store llmStats.perDay)
 *   2. BarChart per agente — costo per agente (da store llmStats.perAgent)
 *
 * Styling: --zone-analytics (#94A3B8), nessun colore recharts default,
 * CartesianGrid stroke rgba(255,255,255,0.05), tooltip .glass custom.
 * Skeleton: rettangolo animato durante assenza dati.
 */

import { useMemo } from 'react'
import {
  AreaChart, Area, BarChart, Bar, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { useStore } from '../../store'

const ZONE_COLOR = '#94A3B8'

// ── Custom glass tooltip ──────────────────────────────────────────────────────

function GlassTooltip({ active, payload, label }: {
  active?:  boolean
  payload?: Array<{ value: number }>
  label?:   string
}) {
  if (!active || !payload?.length) return null
  const val = payload[0].value
  return (
    <div
      className="glass"
      style={{
        borderRadius:  8,
        padding:       '7px 12px',
        fontFamily:    'var(--fmo)',
        fontSize:      11,
        color:         'var(--tp)',
        pointerEvents: 'none',
        whiteSpace:    'nowrap',
      }}
    >
      <div style={{ color: 'var(--tm)', marginBottom: 2 }}>{label}</div>
      <div style={{ color: ZONE_COLOR }}>${val.toFixed(4)}</div>
    </div>
  )
}

function AgentTooltip({ active, payload, label }: {
  active?:  boolean
  payload?: Array<{ value: number }>
  label?:   string
}) {
  if (!active || !payload?.length) return null
  return (
    <div
      className="glass"
      style={{
        borderRadius:  8,
        padding:       '7px 12px',
        fontFamily:    'var(--fmo)',
        fontSize:      11,
        color:         'var(--tp)',
        pointerEvents: 'none',
        whiteSpace:    'nowrap',
      }}
    >
      <div style={{ color: 'var(--tm)', marginBottom: 2 }}>{label}</div>
      <div style={{ color: ZONE_COLOR }}>${payload[0].value.toFixed(4)}</div>
    </div>
  )
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function SkeletonRect({ height }: { height: number }) {
  return (
    <div style={{
      height,
      borderRadius:    6,
      background:      'rgba(148,163,184,0.08)',
      animation:       'skel-pulse 1.8s ease-in-out infinite',
    }} />
  )
}

// ── Panel section label ───────────────────────────────────────────────────────

function SectionLabel({ children }: { children: string }) {
  return (
    <div style={{
      fontFamily:    'var(--fmo)',
      fontSize:      9,
      fontWeight:    700,
      letterSpacing: '0.12em',
      textTransform: 'uppercase',
      color:         ZONE_COLOR,
      opacity:       0.7,
      marginBottom:  8,
    }}>
      {children}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

const DOW = ['D','L','M','M','G','V','S']
function dayLabel(dateStr: string): string {
  const d = new Date(dateStr + 'T12:00:00')
  return `${DOW[d.getDay()]} ${d.getDate()}`
}

export function TokenCostChart() {
  const perDay   = useStore((s) => s.llmStats.perDay)
  const perAgent = useStore((s) => s.llmStats.perAgent)

  // 7gg per AreaChart
  const areaData = useMemo(() => {
    return Object.entries(perDay)
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-7)
      .map(([date, cost]) => ({ day: dayLabel(date), cost }))
  }, [perDay])

  // Per-agent per BarChart
  const agentData = useMemo(() => {
    return Object.entries(perAgent)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 6)
      .map(([name, cost]) => ({
        name: name.charAt(0).toUpperCase() + name.slice(1),
        cost,
      }))
  }, [perAgent])

  // Staggered bar colors cycling around zone palette
  const BAR_COLORS = [
    '#94A3B8', '#7EB8FF', '#B57BFF', '#4ADE80', '#F5A623', '#FF4444',
  ]

  const hasAreaData  = areaData.length > 0
  const hasAgentData = agentData.length > 0

  return (
    <div style={{
      background:     'rgba(13,15,18,0.72)',
      border:         '1px solid rgba(255,255,255,0.07)',
      borderRadius:   10,
      padding:        '18px 20px 14px',
      backdropFilter: 'blur(12px)',
      boxShadow:      'inset 0 1px 0 rgba(255,255,255,0.07)',
    }}>
      {/* ── Header ── */}
      <div style={{
        display:        'flex',
        alignItems:     'baseline',
        gap:            10,
        marginBottom:   18,
      }}>
        <span style={{
          fontFamily:    'var(--fui)',
          fontSize:      13,
          fontWeight:    600,
          color:         'var(--tp)',
          letterSpacing: '0.01em',
        }}>
          Token &amp; Cost
        </span>
        <span style={{
          fontFamily: 'var(--fmo)',
          fontSize:   10,
          color:      'var(--tf)',
        }}>
          LLM spend overview
        </span>
      </div>

      {/* ── Area chart 7gg ── */}
      <SectionLabel>Costo giornaliero — 7gg</SectionLabel>
      {hasAreaData ? (
        <ResponsiveContainer width="100%" height={90}>
          <AreaChart data={areaData} margin={{ top: 4, right: 4, left: -24, bottom: 0 }}>
            <defs>
              <linearGradient id="tcc-area-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%"   stopColor={ZONE_COLOR} stopOpacity={0.28} />
                <stop offset="100%" stopColor={ZONE_COLOR} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
            <XAxis
              dataKey="day"
              tick={{ fontFamily: 'var(--fmo)', fontSize: 8, fill: 'var(--tf)' }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fontFamily: 'var(--fmo)', fontSize: 8, fill: 'var(--tf)' }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => `$${(v as number).toFixed(3)}`}
            />
            <Tooltip
              content={<GlassTooltip />}
              cursor={{ stroke: 'rgba(255,255,255,0.08)', strokeWidth: 1 }}
            />
            <Area
              type="monotone"
              dataKey="cost"
              stroke={ZONE_COLOR}
              strokeWidth={1.5}
              fill="url(#tcc-area-fill)"
              dot={false}
              activeDot={{ r: 3, fill: ZONE_COLOR, stroke: 'none' }}
            />
          </AreaChart>
        </ResponsiveContainer>
      ) : (
        <SkeletonRect height={90} />
      )}

      {/* ── Agent bar chart ── */}
      <SectionLabel>Costo per agente</SectionLabel>
      {hasAgentData ? (
        <ResponsiveContainer width="100%" height={80}>
          <BarChart data={agentData} margin={{ top: 0, right: 4, left: -24, bottom: 0 }} barSize={14}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
            <XAxis
              dataKey="name"
              tick={{ fontFamily: 'var(--fmo)', fontSize: 8, fill: 'var(--tf)' }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fontFamily: 'var(--fmo)', fontSize: 8, fill: 'var(--tf)' }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => `$${(v as number).toFixed(2)}`}
            />
            <Tooltip
              content={<AgentTooltip />}
              cursor={false}
            />
            <Bar dataKey="cost" radius={[3, 3, 0, 0]}>
              {agentData.map((_, i) => (
                <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} fillOpacity={0.75} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      ) : (
        <>
          <div style={{ marginBottom: 6 }}><SkeletonRect height={80} /></div>
          <div style={{
            fontFamily:  'var(--fmo)',
            fontSize:    9,
            color:       'var(--tf)',
            textAlign:   'center',
            paddingTop:  4,
          }}>
            In attesa dei primi run…
          </div>
        </>
      )}

      {/* ── Breakdown costi area fill leggenda ── */}
      {hasAgentData && (
        <div style={{
          display:   'flex',
          flexWrap:  'wrap',
          gap:       '6px 14px',
          marginTop: 10,
        }}>
          {agentData.map((a, i) => (
            <div key={a.name} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{
                width:        7,
                height:       7,
                borderRadius: 2,
                background:   BAR_COLORS[i % BAR_COLORS.length],
                flexShrink:   0,
              }} />
              <span style={{ fontFamily: 'var(--fmo)', fontSize: 9, color: 'var(--tm)' }}>
                {a.name}
              </span>
              <span className="mono-num" style={{ fontSize: 9, color: ZONE_COLOR }}>
                ${a.cost.toFixed(4)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
