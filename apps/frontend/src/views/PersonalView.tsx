/**
 * PersonalView — FE-Blocco 6 (Redesign)
 *
 * Sezioni:
 *   1. InfraRow      — MCP status (Notion/Gmail/Calendar) + ChromaDB counts
 *   2. Reminders     — Prossimi reminder pendenti con countdown
 *   3. Recall feed   — Ultimi recall/research completati
 *   4. ScreenWatcher — status, toggle, captures oggi
 *   5. Quick Ask     — textarea + POST /api/personal/ask + risposta inline
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { motion } from 'framer-motion'
import { useStore } from '../store'
import { GlassCard } from '../components/ui/GlassCard'

// ── Constants ──────────────────────────────────────────────────────────────────

const SPRING_ENTRY = (delay: number) => ({
  initial:    { opacity: 0, y: 10 } as const,
  animate:    { opacity: 1, y:  0 } as const,
  transition: { type: 'spring' as const, stiffness: 280, damping: 30, delay },
})

// ── Helpers ────────────────────────────────────────────────────────────────────

function formatRelativeTime(isoDate: string): string {
  if (!isoDate) return '—'
  const now    = new Date()
  const target = new Date(isoDate)
  const diffMs = target.getTime() - now.getTime()

  if (diffMs < 0) {
    const abs  = Math.abs(diffMs)
    const mins = Math.floor(abs / 60_000)
    if (mins < 60)  return `${mins}m fa`
    const hrs = Math.floor(mins / 60)
    if (hrs  < 24)  return `${hrs}h fa`
    return `${Math.floor(hrs / 24)}g fa`
  }

  const mins = Math.floor(diffMs / 60_000)
  if (mins < 1)  return 'Adesso'
  if (mins < 60) return `tra ${mins}m`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24)  return `tra ${hrs}h`
  const days = Math.floor(hrs / 24)
  if (days === 1) {
    const t = target.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })
    return `domani ${t}`
  }
  return `tra ${days}g`
}

function reminderCountdownColor(isoDate: string): string {
  if (!isoDate) return 'var(--tf)'
  const diffMs = new Date(isoDate).getTime() - Date.now()
  if (diffMs < 0)               return 'rgba(255,68,68,0.85)'
  if (diffMs < 60 * 60_000)     return 'rgba(255,186,0,0.85)'
  if (diffMs < 3 * 3600 * 1000) return 'var(--acc)'
  return 'var(--tf)'
}

function mcpDotColor(status: string): string {
  if (status === 'ok' || status === 'configured') return 'rgba(27,255,94,0.85)'
  if (status === 'not_configured')                return 'rgba(255,255,255,0.18)'
  return 'rgba(255,68,68,0.85)'
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + '…' : s
}

function todayStart(): Date {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return d
}

// ── InfraRow ───────────────────────────────────────────────────────────────────

interface McpStatus { notion: string; gmail: string; calendar: string }
interface MemStats  { available: boolean; by_collection: Record<string, number> }

const MCP_LABELS: Record<keyof McpStatus, string> = {
  notion:   'Notion',
  gmail:    'Gmail',
  calendar: 'Calendar',
}

function InfraRow() {
  const [mcp, setMcp] = useState<McpStatus | null>(null)
  const [mem, setMem] = useState<MemStats  | null>(null)

  const fetchAll = useCallback(() => {
    fetch('/api/personal/mcp/status')
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setMcp(d) })
      .catch(() => {})
    fetch('/api/memory/stats')
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d?.chroma) setMem(d.chroma) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchAll, 120_000)
    return () => clearInterval(id)
  }, [fetchAll])

  const personalCount = mem?.by_collection?.['personal_memory'] ?? '—'
  const screenCount   = mem?.by_collection?.['screen_memory']   ?? '—'

  return (
    <GlassCard hudVariant innerStyle={{ padding: '8px 12px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap' }}>
        <span style={{
          fontFamily:    'var(--fmo)',
          fontSize:      10,
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          color:         'rgba(255,255,255,0.38)',
          marginRight:   4,
        }}>
          [ INFRA ]
        </span>

        {/* MCP dots */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          {(Object.keys(MCP_LABELS) as Array<keyof McpStatus>).map((key) => (
            <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <div style={{
                width:        6,
                height:       6,
                borderRadius: '50%',
                background:   mcpDotColor(mcp?.[key] ?? 'not_configured'),
                flexShrink:   0,
              }} />
              <span style={{
                fontFamily:    'var(--fmo)',
                fontSize:      10,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                color:         'var(--tf)',
              }}>
                {MCP_LABELS[key]}
              </span>
            </div>
          ))}
        </div>

        <div style={{ width: 1, height: 14, background: 'rgba(255,255,255,0.08)', flexShrink: 0 }} />

        {/* Memory counts */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <span style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tf)' }}>
            personal_mem{' '}
            <span className="mono-num" style={{ color: 'var(--tm)' }}>{personalCount}</span>
          </span>
          <span style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tf)' }}>
            screen_mem{' '}
            <span className="mono-num" style={{ color: 'var(--tm)' }}>{screenCount}</span>
          </span>
        </div>
      </div>
    </GlassCard>
  )
}

