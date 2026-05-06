/**
 * PinterestPanel — stato canale Pinterest in tempo reale.
 *
 * B-11 / Blocco B
 *
 * Dati: GET /api/pinterest/status (polling 60s)
 * WS:   pin_published / pin_failed events (real-time overlay)
 *
 * Layout (colonna destra EtsyView, sotto AdsStatus):
 *   [ PINTEREST ]                  last run: HH:MM
 *     ● connected · Standard Access
 *     Pin oggi: 8 · In coda: 15 · Falliti: 1
 *     ─────────────────────────────────
 *     Board 1 — 42 pin
 *     Board 2 — 18 pin
 *     ─────────────────────────────────
 *     Prossimo pin: tra 2 ore
 *     Costo oggi:   €0.0340
 *
 * States: loading → skeleton · data → panel · error → inline
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { motion, AnimatePresence }                   from 'framer-motion'
import {
  fmtNextPin,
  fmtCostEur,
  accessModeLabel,
  connectionDotColor,
}                                                    from './PinterestPanel.helpers'

/* ── Types ────────────────────────────────────────────────────────────────── */

interface BoardInfo {
  section_key:  string
  board_name:   string
  pin_count:    number
}

interface PinterestStatusData {
  connected:             boolean
  access_mode:           string
  token_expires_in_days: number | null
  pins_today:            number
  pins_queued:           number
  pins_failed:           number
  boards:                BoardInfo[]
  next_scheduled_at:     string | null
  cost_today_eur:        number | null
}

/* ── Skeleton row ─────────────────────────────────────────────────────────── */

function SkeletonRow({ width = 80, delay = 0 }: { width?: number; delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0.4 }}
      animate={{ opacity: [0.4, 0.7, 0.4] }}
      transition={{ duration: 1.4, repeat: Infinity, delay }}
      style={{
        height: 10, width, borderRadius: 3,
        background: 'rgba(255,255,255,0.07)',
        marginBottom: 8,
      }}
    />
  )
}

/* ── Stat chip ────────────────────────────────────────────────────────────── */

interface StatChipProps {
  label: string
  value: string | number
  color?: string
  delay:  number
}

function StatChip({ label, value, color = 'rgba(255,255,255,0.80)', delay }: StatChipProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 320, damping: 28, delay }}
      style={{
        background:    'rgba(255,255,255,0.03)',
        border:        '1px solid rgba(255,255,255,0.06)',
        borderRadius:  6,
        padding:       '8px 10px',
        display:       'flex',
        flexDirection: 'column',
        gap:           4,
        minWidth:      0,
      }}
    >
      <span className="hud-label" style={{ fontSize: 8, letterSpacing: '0.12em' }}>
        {label}
      </span>
      <span className="mono-num" style={{ fontSize: 16, fontWeight: 500, color, lineHeight: 1 }}>
        {value}
      </span>
    </motion.div>
  )
}

/* ── PinterestPanel ───────────────────────────────────────────────────────── */

