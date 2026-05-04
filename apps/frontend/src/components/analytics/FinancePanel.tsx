/**
 * FinancePanel — FE-Blocco 5.2
 *
 * Dati: GET /api/finance/summary (polling 60s)
 *
 * Layout:
 *   - Summary header (full width): mese/anno, revenue, fee etsy, LLM cost, netto
 *   - Flat list niche (top 5): border-bottom, NO card per niche
 *
 * Valori: tutti .mono-num
 *   - Revenue / Netto positivi → #1BFF5E
 *   - Fee / costi negativi    → #FF4444
 *
 * States: skeleton → live → error (nessun dato inventato)
 */

import { useState, useEffect } from 'react'
// ── Tipi API ──────────────────────────────────────────────────────────────────

interface NicheRow {
  niche:          string
  sales_count:    number
  gross_eur:      number
  net_eur:        number
  total_fees_eur: number
}

interface FinanceSummary {
  year:              number
  month:             number
  n_sales:           number
  gross_eur:         number
  etsy_fees_eur:     number
  listing_fees_eur:  number
  design_costs_eur:  number
  net_eur:           number
  margin_pct:        number
  by_niche:          NicheRow[]
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const MONTHS_IT = [
  'Gennaio','Febbraio','Marzo','Aprile','Maggio','Giugno',
  'Luglio','Agosto','Settembre','Ottobre','Novembre','Dicembre',
]

function fmtEur(n: number, forceSign = false): string {
  const abs = Math.abs(n).toFixed(2)
  if (forceSign) return n >= 0 ? `+€${abs}` : `−€${abs}`
  return `€${abs}`
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function SkeletonRow({ width = '100%', height = 14 }: { width?: string; height?: number }) {
  return (
    <div style={{
      width,
      height,
      borderRadius: 4,
      background:   'rgba(148,163,184,0.08)',
      animation:    'skel-pulse 1.8s ease-in-out infinite',
    }} />
  )
}

function SkeletonPanel() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <SkeletonRow width="55%" height={11} />
      <SkeletonRow height={28} />
      <SkeletonRow height={14} />
      <SkeletonRow height={14} />
      <SkeletonRow height={14} />
      <SkeletonRow height={14} />
      <div style={{
        height:       1,
        background:   'rgba(255,255,255,0.06)',
        margin:       '6px 0',
      }} />
      {[1,2,3].map((i) => (
        <SkeletonRow key={i} height={12} width={`${90 - i * 10}%`} />
      ))}
    </div>
  )
}

// ── Row componente per metrica summary ───────────────────────────────────────