// ── Reminders ──────────────────────────────────────────────────────────────────

interface Reminder { id: string | number; message: string; when: string; status: string }

function RemindersSection() {
  const [items, setItems] = useState<Reminder[]>([])

  const fetchReminders = useCallback(() => {
    fetch('/api/personal/reminders?limit=10')
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d?.items) setItems(d.items) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetchReminders()
    const id = setInterval(fetchReminders, 60_000)
    return () => clearInterval(id)
  }, [fetchReminders])

  return (
    <GlassCard hudVariant innerStyle={{ padding: 12 }}>
      <span style={{
        fontFamily:    'var(--fmo)',
        fontSize:      10,
        letterSpacing: '0.18em',
        textTransform: 'uppercase',
        color:         'rgba(255,255,255,0.38)',
        display:       'block',
        marginBottom:  10,
      }}>
        [ REMINDERS ]
      </span>

      {items.length === 0 ? (
        <div style={{ fontFamily: 'var(--fmo)', fontSize: 11, color: 'var(--tf)' }}>
          Nessun reminder pendente
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {items.map((item) => (
            <div key={item.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
              <span className="mono-num" style={{
                minWidth:  60,
                fontSize:  10,
                color:     reminderCountdownColor(item.when),
                flexShrink: 0,
                paddingTop: 1,
              }}>
                {formatRelativeTime(item.when)}
              </span>
              <span style={{
                fontFamily: 'var(--fmo)',
                fontSize:   12,
                color:      'var(--tm)',
                flex:       1,
                lineHeight: 1.45,
              }}>
                {truncate(item.message, 90)}
              </span>
            </div>
          ))}
        </div>
      )}
    </GlassCard>
  )
}

// ── Recall Feed ────────────────────────────────────────────────────────────────

interface RecallItem { timestamp: string; agent: string; query: string; status: string }

