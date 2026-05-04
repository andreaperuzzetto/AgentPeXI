/**
 * CtrAbPanel — FE-Blocco 5.3
 *
 * Dati: GET /api/analytics/ctr-ab (polling 60s)
 *
 * Tabella confronti A/B:
 *   Niche | Winner template | CTR winner | CTR loser | Δ
 *
 * Colonna Δ: prefisso + verde (#1BFF5E) o − rosso (#FF4444), sempre .mono-num
 *
 * States: skeleton → live → error → empty
 */

import { useState, useEffect } from 'react'
// ── Tipi API ──────────────────────────────────────────────────────────────────

interface AbWinner {
  template:     string
  color_scheme: string
  ctr:          number
}

interface AbResult {
  niche:        string
  product_type: string | null
  winner:       AbWinner
  loser:        AbWinner
  compared_at:  string | null
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function SkeletonRow() {
  return (
    <tr>
      {[80, 110, 52, 52, 44].map((w, i) => (
        <td key={i} style={{ padding: '6px 8px' }}>
          <div style={{
            width:        w,
            height:       10,
            borderRadius: 3,
            background:   'rgba(148,163,184,0.08)',
            animation:    'skel-pulse 1.8s ease-in-out infinite',
            animationDelay: `${i * 80}ms`,
          }} />
        </td>
      ))}
    </tr>
  )
}

// ── Delta cell ────────────────────────────────────────────────────────────────

function DeltaCell({ winner, loser }: { winner: number; loser: number }) {
  const delta = winner - loser
  const positive = delta >= 0
  const sign     = positive ? '+' : '−'
  const color    = positive ? '#1BFF5E' : '#FF4444'
  return (
    <td style={{ padding: '7px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>
      <span className="mono-num" style={{ fontSize: 11, color }}>
        {sign}{Math.abs(delta * 100).toFixed(1)}pp
      </span>
    </td>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function CtrAbPanel() {
  const [results, setResults] = useState<AbResult[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const fetchData = () => {
    fetch('/api/analytics/ctr-ab?limit=20')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((d: { results: AbResult[] }) => {
        setResults(d.results)
        setLoading(false)
        setError(null)
      })
      .catch((e: Error) => {
        setError(e.message)
        setLoading(false)
      })
  }

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, 60_000)
    return () => clearInterval(id)
  }, [])

  const TH_STYLE: React.CSSProperties = {
    fontFamily:    'var(--fmo)',
    fontSize:      9,
    fontWeight:    700,
    letterSpacing: '0.1em',
    textTransform: 'uppercase' as const,
    color:         'var(--tf)',
    padding:       '0 8px 8px',
    textAlign:     'left' as const,
    whiteSpace:    'nowrap' as const,
    borderBottom:  '1px solid rgba(255,255,255,0.07)',
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
          fontFamily:    'var(--fui)',
          fontSize:      13,
          fontWeight:    600,
          color:         'var(--tp)',
        }}>
          CTR A/B
        </span>
        <span style={{
          fontFamily: 'var(--fmo)',
          fontSize:   10,
          color:      'var(--tf)',
        }}>
          Confronti thumbnail
        </span>
      </div>

      {/* ── Content ── */}
      {error ? (
        <div style={{
          fontFamily: 'var(--fmo)',
          fontSize:   10,
          color:      '#FF4444',
          padding:    '12px 0',
          textAlign:  'center',
        }}>
          Errore: {error}
        </div>
      ) : (
        <div style={{ overflowX: 'auto', marginRight: -4 }}>
          <table style={{
            width:           '100%',
            borderCollapse:  'collapse',
            minWidth:        420,
          }}>
            <thead>
              <tr>
                <th style={TH_STYLE}>Niche</th>
                <th style={TH_STYLE}>Winner template</th>
                <th style={{ ...TH_STYLE, textAlign: 'right' }}>CTR ↑</th>
                <th style={{ ...TH_STYLE, textAlign: 'right' }}>CTR ↓</th>
                <th style={{ ...TH_STYLE, textAlign: 'right' }}>Δ</th>
              </tr>
            </thead>
            <tbody>
              {loading
                ? [0,1,2,3].map((i) => <SkeletonRow key={i} />)
                : results && results.length > 0
                  ? results.map((row, i) => (
                    <tr
                      key={i}
                      style={{
                        borderBottom:  '1px solid rgba(255,255,255,0.04)',
                        transition:    'background 0.15s',
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(148,163,184,0.04)')}
                      onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                    >
                      <td style={{
                        padding:    '7px 8px',
                        fontFamily: 'var(--fui)',
                        fontSize:   11,
                        color:      'var(--tm)',
                        maxWidth:   130,
                        overflow:   'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}>
                        {row.niche}
                      </td>
                      <td style={{
                        padding:    '7px 8px',
                        fontFamily: 'var(--fmo)',
                        fontSize:   10,
                        color:      'var(--tp)',
                        maxWidth:   150,
                        overflow:   'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}>
                        {row.winner.template || '—'}
                      </td>
                      <td style={{ padding: '7px 8px', textAlign: 'right' }}>
                        <span className="mono-num" style={{ fontSize: 11, color: '#1BFF5E' }}>
                          {row.winner.ctr > 0 ? `${(row.winner.ctr * 100).toFixed(1)}%` : '—'}
                        </span>
                      </td>
                      <td style={{ padding: '7px 8px', textAlign: 'right' }}>
                        <span className="mono-num" style={{ fontSize: 11, color: 'var(--tm)' }}>
                          {row.loser.ctr > 0 ? `${(row.loser.ctr * 100).toFixed(1)}%` : '—'}
                        </span>
                      </td>
                      <DeltaCell winner={row.winner.ctr} loser={row.loser.ctr} />
                    </tr>
                  ))
                  : (
                    <tr>
                      <td colSpan={5} style={{
                        padding:    '16px 8px',
                        fontFamily: 'var(--fmo)',
                        fontSize:   10,
                        color:      'var(--tf)',
                        textAlign:  'center',
                      }}>
                        Nessun test A/B disponibile
                      </td>
                    </tr>
                  )
              }
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
