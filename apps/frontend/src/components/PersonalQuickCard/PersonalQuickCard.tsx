import { useState, useEffect } from 'react'

/* ── types ──────────────────────────────────────────────────── */
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

interface OllamaStatus {
  model:      string
  loaded:     boolean
  latency_ms: number | null
}

/* ── helpers ─────────────────────────────────────────────────── */
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
  const d = new Date(next.when)
  const now = new Date()
  const diffMs = d.getTime() - now.getTime()
  if (diffMs < 0) return 'scaduto'
  const m = Math.round(diffMs / 60_000)
  if (m < 60) return `prossimo tra ${m}m`
  const h = Math.round(m / 60)
  if (h < 24) return `oggi ${d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })}`
  return d.toLocaleDateString('it-IT', { weekday: 'short', day: '2-digit', month: 'short' })
}

type ConnStatus = 'ok' | 'error' | 'unknown'

function ConnDot({ status }: { status: ConnStatus }) {
  return (
    <span
      className={`conn-dot ${status === 'ok' ? 'ok' : status === 'error' ? 'err' : 'unk'}`}
    />
  )
}

/* ── component ───────────────────────────────────────────────── */
export function PersonalQuickCard({ onOpen }: { onOpen?: () => void }) {
  const [reminders, setReminders] = useState<Reminder[]>([])
  const [lastRecall, setLastRecall] = useState<RecallItem | null>(null)
  const [mcp, setMcp]             = useState<McpStatus | null>(null)
  const [ollama, setOllama]       = useState<OllamaStatus | null>(null)

  useEffect(() => {
    let cancelled = false

    const fetchAll = async () => {
      // reminders
      try {
        const r = await fetch('/api/personal/reminders?limit=10')
        if (!cancelled) {
          const d = r.ok ? await r.json() : { items: [] }
          setReminders(Array.isArray(d.items) ? d.items : [])
        }
      } catch {
        // offline: inject dev mock
        if (!cancelled && import.meta.env.DEV) {
          setReminders([
            { id: 1, message: 'Check analytics dashboard', when: new Date(Date.now() + 2 * 3600_000).toISOString(), status: 'pending' },
            { id: 2, message: 'Review Etsy listings',      when: new Date(Date.now() + 5 * 3600_000).toISOString(), status: 'pending' },
          ])
        }
      }

      // recalls
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

      // mcp status
      try {
        const r = await fetch('/api/personal/mcp/status')
        if (!cancelled) {
          const d = r.ok ? await r.json() : null
          if (d) setMcp(d)
        }
      } catch { /* stay null = unknown */ }

      // ollama
      try {
        const r = await fetch('/api/ollama/status')
        if (!cancelled) {
          const d = r.ok ? await r.json() : null
          if (d) setOllama(d)
        }
      } catch { /* stay null */ }
    }

    fetchAll()
    const id = setInterval(fetchAll, 30_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  const pendingCount = reminders.filter((r) => r.status === 'pending').length

  return (
    <>
      {/* ── header ── */}
      <div className="qcard-head">
        <span className="qcard-title">Personal</span>
        <button className="qcard-action" onClick={onOpen}>Espandi →</button>
      </div>

      {/* ── body ── */}
      <div className="qcard-body">

        {/* Reminder row */}
        <div className="p-item" onClick={onOpen} style={{ cursor: 'pointer' }}>
          <span className="p-item-icon">⏰</span>
          <div className="p-item-body">
            <div className="p-item-lbl">Reminder</div>
            <div className="p-item-val">
              {pendingCount > 0
                ? `${pendingCount} in attesa — ${nextReminderLabel(reminders)}`
                : 'nessuno in attesa'}
            </div>
          </div>
          {pendingCount > 0 && (
            <span className="p-item-badge">{pendingCount}</span>
          )}
        </div>

        {/* Recall row */}
        <div className="p-item" onClick={onOpen} style={{ cursor: 'pointer' }}>
          <span className="p-item-icon">🔍</span>
          <div className="p-item-body">
            <div className="p-item-lbl">Recall — ultima query</div>
            <div className="p-item-val">
              {lastRecall
                ? `Ricerca "${lastRecall.query}" · ${relTime(lastRecall.timestamp)}`
                : 'nessuna query recente'}
            </div>
          </div>
        </div>

        {/* Connections row */}
        <div className="conn-row-qc">
          <ConnDot status={mcp?.gmail    ?? 'unknown'} />
          <span className="conn-lbl">Gmail</span>
          <span className="conn-sep">·</span>
          <ConnDot status={mcp?.notion   ?? 'unknown'} />
          <span className="conn-lbl">Notion</span>
          <span className="conn-sep">·</span>
          <ConnDot status={mcp?.calendar ?? 'unknown'} />
          <span className="conn-lbl">Calendar</span>
          <span style={{
            marginLeft: 'auto',
            fontFamily: 'var(--fmo)',
            fontSize: 10,
            color: ollama?.loaded ? 'var(--tf)' : 'var(--tf)',
          }}>
            {ollama
              ? `Ollama ${ollama.loaded ? 'warm' : 'cold'}${ollama.latency_ms !== null ? ` · ${ollama.latency_ms}ms` : ''}`
              : 'Ollama —'}
          </span>
        </div>

      </div>
    </>
  )
}