function RecallFeed() {
  const [items, setItems] = useState<RecallItem[]>([])

  const fetchRecalls = useCallback(() => {
    fetch('/api/personal/recalls?limit=10')
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d?.items) setItems(d.items) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetchRecalls()
    const id = setInterval(fetchRecalls, 30_000)
    return () => clearInterval(id)
  }, [fetchRecalls])

  return (
    <GlassCard hudVariant innerStyle={{ padding: 12 }}>
      <span style={{
        fontFamily:    'var(--fmo)',
        fontSize:      10,
        letterSpacing: '0.18em',
        textTransform: 'uppercase',
        color:         'rgba(255,255,255,0.38)',
        display:       'block',
        marginBottom:  10,
      }}>
        [ RECALL HISTORY ]
      </span>

      {items.length === 0 ? (
        <div style={{ fontFamily: 'var(--fmo)', fontSize: 11, color: 'var(--tf)' }}>
          Nessun recall recente
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          {items.map((item, i) => (
            <div key={i} style={{
              display:       'flex',
              alignItems:    'center',
              gap:           8,
              padding:       '5px 0',
              borderBottom:  '1px solid rgba(255,255,255,0.04)',
            }}>
              {/* Time ago */}
              <span className="mono-num" style={{
                fontSize:  10,
                color:     'var(--tf)',
                minWidth:  52,
                flexShrink: 0,
              }}>
                {formatRelativeTime(item.timestamp)}
              </span>

              {/* Agent badge */}
              <span style={{
                fontFamily:    'var(--fmo)',
                fontSize:      9,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color:         'rgba(255,255,255,0.28)',
                minWidth:      58,
                flexShrink:    0,
              }}>
                {item.agent}
              </span>

              {/* Query text */}
              <span style={{
                fontFamily: 'var(--fmo)',
                fontSize:   11,
                color:      'var(--tm)',
                flex:       1,
                overflow:   'hidden',
                whiteSpace: 'nowrap',
                textOverflow: 'ellipsis',
              }}>
                {item.query}
              </span>

              {/* Status dot */}
              <div style={{
                width:        5,
                height:       5,
                borderRadius: '50%',
                flexShrink:   0,
                background:   item.status === 'error'
                  ? 'rgba(255,68,68,0.85)'
                  : 'rgba(27,255,94,0.60)',
              }} />
            </div>
          ))}
        </div>
      )}
    </GlassCard>
  )
}

// ── ScreenWatcher Card ─────────────────────────────────────────────────────────

interface ScreenStatus {
  available:        boolean
  active:           boolean
  paused:           boolean
  captures_today:   number
  last_capture_time: string
  last_capture_app:  string
}