function MetricRow({
  label,
  value,
  positive,
  bold = false,
}: {
  label:    string
  value:    string
  positive: boolean
  bold?:    boolean
}) {
  return (
    <div style={{
      display:        'flex',
      justifyContent: 'space-between',
      alignItems:     'center',
      padding:        '5px 0',
      borderBottom:   '1px solid rgba(255,255,255,0.05)',
    }}>
      <span style={{
        fontFamily: 'var(--fui)',
        fontSize:   11,
        color:      bold ? 'var(--tp)' : 'var(--tm)',
        fontWeight: bold ? 600 : 400,
      }}>
        {label}
      </span>
      <span
        className="mono-num"
        style={{
          fontSize:   bold ? 13 : 12,
          fontWeight: bold ? 600 : 400,
          color:      positive ? '#1BFF5E' : '#FF4444',
        }}
      >
        {value}
      </span>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function FinancePanel() {
  const [data,    setData]    = useState<FinanceSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const fetchData = (signal?: AbortSignal) => {
    fetch('/api/finance/summary', { signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((d: FinanceSummary) => {
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

  const totalFees = data
    ? data.etsy_fees_eur + data.listing_fees_eur + data.design_costs_eur
    : 0

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
        marginBottom:   14,
      }}>
        <span style={{
          fontFamily:    'var(--fui)',
          fontSize:      13,
          fontWeight:    600,
          color:         'var(--tp)',
        }}>
          Finance
        </span>
        {data && (
          <span style={{
            fontFamily: 'var(--fmo)',
            fontSize:   10,
            color:      'var(--tf)',
          }}>
            {MONTHS_IT[data.month - 1]} {data.year}
          </span>
        )}
      </div>

      {/* ── Content ── */}
      {loading ? (
        <SkeletonPanel />
      ) : error ? (
        <div style={{
          fontFamily:  'var(--fmo)',
          fontSize:    10,
          color:       '#FF4444',
          padding:     '12px 0',
          textAlign:   'center',
        }}>
          Errore: {error}
        </div>
      ) : data ? (
        <>
          {/* ── Summary totale ── */}
          <div style={{ marginBottom: 6 }}>
            <MetricRow
              label="Revenue"
              value={fmtEur(data.gross_eur)}
              positive={true}
            />
            <MetricRow
              label="Fee Etsy"
              value={`−${fmtEur(totalFees)}`}
              positive={false}
            />
            {data.design_costs_eur > 0 && (
              <MetricRow
                label="LLM cost"
                value={`−${fmtEur(data.design_costs_eur)}`}
                positive={false}
              />
            )}
            <MetricRow
              label="Netto"
              value={fmtEur(data.net_eur)}
              positive={data.net_eur >= 0}
              bold
            />
          </div>

          {/* ── Margin pct ── */}
          {data.margin_pct != null && (
            <div style={{
              display:       'flex',
              gap:           6,
              marginBottom:  12,
              paddingBottom: 12,
              borderBottom:  '1px solid rgba(255,255,255,0.06)',
            }}>
              <span style={{ fontFamily: 'var(--fmo)', fontSize: 9, color: 'var(--tf)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                Margine
              </span>
              <span className="mono-num" style={{
                fontSize: 9,
                color:    data.margin_pct >= 0 ? '#1BFF5E' : '#FF4444',
              }}>
                {data.margin_pct.toFixed(1)}%
              </span>
              <span style={{ fontFamily: 'var(--fmo)', fontSize: 9, color: 'var(--tf)' }}>
                · {data.n_sales} vendite
              </span>
            </div>
          )}

          {/* ── Niche breakdown ── */}
          {data.by_niche.length > 0 && (
            <>
              <div style={{
                fontFamily:    'var(--fmo)',
                fontSize:      9,
                fontWeight:    700,
                letterSpacing: '0.1em',
                textTransform: 'uppercase',
                color:         'var(--tf)',
                marginBottom:  6,
              }}>
                Per niche (top {Math.min(data.by_niche.length, 5)})
              </div>
              <div>
                {data.by_niche.slice(0, 5).map((row) => (
                  <div
                    key={row.niche}
                    style={{
                      display:        'flex',
                      alignItems:     'center',
                      justifyContent: 'space-between',
                      padding:        '6px 0',
                      borderBottom:   '1px solid rgba(255,255,255,0.04)',
                      gap:            8,
                    }}
                  >
                    <span style={{
                      fontFamily:  'var(--fui)',
                      fontSize:    10,
                      color:       'var(--tm)',
                      flex:        1,
                      minWidth:    0,
                      overflow:    'hidden',
                      textOverflow:'ellipsis',
                      whiteSpace:  'nowrap',
                    }}>
                      {row.niche}
                    </span>
                    <span className="mono-num" style={{
                      fontSize:   10,
                      color:      '#1BFF5E',
                      flexShrink: 0,
                    }}>
                      {fmtEur(row.gross_eur)}
                    </span>
                    <span className="mono-num" style={{
                      fontSize:   9,
                      color:      'var(--tf)',
                      flexShrink: 0,
                    }}>
                      {row.sales_count}v
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}

          {data.by_niche.length === 0 && (
            <div style={{
              fontFamily: 'var(--fmo)',
              fontSize:   10,
              color:      'var(--tf)',
              padding:    '8px 0',
            }}>
              Nessuna vendita per niche nel periodo
            </div>
          )}
        </>
      ) : null}
    </div>
  )
}
