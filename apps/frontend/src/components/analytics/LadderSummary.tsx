/**
 * LadderSummary — FE-Blocco 5.4
 *
 * Dati: GET /api/analytics/ladder (polling 60s)
 *
 * Recharts PieChart donut:
 *   ok         → #1BFF5E
 *   views_low  → #F5A623
 *   ctr_low    → #B57BFF
 *   conv_low   → #FF4444
 *
 * Centro donut: totale listing (font-size 1.5rem, weight 600)
 * Label esterne: .mono-num text-xs
 * Sotto chart: conteggi assoluti + data ultimo aggiornamento
 *
 * States: skeleton (cerchio) → live → error (nessun dato inventato)
 */

import { useState, useEffect } from 'react'
import { PieChart, Pie, Cell, Tooltip } from 'recharts'

// ── Tipi API ──────────────────────────────────────────────────────────────────

interface LadderData {
  ok:          number
  views_low:   number
  ctr_low:     number
  conv_low:    number
  undiagnosed: number
  total:       number
  last_updated: number | null
}

// ── Palette ladder ────────────────────────────────────────────────────────────

const LADDER_ITEMS = [
  { key: 'ok',         label: 'OK',         color: '#1BFF5E' },
  { key: 'views_low',  label: 'Views low',  color: '#F5A623' },
  { key: 'ctr_low',    label: 'CTR low',    color: '#B57BFF' },
  { key: 'conv_low',   label: 'Conv low',   color: '#FF4444' },
] as const

// ── Custom tooltip glass ──────────────────────────────────────────────────────

function GlassTooltip({ active, payload }: {
  active?:  boolean
  payload?: Array<{ name: string; value: number; payload: { color: string } }>
}) {
  if (!active || !payload?.length) return null
  const { name, value, payload: p } = payload[0]
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
      <span style={{ color: p.color }}>{name}</span>
      <span style={{ color: 'var(--tm)', marginLeft: 8 }}>{value}</span>
    </div>
  )
}

// ── Custom label esterno ──────────────────────────────────────────────────────

function CustomLabel({
  cx, cy, midAngle, outerRadius, name, value, percent,
}: {
  cx?: number; cy?: number; midAngle?: number; outerRadius?: number
  name?: string; value?: number; percent?: number
}) {
  if (!cx || !cy || !midAngle || !outerRadius) return null
  if (!percent || percent < 0.04) return null  // nascondi label troppo piccoli
  const RADIAN = Math.PI / 180
  const r  = outerRadius + 18
  const x  = cx + r * Math.cos(-midAngle * RADIAN)
  const y  = cy + r * Math.sin(-midAngle * RADIAN)
  return (
    <text
      x={x}
      y={y}
      textAnchor={x > cx ? 'start' : 'end'}
      dominantBaseline="central"
      style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: 10 }}
      fill="rgba(255,255,255,0.55)"
    >
      {name} {value}
    </text>
  )
}

// ── Skeleton cerchio ──────────────────────────────────────────────────────────

