import { useState, useEffect, useMemo } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { useStore } from '../../store'

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

type ConnStatus = 'ok' | 'error' | 'unknown'

const ETSY_AGENTS = ['research', 'design', 'publisher', 'analytics', 'finance']

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

function nextReminderLabel(reminders: Reminder[]): string {
  const pending = reminders
    .filter((r) => r.status === 'pending')
    .sort((a, b) => new Date(a.when).getTime() - new Date(b.when).getTime())
  if (!pending.length) return 'nessuno in attesa'
  const next = pending[0]
  const d    = new Date(next.when)
  const now  = new Date()
  const diffMs = d.getTime() - now.getTime()
  if (diffMs < 0) return 'scaduto'
  const m = Math.round(diffMs / 60_000)
  if (m < 60) return `prossimo tra ${m}m`
  const h = Math.round(m / 60)
  if (h < 24) return `oggi ${d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })}`
  return d.toLocaleDateString('it-IT', { weekday: 'short', day: '2-digit', month: 'short' })
}

function ConnDot({ status }: { status: ConnStatus }) {
  return (
    <span className={`conn-dot ${status === 'ok' ? 'ok' : status === 'error' ? 'err' : 'unk'}`} />
  )
}

/* ── section label ───────────────────────────────────────────────── */
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontFamily:    'var(--fmo)',
      fontSize:      9,
      fontWeight:    700,
      letterSpacing: '.1em',
      textTransform: 'uppercase',
      color:         'var(--tf)',
      paddingTop:    4,
      paddingBottom: 2,
      borderBottom:  '1px solid var(--bs)',
    }}>
      {children}
    </div>
  )
}

/* ── component ───────────────────────────────────────────────────── */
export function PersonalQuickCard({ onOpen }: { onOpen?: () => void }) {
  const { agentSteps, analyticsSummary } = useStore(
    useShallow((s) => ({
      agentSteps:       s.agentSteps,
      analyticsSummary: s.analyticsSummary,
    }))
  )

  const [reminders,  setReminders]  = useState<Reminder[]>([])
  const [lastRecall, setLastRecall] = useState<RecallItem | null>(null)
  const [mcp,        setMcp]        = useState<McpStatus | null>(null)

  useEffect(() => {
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
            { id: 1, message: 'Check analytics dashboard', when: new Date(Date.now() + 2 * 3600_000).toISOString(), status: 'pending' },
            { id: 2, message: 'Review Etsy listings',      when: new Date(Date.now() + 5 * 3600_000).toISOString(), status: 'pending' },
          ])
        }
      }

      try {
        const r = await fetch('/api/personal/recalls?limit=1')
        if (!cancelled) {
          const d = r.ok ? await r.json() : { items: [] }
          setLastRecall(Array.isArray(d.items) && d.items.length ? d.items[0] : null)
        }
      } catch {
        if (!cancelled && import.meta.env.DEV) {
          setLastRecall({ timestamp: new Date(Date.now() - 3 * 60_000).toISOString(), agent: 'recall', query: 'branding handmade', status: 'ok' })
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
    const id = setInterval(fetchAll, 30_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  /* ── Etsy: last pipeline step ── */
  const lastEtsyStep = useMemo(() => {
    let latest = null as { agent: string; description: string; timestamp: string } | null
    for (const ag of ETSY_AGENTS) {
      for (const step of agentSteps[ag] ?? []) {
        if (!latest || step.timestamp > latest.timestamp) {
          latest = { agent: ag, description: step.description, timestamp: step.timestamp }
        }
      }
    }
    return latest
  }, [agentSteps])

  /* ── Etsy: production queue ── */
  const pq      = analyticsSummary?.production_queue ?? {}
  const pending = (pq['pending'] ?? 0) + (pq['queued'] ?? 0)
  const inCorso = pq['running'] ?? 0
  const oggiStr = (() => {
    if (!analyticsSummary) return '—'
    const today = new Date().toISOString().split('T')[0]
    const pd = analyticsSummary.per_day?.[today]
    return pd ? String(Object.values(pd).reduce((a, b) => a + b, 0)) : '—'
  })()

  /* ── Reminder ── */
  const pendingCount = reminders.filter((r) => r.status === 'pending').length

  return (
    <>
      {/* ── header ── */}
      <div className="qcard-head">
        <span className="qcard-title">Brief</span>
        <button className="qcard-action" onClick={onOpen}>Espandi →</button>
      </div>

      {/* ── body ── */}
      <div className="qcard-body">

        {/* Reminder — condiviso, cross-domain */}
        <div className="p-item" onClick={onOpen} style={{ cursor: 'pointer' }}>
          <span className={`dc-adot${pendingCount > 0 ? ' run' : ''}`} style={{ flexShrink: 0 }} />
          <div className="p-item-body">
            <div className="p-item-lbl">Reminder</div>
            <div className="p-item-val">
              {pendingCount > 0
                ? `${pendingCount} in attesa — ${nextReminderLabel(reminders)}`
                : 'nessuno in attesa'}
            </div>
          </div>
          {pendingCount > 0 && <span className="p-item-badge">{pendingCount}</span>}
        </div>

        {/* ── Personal ── */}
        <SectionLabel>Personal</SectionLabel>

        <div className="p-item">
          <span className="dc-adot" style={{ flexShrink: 0 }} />
          <div className="p-item-body">
            <div className="p-item-lbl">Recall — ultima query</div>
            <div className="p-item-val">
              {lastRecall
                ? `"${lastRecall.query}" · ${relTime(lastRecall.timestamp)}`
                : 'nessuna query recente'}
            </div>
          </div>
        </div>

        <div className="conn-row-qc">
          <ConnDot status={mcp?.gmail    ?? 'unknown'} />
          <span className="conn-lbl">Gmail</span>
          <span className="conn-sep">·</span>
          <ConnDot status={mcp?.notion   ?? 'unknown'} />
          <span className="conn-lbl">Notion</span>
          <span className="conn-sep">·</span>
          <ConnDot status={mcp?.calendar ?? 'unknown'} />
          <span className="conn-lbl">Calendar</span>
        </div>

        {/* ── Etsy Store ── */}
        <SectionLabel>Etsy Store</SectionLabel>

        <div className="p-item">
          <span className={`dc-adot${lastEtsyStep ? ' run' : ''}`} style={{ flexShrink: 0 }} />
          <div className="p-item-body">
            <div className="p-item-lbl">
              {lastEtsyStep ? lastEtsyStep.agent.toUpperCase() : 'Pipeline'}
            </div>
            <div className="p-item-val">
              {lastEtsyStep
                ? `${lastEtsyStep.description.slice(0, 46)}${lastEtsyStep.description.length > 46 ? '…' : ''} · ${relTime(lastEtsyStep.timestamp)}`
                : 'nessuna attività recente'}
            </div>
          </div>
        </div>

        <div className="conn-row-qc">
          <span className="conn-lbl" style={{ color: pending > 0 ? 'var(--acc)' : 'var(--tm)' }}>
            {pending} pending
          </span>
          <span className="conn-sep">·</span>
          <span className={`dc-adot${inCorso > 0 ? ' run' : ''}`} style={{ flexShrink: 0 }} />
          <span className="conn-lbl">{inCorso} in corso</span>
          <span className="conn-sep">·</span>
          <span className="conn-lbl">{oggiStr} oggi</span>
        </div>

      </div>
    </>
  )
}
