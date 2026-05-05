/**
 * AdsStatus — stato campagne Etsy Ads.
 *
 * FE-Blocco 4, Step 4.5
 *
 * Dati: GET /api/etsy/ads-status
 * Fetch: on mount, poi ogni 60s
 *
 * Layout:
 *   [ ADS MANAGER ]                   last run: HH:MM
 *     Attive:     12 listing
 *     In pausa:    3 listing  (CTR < threshold)
 *     CTR medio:  2.3%
 *
 * States:
 *   loading → skeleton 3 righe
 *   data    → stat rows
 *   error   → inline error
 */

import { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence }           from 'framer-motion'
import { useStore }                           from '../../store'

/* ── Types ────────────────────────────────────────────────────────────────── */

interface AdsStatusData {
  activated_count:    number
  paused_count:       number
  avg_ctr:            number | null
  last_auto_manage_at: number | null
}

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function fmtLastRun(ts: number | null): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffH  = diffMs / 3_600_000

  if (diffH < 1)   return `${Math.round(diffMs / 60_000)}m fa`
  if (diffH < 24)  {
    const hh = d.getHours().toString().padStart(2, '0')
    const mm = d.getMinutes().toString().padStart(2, '0')
    return `oggi ${hh}:${mm}`
  }
  const day = d.toLocaleDateString('it-IT', { weekday: 'long' })
  const hh  = d.getHours().toString().padStart(2, '0')
  const mm  = d.getMinutes().toString().padStart(2, '0')
  return `${day} ${hh}:${mm}`
}

function fmtCtr(ctr: number | null): string {
  if (ctr === null) return '—'
  return `${(ctr * 100).toFixed(1)}%`
}

/* ── Metric cell ─────────────────────────────────────────────────────────── */

interface MetricCellProps {
  label: string
  value: string
  color?: string
  delay:  number
}

function MetricCell({ label, value, color = 'rgba(255,255,255,0.80)', delay }: MetricCellProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 320, damping: 28, delay }}
      style={{
        background:   'rgba(255,255,255,0.03)',
        border:       '1px solid rgba(255,255,255,0.06)',
        borderRadius: 6,
        padding:      '10px 12px',
        display:      'flex',
        flexDirection:'column',
        gap:          5,
      }}
    >
      <span className="hud-label" style={{ fontSize: 8, letterSpacing: '0.12em' }}>
        {label}
      </span>
      <span className="mono-num" style={{ fontSize: 18, fontWeight: 500, color, lineHeight: 1 }}>
        {value}
      </span>
    </motion.div>
  )
}

/* ── AdsStatus ────────────────────────────────────────────────────────────── */

export function AdsStatus() {
  const [data,    setData]    = useState<AdsStatusData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const budgetMonthlyUsd = useStore(s => s.budgetMonthlyUsd)
  const dailySlice = (budgetMonthlyUsd ?? 0) > 0 ? `$${((budgetMonthlyUsd ?? 0) / 30).toFixed(2)}` : '—'

  const fetchStatus = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await fetch('/api/etsy/ads-status', { signal })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json() as AdsStatusData
      setData(json)
      setError(null)
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return
      setError('Connessione fallita. Riprova.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void fetchStatus(controller.signal)
    const id = setInterval(() => { void fetchStatus(controller.signal) }, 60_000)
    return () => { clearInterval(id); controller.abort() }
  }, [fetchStatus])

  /* CTR color: green if ≥2%, amber if ≥1%, muted if < 1% or null */
  const ctrColor = data?.avg_ctr == null
    ? 'rgba(255,255,255,0.28)'
    : data.avg_ctr >= 0.02
      ? '#1BFF5E'
      : data.avg_ctr >= 0.01
        ? '#F5A623'
        : '#FF6B6B'

  return (
    <div style={{
      background:     'rgba(13,15,18,0.72)',
      border:         '1px solid rgba(255,255,255,0.07)',
      borderRadius:   10,
      padding:        '16px 18px 14px',
      backdropFilter: 'blur(12px)',
      boxShadow:      'inset 0 1px 0 rgba(255,255,255,0.07)',
    }}>
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 14 }}>
        <div className="hud-label" style={{ flex: 1 }}>
          [ ADS MANAGER ]
        </div>

        {/* Last run timestamp */}
        {!loading && data && (
          <span className="mono-num" style={{
            fontSize: 9, color: 'rgba(255,255,255,0.28)', letterSpacing: '0.04em',
          }}>
            last run: {fmtLastRun(data.last_auto_manage_at)}
          </span>
        )}

        {/* Active count badge */}
        {!loading && data && data.activated_count > 0 && (
          <span className="mono-num" style={{
            fontSize:   10,
            color:      '#1BFF5E',
            background: 'rgba(27,255,94,0.10)',
            padding:    '2px 6px',
            borderRadius: 3,
            marginLeft:   8,
            letterSpacing: '0.04em',
          }}>
            {data.activated_count}
          </span>
        )}
      </div>

      {/* ── Error ───────────────────────────────────────────────────────── */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            style={{
              border:       '1px solid rgba(255,68,68,0.28)',
              borderRadius: 5,
              padding:      '6px 10px',
              marginBottom: 10,
              fontFamily:   'var(--fmo)',
              fontSize:     11,
              color:        '#FF6B6B',
              letterSpacing:'0.02em',
            }}
          >
            {error}
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Body ────────────────────────────────────────────────────────── */}
      {loading ? (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {[0, 0.06, 0.12, 0.18].map((d, i) => (
            <div key={i} style={{
              background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
              borderRadius: 6, padding: '10px 12px', opacity: 1 - d * 2,
            }}>
              <div style={{ height: 7, width: 48, borderRadius: 3, background: 'rgba(255,255,255,0.06)', marginBottom: 8 }} />
              <div style={{ height: 16, width: 52, borderRadius: 3, background: 'rgba(255,255,255,0.09)' }} />
            </div>
          ))}
        </div>

      ) : data ? (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          <MetricCell
            label="KEY METRICS"
            value={String(data.activated_count)}
            color={data.activated_count > 0 ? 'rgba(255,255,255,0.80)' : 'rgba(255,255,255,0.28)'}
            delay={0}
          />
          <MetricCell
            label="AVG CTR"
            value={fmtCtr(data.avg_ctr)}
            color={ctrColor}
            delay={0.06}
          />
          <MetricCell
            label="IN PAUSA"
            value={String(data.paused_count)}
            color={data.paused_count > 0 ? '#F5A623' : 'rgba(255,255,255,0.28)'}
            delay={0.12}
          />
          <MetricCell
            label="DAILY BUDGET"
            value={dailySlice}
            color="rgba(255,255,255,0.60)"
            delay={0.18}
          />
        </div>

      ) : null}
    </div>
  )
}