function ScreenWatcherCard() {
  const [st, setSt]         = useState<ScreenStatus | null>(null)
  const [toggling, setToggling] = useState(false)
  const watcherStep = useStore((s) => s.agentSteps['watcher'])

  const fetchStatus = useCallback(() => {
    fetch('/api/screen/status')
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setSt(d) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetchStatus()
    const id = setInterval(fetchStatus, 30_000)
    return () => clearInterval(id)
  }, [fetchStatus])

  const handleToggle = async () => {
    setToggling(true)
    try {
      const r = await fetch('/api/screen/toggle', { method: 'POST' })
      if (r.ok) {
        const d = await r.json()
        setSt((prev) => prev ? { ...prev, active: d.active, paused: !d.active } : prev)
      }
    } catch { /* ignore */ }
    setToggling(false)
  }

  const isActive = st?.active === true
  const capturesToday = st?.captures_today ?? 0
  const lastApp = st?.last_capture_app || '—'

  const todayWatcherSteps = (() => {
    if (!watcherStep) return 0
    const t = todayStart()
    return watcherStep.filter((s) => new Date(s.timestamp) >= t).length
  })()

  return (
    <GlassCard hudVariant innerStyle={{ padding: 12 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span style={{
          fontFamily:    'var(--fmo)',
          fontSize:      10,
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          color:         'rgba(255,255,255,0.38)',
        }}>
          [ SCREEN WATCHER ]
        </span>

        {/* Status badge */}
        <span style={{
          fontFamily:    'var(--fmo)',
          fontSize:      10,
          letterSpacing: '0.1em',
          textTransform: 'uppercase',
          padding:       '2px 6px',
          borderRadius:  3,
          marginLeft:    'auto',
          ...(!st || !st.available
            ? { background: 'rgba(255,255,255,0.05)', color: 'var(--tf)', border: '1px solid rgba(255,255,255,0.06)' }
            : isActive
              ? { background: 'rgba(27,255,94,0.12)', color: 'rgba(27,255,94,0.85)', border: '1px solid rgba(27,255,94,0.20)' }
              : { background: 'rgba(255,255,255,0.05)', color: 'var(--tf)', border: '1px solid rgba(255,255,255,0.06)' }
          ),
        }}>
          {!st || !st.available ? 'n/a' : isActive ? 'active' : 'paused'}
        </span>
      </div>

      {/* Metrics row */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 10 }}>
        <div>
          <div style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tf)', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 2 }}>
            App corrente
          </div>
          <div style={{ fontFamily: 'var(--fmo)', fontSize: 12, color: 'var(--tm)' }}>
            {lastApp}
          </div>
        </div>
        <div>
          <div style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tf)', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 2 }}>
            Catture oggi
          </div>
          <div className="mono-num" style={{ fontFamily: 'var(--fmo)', fontSize: 12, color: 'var(--tm)' }}>
            {capturesToday}
          </div>
        </div>
        <div>
          <div style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tf)', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 2 }}>
            Step oggi
          </div>
          <div className="mono-num" style={{ fontFamily: 'var(--fmo)', fontSize: 12, color: 'var(--tm)' }}>
            {todayWatcherSteps}
          </div>
        </div>
      </div>

      {/* Toggle button */}
      <button
        onClick={handleToggle}
        disabled={toggling || !st?.available}
        style={{
          fontFamily:    'var(--fmo)',
          fontSize:      11,
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
          padding:       '5px 14px',
          borderRadius:  5,
          border:        isActive
            ? '1px solid rgba(255,68,68,0.25)'
            : '1px solid rgba(27,255,94,0.25)',
          background:    isActive
            ? 'rgba(255,68,68,0.08)'
            : 'rgba(27,255,94,0.08)',
          color:         isActive
            ? 'rgba(255,68,68,0.85)'
            : 'rgba(27,255,94,0.85)',
          cursor:        toggling || !st?.available ? 'not-allowed' : 'pointer',
          opacity:       toggling || !st?.available ? 0.5 : 1,
          transition:    'all 0.2s var(--e-spring)',
        }}
        onMouseDown={(e) => { (e.currentTarget as HTMLElement).style.transform = 'scale(0.97)' }}
        onMouseUp={(e)   => { (e.currentTarget as HTMLElement).style.transform = 'scale(1)' }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.transform = 'scale(1)' }}
      >
        {toggling ? '...' : isActive ? 'Pausa' : 'Attiva'}
      </button>
    </GlassCard>
  )
}

// ── Quick Ask ──────────────────────────────────────────────────────────────────

