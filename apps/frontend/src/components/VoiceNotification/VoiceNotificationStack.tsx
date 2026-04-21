import { useEffect, useRef } from 'react'
import { useUiStore, type VoiceNotification } from '../../store/uiStore'
import './VoiceNotificationStack.css'

const DISMISS_MS = 15_000

/* ── Single card ── */
function VoiceCard({ n }: { n: VoiceNotification }) {
  const dismiss = useUiStore((s) => s.dismissNotification)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    timerRef.current = setTimeout(() => dismiss(n.id), DISMISS_MS)
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [n.id, dismiss])

  /* Formato timestamp leggibile: HH:MM:SS */
  const timeStr = (() => {
    try {
      const d = new Date(n.ts)
      return d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    } catch {
      return ''
    }
  })()

  return (
    <div className="vn-card">
      {/* Progress bar */}
      <div className="vn-progress">
        <div
          className="vn-progress-bar"
          style={{ '--vn-duration': `${DISMISS_MS}ms` } as React.CSSProperties}
        />
      </div>

      {/* Header */}
      <div className="vn-header">
        <span className="vn-dot" />
        <span className="vn-agent">{n.agent}</span>
        {timeStr && <span className="vn-ts">{timeStr}</span>}
        <button className="vn-close" onClick={() => dismiss(n.id)} aria-label="Chiudi">×</button>
      </div>

      {/* Body */}
      <div className="vn-body">
        {n.message && <div className="vn-message">{n.message}</div>}
        {n.detail  && <div className="vn-detail">{n.detail}</div>}
      </div>
    </div>
  )
}

/* ── Stack ── */
export function VoiceNotificationStack() {
  const notifications = useUiStore((s) => s.notifications)
  if (notifications.length === 0) return null

  return (
    <div className="vn-stack">
      {notifications.map((n) => (
        <VoiceCard key={n.id} n={n} />
      ))}
    </div>
  )
}
