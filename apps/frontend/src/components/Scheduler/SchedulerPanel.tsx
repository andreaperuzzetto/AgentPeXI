import { useState, useEffect } from 'react'

interface Schedule {
  id: number | string
  name: string
  cron_expression?: string | null
  interval?: string | null
  enabled: boolean | number
  next_run?: string | null
  nextRun?: string | null
  agent_name?: string | null
}

/* Extract HH:MM from next_run ISO or cron expression */
function formatTime(s: Schedule): string {
  const nextRun = s.next_run ?? s.nextRun
  if (nextRun) {
    try {
      return new Date(nextRun).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })
    } catch { /* ignore */ }
  }
  /* Try HH:MM from cron expression (field 1 = hour, field 0 = minute) */
  const cron = s.cron_expression ?? s.interval ?? ''
  const parts = cron.trim().split(/\s+/)
  if (parts.length >= 2 && parts[0] !== '*' && parts[1] !== '*') {
    return `${parts[1].padStart(2, '0')}:${parts[0].padStart(2, '0')}`
  }
  const m = cron.match(/(\d{1,2}:\d{2})/)
  return m ? m[1] : cron.slice(0, 5) || '—'
}

function tagStyle(enabled: boolean): { bg: string; color: string; border: string; label: string } {
  if (enabled) {
    return {
      bg:     'rgba(45,232,106,.06)',
      color:  'var(--accent)',
      border: '1px solid rgba(45,232,106,.2)',
      label:  'ORA',
    }
  }
  return {
    bg:     'var(--s2)',
    color:  'var(--tf)',
    border: '1px solid var(--b0)',
    label:  'SCHED',
  }
}

export function SchedulerPanel() {
  const [schedules, setSchedules] = useState<Schedule[]>([])

  useEffect(() => {
    fetch('/api/scheduler')
      .then((r) => (r.ok ? r.json() : { tasks: [] }))
      .then((data) => {
        const tasks = data.tasks ?? data
        setSchedules(Array.isArray(tasks) ? tasks : [])
      })
      .catch(() => setSchedules([]))
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, overflow: 'hidden' }}>
      {/* Mini title — matches .mini-title */}
      <div
        style={{
          padding: '8px 13px',
          borderBottom: '1px solid var(--b0)',
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <span
          style={{
            fontFamily: 'var(--fh)',
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase' as const,
            color: 'var(--tm)',
          }}
        >
          Scheduler
        </span>
      </div>

      {schedules.length === 0 ? (
        <div style={{ padding: '16px 13px', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
          <svg width="28" height="28" viewBox="0 0 20 20" fill="none" style={{ color: 'var(--tf)', opacity: 0.5 }}>
            <circle cx="10" cy="10" r="7.5" stroke="currentColor" strokeWidth="1.2" />
            <path d="M10 6v4.5l3 1.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)' }}>
            Nessun job schedulato
          </span>
        </div>
      ) : (
        <div style={{ flex: 1, overflowY: 'auto', padding: '4px 13px' }}>
          {schedules.map((t) => {
            const isEnabled = Boolean(t.enabled)
            const ts = tagStyle(isEnabled)
            const label = t.agent_name ? `${t.agent_name} · ${t.name}` : t.name
            return (
              <SchedRow
                key={t.id}
                time={formatTime(t)}
                label={label}
                tagLabel={ts.label}
                tagBg={ts.bg}
                tagColor={ts.color}
                tagBorder={ts.border}
                labelColor={isEnabled ? 'var(--tp)' : 'var(--tm)'}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}

function SchedRow({
  time,
  label,
  tagLabel,
  tagBg,
  tagColor,
  tagBorder,
  labelColor = 'var(--tm)',
}: {
  time: string
  label: string
  tagLabel: string
  tagBg: string
  tagColor: string
  tagBorder: string
  labelColor?: string
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '4px 3px',
        fontSize: 12,
        borderRadius: 4,
        transition: 'background .2s var(--e-io)',
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'rgba(45,232,106,.04)' }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
    >
      {/* .sched-time */}
      <span
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 10,
          color: 'var(--tm)',
          width: 36,
          flexShrink: 0,
        }}
      >
        {time}
      </span>
      {/* .sched-label */}
      <span style={{ color: labelColor, flex: 1, fontSize: 12 }}>{label}</span>
      {/* .sched-tag */}
      <span
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 9,
          padding: '1px 6px',
          borderRadius: 4,
          flexShrink: 0,
          background: tagBg,
          color: tagColor,
          border: `1px solid ${tagBorder}`,
        }}
      >
        {tagLabel}
      </span>
    </div>
  )
}
