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
    <section style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>

      {/* Header — mini-title style */}
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
          Tool Activity
        </span>
        {events.length > 0 && (
          <span style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)' }}>
            {events.length}
          </span>
        )}
      </div>

      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 0,
        }}
      >
        {events.length === 0 ? (
          <div
            style={{
              padding: '12px 13px',
              fontFamily: 'var(--fd)',
              fontSize: 10,
              color: 'var(--tf)',
              textAlign: 'center',
            }}
          >
            In attesa di eventi tool…
          </div>
        ) : (
          events.map((evt) => <ToolEventRow key={evt.id} evt={evt} />)
        )}
      </div>
    </section>
  )
}
