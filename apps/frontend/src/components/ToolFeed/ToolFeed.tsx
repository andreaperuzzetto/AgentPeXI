import { useRef, useEffect } from 'react'
import { useStore } from '../../store'
import { ToolEventRow } from './ToolEvent'

export function ToolFeed() {
  const events = useStore((s) => s.toolEvents)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [events.length])

  return (
    <section
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '40%',
        minHeight: 0,
      }}
    >
      <div style={{ padding: '10px 10px 6px' }}>
        <span className="section-label">Tool Activity</span>
      </div>
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 1,
        }}
      >
        {events.length === 0 && (
          <div
            style={{
              padding: '16px 10px',
              fontSize: '0.6875rem',
              color: 'var(--text-faint)',
              textAlign: 'center',
            }}
          >
            In attesa di eventi tool…
          </div>
        )}
        {events.map((evt) => (
          <ToolEventRow key={evt.id} evt={evt} />
        ))}
      </div>
    </section>
  )
}
