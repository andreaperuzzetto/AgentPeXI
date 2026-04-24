import { useEffect, useState, useMemo } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { useStore } from '../../store'
import './ContextOverlay.css'

/* ── types ──────────────────────────────────────────────────────── */
interface Reminder {
  id: number
  message: string
  when: string
  status: 'pending' | 'triggered' | 'missed'
}

interface RecallItem {
  timestamp: string
  agent: string
  query: string
  status: 'ok' | 'error'
}

interface McpStatus {
  notion:   'ok' | 'error' | 'unknown'
  gmail:    'ok' | 'error' | 'unknown'
  calendar: 'ok' | 'error' | 'unknown'
}

interface Props {
  open: boolean
  onClose: () => void
}

/* ── helpers ─────────────────────────────────────────────────────── */
function relTime(iso: string): string {
  try {
    const ms = Date.now() - new Date(iso).getTime()
    const m  = Math.round(ms / 60_000)
    if (m < 1)  return 'adesso'
    if (m < 60) return `${m}m fa`
    const h = Math.round(m / 60)
    if (h < 24) return `${h}h fa`
    return new Date(iso).toLocaleDateString('it-IT', { day: '2-digit', month: 'short' })
  } catch { return '—' }
}

function fmtWhen(iso: string): string {
  try {
    const d = new Date(iso)
    const now = new Date()
    const diffMs = d.getTime() - now.getTime()
    if (diffMs < 0) return 'scaduto'
    const m = Math.round(diffMs / 60_000)
    if (m < 60) return `tra ${m}m`
    const h = Math.round(m / 60)
    if (h < 24) return `oggi ${d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })}`
    return d.toLocaleDateString('it-IT', { weekday: 'short', day: '2-digit', month: 'short' })
  } catch { return '—' }
}

type ConnStatus = 'ok' | 'error' | 'unknown'

function ConnDot({ status }: { status: ConnStatus }) {
  const color =
    status === 'ok'    ? 'var(--ok)' :
    status === 'error' ? 'var(--err)' :
    'var(--tf)'
  const shadow = status === 'ok' ? '0 0 5px rgba(27,255,94,.5)' : 'none'
  return (
    <span style={{
      display: 'inline-block',
      width: 6, height: 6,
      borderRadius: '50%',
      background: color,
      boxShadow: shadow,
      flexShrink: 0,
    }} />
  )
}

const ETSY_AGENTS = ['research', 'design', 'publisher', 'analytics', 'finance']

