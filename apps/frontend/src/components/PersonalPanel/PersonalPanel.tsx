import { useState, useEffect } from 'react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface RecentActivity {
  timestamp: string
  agent: string
  query: string
  status: 'ok' | 'error'
}

interface Reminder {
  id: number
  message: string
  when: string          // ISO8601
  status: 'pending' | 'triggered' | 'missed'
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

// ── Helpers ───────────────────────────────────────────────────────────────────

function relTime(iso: string): string {
  try {
    const d   = new Date(iso)
    const now = new Date()
    const ms  = now.getTime() - d.getTime()
    const m   = Math.round(ms / 60_000)
    if (m <  1) return 'adesso'
    if (m < 60) return `${m}m fa`
    const h = Math.round(m / 60)
    if (h < 24) return `${h}h fa`
    return d.toLocaleDateString('it-IT', { day: '2-digit', month: 'short' })
  } catch { return '—' }
}

function absTime(iso: string): string {
  try {
    const d   = new Date(iso)
    const now = new Date()
    const diffMs = d.getTime() - now.getTime()

    if (diffMs < 0) return 'scaduto'

    const m = Math.round(diffMs / 60_000)
    if (m < 60)  return `tra ${m}m`
    const h = Math.round(m / 60)
    if (h < 24)  return `oggi ${d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })}`

    const days = Math.floor(diffMs / 86_400_000)
    if (days === 1) return `dom ${d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })}`

    return d.toLocaleDateString('it-IT', { weekday: 'short', day: '2-digit', month: 'short' })
      + ' ' + d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })
  } catch { return '—' }
}

function agentLabel(name: string): string {
  const map: Record<string, string> = {
    recall:            'RECALL',
    remind:            'REMIND',
    summarize:         'SUMM',
    research_personal: 'RESEARCH',
    watcher:           'WATCHER',
    file:              'FILE',
    notion:            'NOTION',
    gmail:             'GMAIL',
    calendar:          'CALENDAR',
  }
  return map[name] ?? name.toUpperCase().slice(0, 8)
}

// ── Section header ─────────────────────────────────────────────────────────

function SectionHeader({ label, count }: { label: string; count?: number }) {
  return (
    <div style={{
      padding: '8px 13px 6px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      borderBottom: '1px solid var(--b0)',
      flexShrink: 0,
    }}>
      <span style={{
        fontFamily: 'var(--fh)',
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: '0.08em',
        textTransform: 'uppercase' as const,
        color: 'var(--tm)',
      }}>
        {label}
      </span>
      {count !== undefined && (
        <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)' }}>
          {count}
        </span>
      )}
    </div>
  )
}

// ── Recent Activity ────────────────────────────────────────────────────────