function SkeletonDonut() {
  return (
    <div style={{
      display:        'flex',
      flexDirection:  'column',
      alignItems:     'center',
      gap:            12,
    }}>
      <div style={{
        width:        160,
        height:       160,
        borderRadius: '50%',
        background:   'rgba(148,163,184,0.08)',
        animation:    'skel-pulse 1.8s ease-in-out infinite',
      }} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, width: '100%' }}>
        {[1,2,3,4].map((i) => (
          <div key={i} style={{
            height:       11,
            borderRadius: 3,
            background:   'rgba(148,163,184,0.07)',
            animation:    `skel-pulse 1.8s ease-in-out infinite`,
            animationDelay: `${i * 100}ms`,
            width:        `${85 - i * 6}%`,
          }} />
        ))}
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function LadderSummary() {
  const [data,    setData]    = useState<LadderData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const fetchData = (signal?: AbortSignal) => {
    fetch('/api/analytics/ladder', { signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((d: LadderData) => {
        setData(d)
        setLoading(false)
        setError(null)
      })
      .catch((e: Error) => {
        if ((e as DOMException).name === 'AbortError') return
        setError(e.message)
        setLoading(false)
      })
  }

  useEffect(() => {
    const controller = new AbortController()
    fetchData(controller.signal)
    const id = setInterval(() => fetchData(controller.signal), 60_000)
    return () => { clearInterval(id); controller.abort() }
  }, [])

  const pieData = data
    ? LADDER_ITEMS
        .map((item) => ({
          name:  item.label,
          value: data[item.key],
          color: item.color,
        }))
        .filter((d) => d.value > 0)
    : []

  const fmtLastUpdated = (ts: number | null): string => {
    if (!ts) return '—'
    const d = new Date(ts * 1000)
    const MONTHS = ['gen','feb','mar','apr','mag','giu','lug','ago','set','ott','nov','dic']
    return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()} ${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}`
  }

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
        display:      'flex',
        alignItems:   'baseline',
        gap:          10,
        marginBottom: 14,
      }}>
        <span style={{
          fontFamily: 'var(--fui)',
          fontSize:   13,
          fontWeight: 600,
          color:      'var(--tp)',
        }}>
          Ladder
        </span>
        <span style={{
          fontFamily: 'var(--fmo)',
          fontSize:   10,
          color:      'var(--tf)',
        }}>
          Diagnosi listing
        </span>
      </div>

      {/* ── Content ── */}
      {loading ? (
        <SkeletonDonut />
      ) : error ? (
        <div style={{
          fontFamily: 'var(--fmo)',
          fontSize:   10,
          color:      '#FF4444',
          padding:    '12px 0',
          textAlign:  'center',
        }}>
          Errore: {error}
        </div>
      ) : data ? (
        <>
          {/* ── Donut chart ── */}
          {pieData.length > 0 ? (
            <div style={{ display: 'flex', justifyContent: 'center', position: 'relative' }}>
              <PieChart width={220} height={200}>
                <Pie
                  data={pieData}
                  cx={110}
                  cy={96}
                  innerRadius={52}
                  outerRadius={78}
                  paddingAngle={2}
                  dataKey="value"
                  labelLine={false}
                  label={CustomLabel}
                  strokeWidth={0}
                >
                  {pieData.map((entry, i) => (
                    <Cell key={i} fill={entry.color} fillOpacity={0.85} />
                  ))}
                </Pie>
                <Tooltip
                  content={<GlassTooltip />}
                  cursor={false}
                />
              </PieChart>

              {/* Centro donut: totale */}
              <div style={{
                position:   'absolute',
                top:        '50%',
                left:       '50%',
                transform:  'translate(-50%, -50%)',
                textAlign:  'center',
                pointerEvents: 'none',
              }}>
                <div className="mono-num" style={{
                  fontSize:   '1.5rem',
                  fontWeight: 600,
                  color:      'var(--tp)',
                  lineHeight: 1,
                }}>
                  {data.total}
                </div>
                <div style={{
                  fontFamily:    'var(--fmo)',
                  fontSize:      8,
                  color:         'var(--tf)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  marginTop:     3,
                }}>
                  listing
                </div>
              </div>
            </div>
          ) : (
            <div style={{
              display:        'flex',
              justifyContent: 'center',
              alignItems:     'center',
              height:         160,
              fontFamily:     'var(--fmo)',
              fontSize:       10,
              color:          'var(--tf)',
            }}>
              Nessun dato ladder disponibile
            </div>
          )}

          {/* ── Conteggi assoluti ── */}
          <div style={{ marginTop: 8 }}>
            {LADDER_ITEMS.map((item) => {
              const count = data[item.key]
              if (count === 0) return null
              return (
                <div
                  key={item.key}
                  style={{
                    display:        'flex',
                    justifyContent: 'space-between',
                    alignItems:     'center',
                    padding:        '4px 0',
                    borderBottom:   '1px solid rgba(255,255,255,0.04)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <span style={{
                      width:        6,
                      height:       6,
                      borderRadius: '50%',
                      background:   item.color,
                      flexShrink:   0,
                    }} />
                    <span style={{
                      fontFamily:  'var(--fui)',
                      fontSize:    10,
                      color:       'var(--tm)',
                    }}>
                      {item.label}
                    </span>
                  </div>
                  <span className="mono-num" style={{ fontSize: 10, color: item.color }}>
                    {count}
                  </span>
                </div>
              )
            })}
            {data.undiagnosed > 0 && (
              <div style={{
                display:        'flex',
                justifyContent: 'space-between',
                padding:        '4px 0',
              }}>
                <span style={{ fontFamily: 'var(--fui)', fontSize: 10, color: 'var(--tf)' }}>
                  Undiagnosed
                </span>
                <span className="mono-num" style={{ fontSize: 10, color: 'var(--tf)' }}>
                  {data.undiagnosed}
                </span>
              </div>
            )}
          </div>

          {/* ── Last updated ── */}
          {data.last_updated && (
            <div style={{
              marginTop:     8,
              fontFamily:    'var(--fmo)',
              fontSize:      9,
              color:         'var(--tf)',
              textAlign:     'right',
            }}>
              Aggiornato {fmtLastUpdated(data.last_updated)}
            </div>
          )}
        </>
      ) : null}
    </div>
  )
}