function QuickAsk() {
  const [text,    setText]    = useState('')
  const [loading, setLoading] = useState(false)
  const [reply,   setReply]   = useState<string | null>(null)
  const [error,   setError]   = useState<string | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSubmit = async () => {
    const q = text.trim()
    if (!q || loading) return
    setLoading(true)
    setReply(null)
    setError(null)
    try {
      const r = await fetch('/api/personal/ask', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ text: q }),
      })
      const d = await r.json()
      if (r.ok && d.response) {
        setReply(d.response)
      } else {
        setError(d.error ?? 'Errore sconosciuto')
      }
    } catch {
      setError('Impossibile raggiungere il backend')
    }
    setLoading(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <GlassCard hudVariant innerStyle={{ padding: 12 }}>
      {/* Label */}
      <div style={{
        fontFamily:    'var(--fmo)',
        fontSize:      10,
        letterSpacing: '0.18em',
        textTransform: 'uppercase',
        color:         'rgba(255,255,255,0.38)',
        marginBottom:  10,
      }}>
        [ QUICK ASK ]
      </div>

      {/* Textarea */}
      <textarea
        ref={textareaRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Chiedi qualcosa… (⌘↵ per inviare)"
        rows={3}
        style={{
          width:           '100%',
          resize:          'none',
          background:      'rgba(255,255,255,0.03)',
          border:          '1px solid rgba(255,255,255,0.08)',
          borderRadius:    6,
          padding:         '8px 10px',
          fontFamily:      'var(--fmo)',
          fontSize:        12,
          color:           'var(--tp)',
          lineHeight:      1.5,
          outline:         'none',
          transition:      'border-color 0.3s var(--e-spring)',
          boxSizing:       'border-box',
          marginBottom:    8,
        }}
        onFocus={(e)  => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.20)' }}
        onBlur={(e)   => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.08)' }}
      />

      {/* Submit */}
      <button
        onClick={handleSubmit}
        disabled={loading || !text.trim()}
        style={{
          fontFamily:    'var(--fmo)',
          fontSize:      11,
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
          padding:       '5px 16px',
          borderRadius:  5,
          border:        '1px solid rgba(46,205,183,0.25)',
          background:    'rgba(46,205,183,0.08)',
          color:         loading || !text.trim() ? 'var(--tf)' : 'var(--acc)',
          cursor:        loading || !text.trim() ? 'not-allowed' : 'pointer',
          opacity:       loading || !text.trim() ? 0.5 : 1,
          transition:    'all 0.2s var(--e-spring)',
        }}
        onMouseDown={(e) => { if (!loading && text.trim()) (e.currentTarget as HTMLElement).style.transform = 'scale(0.98)' }}
        onMouseUp={(e)   => { (e.currentTarget as HTMLElement).style.transform = 'scale(1)' }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.transform = 'scale(1)' }}
      >
        {loading ? 'Invio…' : 'Invia'}
      </button>

      {/* Reply */}
      {reply && (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: 'spring', stiffness: 280, damping: 28 }}
          style={{
            marginTop:    10,
            padding:      '10px 12px',
            background:   'rgba(46,205,183,0.05)',
            border:       '1px solid rgba(46,205,183,0.12)',
            borderRadius: 6,
            fontFamily:   'var(--fmo)',
            fontSize:     12,
            color:        'var(--tm)',
            lineHeight:   1.6,
            whiteSpace:   'pre-wrap',
          }}
        >
          {reply}
        </motion.div>
      )}

      {/* Error */}
      {error && (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: 'spring', stiffness: 280, damping: 28 }}
          style={{
            marginTop:    10,
            padding:      '8px 10px',
            background:   'rgba(255,68,68,0.06)',
            border:       '1px solid rgba(255,68,68,0.18)',
            borderRadius: 6,
            fontFamily:   'var(--fmo)',
            fontSize:     11,
            color:        'rgba(255,68,68,0.85)',
          }}
        >
          {error}
        </motion.div>
      )}
    </GlassCard>
  )
}

// ── PersonalView ───────────────────────────────────────────────────────────────

export function PersonalView() {
  return (
    <>
      <style>{`
        .pv-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 12px;
        }
        @media (max-width: 640px) {
          .pv-grid { grid-template-columns: 1fr; }
        }
      `}</style>

      <div style={{
        width:         '100%',
        height:        '100%',
        padding:       20,
        overflowY:     'auto',
        overflowX:     'hidden',
        boxSizing:     'border-box',
        display:       'flex',
        flexDirection: 'column',
        gap:           16,
      }}>
        {/* ── Infra row ─────────────────────────────────────────────── */}
        <motion.section {...SPRING_ENTRY(0)}>
          <InfraRow />
        </motion.section>

        {/* ── Reminders + Recall feed ────────────────────────────────── */}
        <motion.section {...SPRING_ENTRY(0.04)}>
          <div className="pv-grid">
            <RemindersSection />
            <RecallFeed />
          </div>
        </motion.section>

        {/* ── ScreenWatcher ─────────────────────────────────────────── */}
        <motion.section {...SPRING_ENTRY(0.08)}>
          <ScreenWatcherCard />
        </motion.section>

        {/* ── Quick Ask ─────────────────────────────────────────────── */}
        <motion.section {...SPRING_ENTRY(0.10)}>
          <QuickAsk />
        </motion.section>
      </div>
    </>
  )
}