function ActivitySection({ items }: { items: RecentActivity[] }) {
  return (
    <div style={{ flexShrink: 0 }}>
      <SectionHeader label="Attività Recente" count={items.length || undefined} />
      {items.length === 0 ? (
        <div style={{ padding: '10px 13px' }}>
          <span style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tf)' }}>
            Nessuna attività recente
          </span>
        </div>
      ) : (
        <div style={{ padding: '3px 13px 6px' }}>
          {items.map((item, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '4px 3px',
                borderRadius: 4,
                transition: 'background .2s var(--e-io)',
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'rgba(45,232,106,.04)' }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
            >
              {/* Time */}
              <span style={{
                fontFamily: 'var(--fd)',
                fontSize: 11,
                color: 'var(--tf)',
                width: 44,
                flexShrink: 0,
              }}>
                {relTime(item.timestamp)}
              </span>

              {/* Agent badge */}
              <span style={{
                fontFamily: 'var(--fd)',
                fontSize: 10,
                padding: '1px 5px',
                borderRadius: 3,
                border: '1px solid rgba(45,232,106,.18)',
                color: 'var(--accent)',
                flexShrink: 0,
                letterSpacing: '0.04em',
              }}>
                {agentLabel(item.agent)}
              </span>

              {/* Query */}
              <span style={{
                fontFamily: 'var(--fd)',
                fontSize: 12,
                color: 'var(--tm)',
                flex: 1,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap' as const,
              }}>
                {item.query}
              </span>

              {/* Status dot */}
              <span style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: item.status === 'ok' ? 'var(--ok)' : 'var(--err)',
                flexShrink: 0,
                opacity: 0.8,
              }} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Reminders ──────────────────────────────────────────────────────────────

function RemindersSection({ items }: { items: Reminder[] }) {
  const pending = items.filter((r) => r.status === 'pending')
  return (
    <div style={{ flexShrink: 0 }}>
      <SectionHeader label="Prossimi Reminder" count={pending.length || undefined} />
      {pending.length === 0 ? (
        <div style={{ padding: '10px 13px' }}>
          <span style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tf)' }}>
            Nessun reminder in attesa
          </span>
        </div>
      ) : (
        <div style={{ padding: '3px 13px 6px' }}>
          {pending.map((r) => (
            <div
              key={r.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '4px 3px',
                borderRadius: 4,
                transition: 'background .2s var(--e-io)',
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'rgba(45,232,106,.04)' }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
            >
              {/* When */}
              <span style={{
                fontFamily: 'var(--fd)',
                fontSize: 11,
                color: 'var(--accent)',
                width: 70,
                flexShrink: 0,
              }}>
                {absTime(r.when)}
              </span>

              {/* Message */}
              <span style={{
                fontFamily: 'var(--fd)',
                fontSize: 13,
                color: 'var(--tp)',
                flex: 1,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap' as const,
              }}>
                {r.message}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Connections ────────────────────────────────────────────────────────────

type ConnStatus = 'ok' | 'error' | 'unknown'

function ConnDot({ status }: { status: ConnStatus }) {
  const color =
    status === 'ok'      ? 'var(--ok)'     :
    status === 'error'   ? 'var(--err)'    :
    /* unknown */          'var(--tf)'
  return (
    <span style={{
      width: 7,
      height: 7,
      borderRadius: '50%',
      background: color,
      flexShrink: 0,
      display: 'inline-block',
      marginRight: 4,
      boxShadow: status === 'ok' ? '0 0 5px rgba(45,232,106,.5)' : 'none',
      transition: 'background .3s, box-shadow .3s',
    }} />
  )
}

function ConnectionsSection({
  mcp,
  ollama,
}: {
  mcp:    McpStatus | null
  ollama: OllamaStatus | null
}) {
  const mcpItems: { key: keyof McpStatus; label: string }[] = [
    { key: 'notion',   label: 'Notion'   },
    { key: 'gmail',    label: 'Gmail'    },
    { key: 'calendar', label: 'Calendar' },
  ]

  return (
    <div style={{ flexShrink: 0 }}>
      <SectionHeader label="Connessioni" />
      <div style={{ padding: '6px 13px 10px', display: 'flex', flexDirection: 'column', gap: 5 }}>

        {/* MCP services */}
        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' as const }}>
          {mcpItems.map(({ key, label }) => (
            <span key={key} style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tm)', display: 'flex', alignItems: 'center' }}>
              <ConnDot status={mcp ? mcp[key] : 'unknown'} />
              {label}
            </span>
          ))}
        </div>

        {/* Ollama */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '5px 8px',
          borderRadius: 6,
          background: 'var(--s2)',
          border: '1px solid var(--b0)',
          marginTop: 3,
        }}>
          <ConnDot status={ollama?.loaded ? 'ok' : 'unknown'} />
          <span style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tm)' }}>
            Ollama
          </span>
          {ollama && (
            <>
              <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)' }}>
                {ollama.model}
              </span>
              <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: ollama.loaded ? 'var(--accent)' : 'var(--tf)', marginLeft: 'auto' }}>
                {ollama.loaded ? 'warm' : 'cold'}
              </span>
              {ollama.latency_ms !== null && (
                <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)' }}>
                  {ollama.latency_ms}ms
                </span>
              )}
              <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--ok)', opacity: 0.8 }}>
                €0
              </span>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────

export function PersonalPanel() {
  const [activity, setActivity] = useState<RecentActivity[]>([])
  const [reminders, setReminders] = useState<Reminder[]>([])
  const [mcp, setMcp] = useState<McpStatus | null>(null)
  const [ollama, setOllama] = useState<OllamaStatus | null>(null)

  const fetchAll = () => {
    fetch('/api/personal/recalls?limit=8')
      .then((r) => r.ok ? r.json() : { items: [] })
      .then((d) => setActivity(Array.isArray(d.items) ? d.items : []))
      .catch(() => {})

    fetch('/api/personal/reminders?limit=10')
      .then((r) => r.ok ? r.json() : { items: [] })
      .then((d) => setReminders(Array.isArray(d.items) ? d.items : []))
      .catch(() => {})

    fetch('/api/personal/mcp/status')
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setMcp(d) })
      .catch(() => {})

    fetch('/api/ollama/status')
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setOllama(d) })
      .catch(() => {})
  }

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchAll, 30_000)
    return () => clearInterval(id)
  }, [])

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      overflow: 'hidden',
    }}>
      {/* Panel header */}
      <div style={{
        padding: '8px 13px',
        borderBottom: '1px solid var(--b1)',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
      }}>
        <span style={{
          fontFamily: 'var(--fh)',
          fontSize: 13,
          fontWeight: 700,
          letterSpacing: '0.06em',
          color: 'var(--tp)',
          textTransform: 'uppercase' as const,
        }}>
          Personal
        </span>
        <span style={{
          fontFamily: 'var(--fd)',
          fontSize: 11,
          color: 'var(--tf)',
          padding: '1px 7px',
          borderRadius: 99,
          border: '1px solid var(--b0)',
          marginLeft: 'auto',
        }}>
          OLLAMA · €0
        </span>
      </div>

      {/* Scrollable body */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 0,
      }}>
        <ActivitySection items={activity} />

        <div style={{ height: 1, background: 'var(--b0)', flexShrink: 0 }} />
        <RemindersSection items={reminders} />

        <div style={{ height: 1, background: 'var(--b0)', flexShrink: 0 }} />
        <ConnectionsSection mcp={mcp} ollama={ollama} />
      </div>
    </div>
  )
}