export function PinterestPanel() {
  const [data,       setData]       = useState<PinterestStatusData | null>(null)
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState<string | null>(null)
  const [lastRun,    setLastRun]    = useState<Date | null>(null)
  const [flashFail,  setFlashFail]  = useState(false)   // WS pin_failed flash
  const wsRef = useRef<WebSocket | null>(null)

  /* ── REST polling ──────────────────────────────────────────────────────── */
  const fetchStatus = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await fetch('/api/pinterest/status', { signal })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json() as PinterestStatusData
      setData(json)
      setError(null)
      setLastRun(new Date())
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return
      setError('Connessione fallita')
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

  /* ── WebSocket real-time overlay ───────────────────────────────────────── */
  useEffect(() => {
    let ws: WebSocket | null = null
    try {
      ws = new WebSocket('/ws/events')
      wsRef.current = ws
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string) as { type: string }
          if (msg.type === 'pin_published') {
            setData(prev => prev ? {
              ...prev,
              pins_today:  prev.pins_today + 1,
              pins_queued: Math.max(0, prev.pins_queued - 1),
            } : prev)
          } else if (msg.type === 'pin_failed') {
            setData(prev => prev ? { ...prev, pins_failed: prev.pins_failed + 1 } : prev)
            setFlashFail(true)
            setTimeout(() => setFlashFail(false), 3_000)
          }
        } catch { /* ignore parse errors */ }
      }
    } catch { /* /ws/events not available yet — polling only */ }
    return () => { ws?.close(); wsRef.current = null }
  }, [])

  /* ── Helpers ───────────────────────────────────────────────────────────── */
  const fmtTime = (d: Date | null) => {
    if (!d) return '—'
    return `${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}`
  }

  const dotColor = data
    ? connectionDotColor(data.connected, data.token_expires_in_days)
    : 'rgba(255,255,255,0.20)'

  const failedColor = flashFail ? '#F5A623'
    : (data?.pins_failed ?? 0) > 0 ? '#FF6B6B'
    : 'rgba(255,255,255,0.28)'

  /* ── Render ────────────────────────────────────────────────────────────── */
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
          [ PINTEREST ]
        </div>
        {!loading && lastRun && (
          <span className="mono-num" style={{
            fontSize: 9, color: 'rgba(255,255,255,0.28)', letterSpacing: '0.04em',
          }}>
            last run: {fmtTime(lastRun)}
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

      {/* ── Skeleton ────────────────────────────────────────────────────── */}
      {loading && (
        <div>
          <SkeletonRow width={160} delay={0} />
          <SkeletonRow width={220} delay={0.08} />
          <SkeletonRow width={130} delay={0.16} />
          <SkeletonRow width={180} delay={0.24} />
        </div>
      )}

      {/* ── Data ────────────────────────────────────────────────────────── */}
      {!loading && data && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3 }}
        >
          {/* Connection row */}
          <motion.div
            initial={{ opacity: 0, x: -4 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ type: 'spring', stiffness: 320, damping: 28 }}
            style={{
              display:     'flex',
              alignItems:  'center',
              gap:         8,
              marginBottom: 12,
              padding:     '8px 10px',
              background:  'rgba(255,255,255,0.03)',
              border:      '1px solid rgba(255,255,255,0.06)',
              borderRadius: 6,
            }}
          >
            {/* Status dot */}
            <motion.div
              animate={{ opacity: [1, 0.5, 1] }}
              transition={{ duration: 2, repeat: data.connected ? Infinity : 0 }}
              style={{
                width: 8, height: 8, borderRadius: '50%',
                background: dotColor,
                flexShrink: 0,
                boxShadow:  `0 0 6px ${dotColor}88`,
              }}
            />
            <span className="hud-label" style={{ flex: 1, fontSize: 10 }}>
              Pinterest
            </span>
            <span className="mono-num" style={{
              fontSize: 9,
              color:    dotColor,
              letterSpacing: '0.06em',
            }}>
              {accessModeLabel(data.access_mode)}
            </span>
            {data.token_expires_in_days !== null && data.token_expires_in_days <= 7 && (
              <span className="mono-num" style={{
                fontSize: 8,
                color:    '#F5A623',
                marginLeft: 4,
              }}>
                {data.token_expires_in_days}d
              </span>
            )}
          </motion.div>

          {/* Metric chips — 3 columns */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, marginBottom: 12 }}>
            <StatChip
              label="PIN OGGI"
              value={data.pins_today}
              color={data.pins_today > 0 ? '#1BFF5E' : 'rgba(255,255,255,0.28)'}
              delay={0}
            />
            <StatChip
              label="IN CODA"
              value={data.pins_queued}
              color="rgba(255,255,255,0.80)"
              delay={0.05}
            />
            <StatChip
              label="FALLITI"
              value={data.pins_failed}
              color={failedColor}
              delay={0.10}
            />
          </div>

          {/* Board summary */}
          {data.boards.length > 0 && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.15 }}
              style={{ marginBottom: 12 }}
            >
              <div className="hud-label" style={{ fontSize: 8, marginBottom: 6, letterSpacing: '0.12em' }}>
                BOARD ATTIVI
              </div>
              {data.boards.map((b, i) => (
                <motion.div
                  key={b.section_key}
                  initial={{ opacity: 0, x: -4 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.18 + i * 0.04 }}
                  style={{
                    display:        'flex',
                    justifyContent: 'space-between',
                    alignItems:     'center',
                    padding:        '5px 0',
                    borderBottom:   i < data.boards.length - 1
                      ? '1px solid rgba(255,255,255,0.04)'
                      : 'none',
                  }}
                >
                  <span style={{
                    fontFamily:    'var(--fmo)',
                    fontSize:      10,
                    color:         'rgba(255,255,255,0.60)',
                    letterSpacing: '0.02em',
                    overflow:      'hidden',
                    textOverflow:  'ellipsis',
                    whiteSpace:    'nowrap',
                    maxWidth:      '70%',
                  }}>
                    {b.board_name}
                  </span>
                  <span className="mono-num" style={{ fontSize: 10, color: 'rgba(255,255,255,0.40)' }}>
                    {b.pin_count}
                  </span>
                </motion.div>
              ))}
            </motion.div>
          )}

          {/* Footer: next pin + cost */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.35 }}
            style={{
              display:       'grid',
              gridTemplateColumns: '1fr 1fr',
              gap:           6,
            }}
          >
            <div style={{
              background:   'rgba(255,255,255,0.02)',
              border:       '1px solid rgba(255,255,255,0.05)',
              borderRadius:  5,
              padding:      '7px 9px',
            }}>
              <div className="hud-label" style={{ fontSize: 7, marginBottom: 4 }}>PROSSIMO PIN</div>
              <span className="mono-num" style={{ fontSize: 10, color: 'rgba(255,255,255,0.55)' }}>
                {fmtNextPin(data.next_scheduled_at)}
              </span>
            </div>
            <div style={{
              background:   'rgba(255,255,255,0.02)',
              border:       '1px solid rgba(255,255,255,0.05)',
              borderRadius:  5,
              padding:      '7px 9px',
            }}>
              <div className="hud-label" style={{ fontSize: 7, marginBottom: 4 }}>COSTO OGGI</div>
              <span className="mono-num" style={{ fontSize: 10, color: '#00CED1' }}>
                {fmtCostEur(data.cost_today_eur)}
              </span>
            </div>
          </motion.div>
        </motion.div>
      )}
    </div>
  )
}
