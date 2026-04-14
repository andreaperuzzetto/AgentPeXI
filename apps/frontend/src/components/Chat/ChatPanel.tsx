import { useRef, useEffect } from 'react'
import { useStore } from '../../store'
import { ChatMessage } from './ChatMessage'
import { ChatInput } from './ChatInput'

interface Props {
  onSend: (content: string) => void
}

export function ChatPanel({ onSend }: Props) {
  const messages = useStore((s) => s.messages)
  const isTyping = useStore((s) => s.isTyping)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length])

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        borderRight: '1px solid var(--border-strong)',
        background: 'var(--bg-surface-1)',
      }}
    >
      {/* Section label */}
      <div
        style={{
          padding: '10px 12px 6px',
          borderBottom: '1px solid var(--border-subtle)',
        }}
      >
        <span className="section-label">Chat</span>
      </div>

      {/* Messages */}
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 2,
          padding: '4px 0',
        }}
      >
        {messages.length === 0 && (
          <div
            style={{
              flex: 1,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--text-faint)',
              fontSize: '0.75rem',
              padding: 24,
              textAlign: 'center',
            }}
          >
            Invia un messaggio per iniziare la conversazione con Pepe.
          </div>
        )}
        {messages.map((m) => (
          <ChatMessage key={m.id} msg={m} />
        ))}
        {isTyping && (
          <div style={{ padding: '6px 12px', fontSize: '0.75rem', color: 'var(--accent-dim)' }}>
            Pepe sta elaborando…
          </div>
        )}
      </div>

      {/* Input */}
      <ChatInput onSend={onSend} />
    </div>
  )
}