/* ── component ───────────────────────────────────────────────────── */
export function ContextOverlay({ open, onClose }: Props) {
  const { agentSteps, analyticsSummary } = useStore(
    useShallow((s) => ({
      agentSteps:       s.agentSteps,
      analyticsSummary: s.analyticsSummary,
    }))
  )

  const [reminders, setReminders] = useState<Reminder[]>([])
  const [recalls,   setRecalls]   = useState<RecallItem[]>([])
  const [mcp,       setMcp]       = useState<McpStatus | null>(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false

    const fetchAll = async () => {
      try {
        const r = await fetch('/api/personal/reminders?limit=10')
        if (!cancelled) {
          const d = r.ok ? await r.json() : { items: [] }
          setReminders(Array.isArray(d.items) ? d.items : [])
        }
      } catch {
        if (!cancelled && import.meta.env.DEV) {
          setReminders([
            { id: 1, message: 'Check analytics dashboard',  when: new Date(Date.now() + 2 * 3600_000).toISOString(), status: 'pending' },
            { id: 2, message: 'Review Etsy listings',       when: new Date(Date.now() + 5 * 3600_000).toISOString(), status: 'pending' },
            { id: 3, message: 'Backup database',            when: new Date(Date.now() - 1 * 3600_000).toISOString(), status: 'triggered' },
          ])
        }
      }

      try {
        const r = await fetch('/api/personal/recalls?limit=5')
        if (!cancelled) {
          const d = r.ok ? await r.json() : { items: [] }
          setRecalls(Array.isArray(d.items) ? d.items : [])
        }
      } catch {
        if (!cancelled && import.meta.env.DEV) {
          setRecalls([
            { timestamp: new Date(Date.now() - 3 * 60_000).toISOString(),  agent: 'recall', query: 'branding handmade', status: 'ok' },
            { timestamp: new Date(Date.now() - 18 * 60_000).toISOString(), agent: 'recall', query: 'trend earrings 2025', status: 'ok' },
          ])
        }
      }

      try {
        const r = await fetch('/api/personal/mcp/status')
        if (!cancelled) {
          const d = r.ok ? await r.json() : null
          if (d) setMcp(d)
        }
      } catch { /* stay null */ }
    }

    fetchAll()
    return () => { cancelled = true }
  }, [open])

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose])

  // Etsy — last 5 steps across pipeline agents
  const lastEtsySteps = useMemo(() => {
    const all: { agent: string; description: string; stepType: string; timestamp: string }[] = []
    for (const ag of ETSY_AGENTS) {
      const steps = agentSteps[ag] ?? []
      for (const step of steps) {
        all.push({ agent: ag, description: step.description, stepType: step.stepType, timestamp: step.timestamp })
      }
    }
    return all
      .sort((a, b) => (b.timestamp ?? '').localeCompare(a.timestamp ?? ''))
      .slice(0, 5)
  }, [agentSteps])

  // Etsy — production queue
  const pq = analyticsSummary?.production_queue ?? {}
  const pending = (pq['pending'] ?? 0) + (pq['queued'] ?? 0)
  const inCorso = pq['running'] ?? 0
  const oggiCount = (() => {
    if (!analyticsSummary) return '—'
    const today = new Date().toISOString().split('T')[0]
    const pd = analyticsSummary.per_day?.[today]
    return pd ? String(Object.values(pd).reduce((a, b) => a + b, 0)) : '—'
  })()

  // Reminders
  const pendingReminders = reminders.filter((r) => r.status === 'pending')

  if (!open) return null

  return (
    <div
      className="an-backdrop"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="an-modal ctx-modal">

        {/* ── header ── */}
        <div className="an-modal-head">
          <span style={{ fontFamily: 'var(--fh)', fontSize: 19, fontWeight: 700, letterSpacing: '0.04em', color: 'var(--tp)' }}>
            Brief
          </span>
          <span style={{ fontFamily: 'var(--fd)', fontSize: 14, color: 'var(--tf)', marginLeft: 10 }}>
            {new Date().toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })}
          </span>
          <div style={{ flex: 1 }} />
          <button onClick={onClose} className="an-close-btn">✕ Chiudi</button>
        </div>

        {/* ── body ── */}
        <div className="ctx-body">

          {/* ── Reminder — fascia full-width, cross-domain ── */}
          <div className="ctx-reminder-bar">
            <span className="ctx-reminder-lbl">Reminder</span>
            {pendingReminders.length === 0 ? (
              <span style={{ fontFamily: 'var(--fmo)', fontSize: 11, color: 'var(--tf)' }}>
                nessuno in attesa
              </span>
            ) : (
              <div className="ctx-reminder-list">
                {pendingReminders.map((r) => (
                  <div key={r.id} className="ctx-reminder-item">
                    <span
                      style={{
                        display: 'inline-block', width: 5, height: 5,
                        borderRadius: '50%', background: 'var(--ok)',
                        boxShadow: '0 0 5px rgba(27,255,94,.5)',
                        flexShrink: 0, marginTop: 1,
                      }}
                    />
                    <span style={{ fontFamily: 'var(--fmo)', fontSize: 11, color: 'var(--tm)', flex: 1 }}>
                      {r.message}
                    </span>
                    <span style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--acc)', flexShrink: 0 }}>
                      {fmtWhen(r.when)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ── due colonne ── */}
          <div className="ctx-cols">

          {/* ══ PERSONAL ══ */}
          <div className="ctx-col">
            <div className="ctx-col-head">Personal</div>

            {/* Recall */}
            <CtxCard title="Recall — ultime query">
              {recalls.length === 0 ? (
                <span className="an-sub">Nessuna query recente</span>
              ) : (
                <div className="metric-list">
                  {recalls.map((rc, i) => (
                    <div key={i} className="mi">
                      <span className="mi-l" style={{ flex: 1, marginRight: 8 }}>"{rc.query}"</span>
                      <span className="mi-v" style={{ color: 'var(--tf)', flexShrink: 0 }}>{relTime(rc.timestamp)}</span>
                    </div>
                  ))}
                </div>
              )}
            </CtxCard>

            {/* Connessioni */}
            <CtxCard title="Connessioni">
              <div className="metric-list">
                {([
                  { label: 'Gmail',    status: mcp?.gmail    ?? 'unknown' },
                  { label: 'Notion',   status: mcp?.notion   ?? 'unknown' },
                  { label: 'Calendar', status: mcp?.calendar ?? 'unknown' },
                ] as { label: string; status: ConnStatus }[]).map((c) => (
                  <div key={c.label} className="mi">
                    <span className="mi-l">{c.label}</span>
                    <span className="mi-v" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <ConnDot status={c.status} />
                      <span style={{ color: c.status === 'ok' ? 'var(--ok)' : c.status === 'error' ? 'var(--err)' : 'var(--tf)', fontSize: 10 }}>
                        {c.status === 'ok' ? 'attivo' : c.status === 'error' ? 'errore' : 'sconosciuto'}
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            </CtxCard>
          </div>

          {/* ── divisore verticale ── */}
          <div className="ctx-divider" />

          {/* ══ ETSY STORE ══ */}
          <div className="ctx-col">
            <div className="ctx-col-head">Etsy Store</div>

            {/* Pipeline — ultima attività */}
            <CtxCard title="Pipeline — ultima attività">
              {lastEtsySteps.length === 0 ? (
                <span className="an-sub">Nessuna attività recente</span>
              ) : (
                <div className="metric-list">
                  {lastEtsySteps.map((s, i) => (
                    <div key={i} className="mi" style={{ gap: 8 }}>
                      <span
                        className="mi-l"
                        style={{
                          width: 60,
                          flexShrink: 0,
                          textTransform: 'uppercase',
                          letterSpacing: '0.04em',
                          color: 'var(--acc)',
                        }}
                      >
                        {s.agent}
                      </span>
                      <span className="mi-l" style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {s.description}
                      </span>
                      <span className="mi-v" style={{ color: 'var(--tf)', flexShrink: 0 }}>
                        {relTime(s.timestamp)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </CtxCard>

            {/* Production queue */}
            <CtxCard title="Production Queue">
              <div className="metric-list">
                {[
                  { l: 'Pending',    v: String(pending),  vc: pending > 0 ? 'var(--acc)' : 'var(--tf)' },
                  { l: 'In corso',   v: String(inCorso),  vc: inCorso > 0 ? 'var(--ok)' : 'var(--tf)' },
                  { l: 'Oggi',       v: oggiCount,        vc: 'var(--tm)' },
                ].map((row) => (
                  <div key={row.l} className="mi">
                    <span className="mi-l">{row.l}</span>
                    <span className="mi-v" style={{ color: row.vc }}>{row.v}</span>
                  </div>
                ))}
              </div>
            </CtxCard>

          </div>

          </div>{/* fine ctx-cols */}
        </div>
      </div>
    </div>
  )
}

function CtxCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card ctx-card">
      <div className="an-t">{title}</div>
      {children}
    </div>
  )
}
