import { useRef, useEffect } from 'react'
import { useStore } from '../../store'
import { ChatMessage } from './ChatMessage'
import { ChatInput } from './ChatInput'

interface Props { onCollapse?: () => void }

export function ChatPanel({ onCollapse }: Props) {
  const messages = useStore((s) => s.messages)
  const isTyping = useStore((s) => s.isTyping)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isTyping])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>

      <div className="panel-header">
        <span className="section-label">Chat</span>
        {onCollapse && (
          <button
            onClick={onCollapse}
            title="Comprimi"
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: 'var(--tf)', fontSize: 13, padding: '2px 4px',
              lineHeight: 1, borderRadius: 4,
              transition: 'color .2s var(--e-io), transform .2s var(--e-spring)',
            }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = 'var(--tm)' }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = 'var(--tf)' }}
          >
            ‹
          </button>
        )}
      </div>

      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '10px 12px',
          display: 'flex',
          flexDirection: 'column',
          gap: 9,
          minHeight: 0,
        }}
      >
        {messages.length === 0 && (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <p
              style={{
                fontFamily: 'var(--fd)',
                fontSize: 11,
                color: 'var(--tf)',
                textAlign: 'center',
                lineHeight: 1.6,
              }}
            >
              Invia un messaggio<br />per iniziare con Pepe
            </p>
          </div>
        )}
        {messages.map((msg) => (
          <ChatMessage key={msg.id} msg={msg} />
        ))}
        {isTyping && (
          /* Typing dots — matches prototype .typing */
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '8px 11px' }}>
            {[0, 180, 360].map((delay) => (
              <span
                key={delay}
                style={{
                  width: 5,
                  height: 5,
                  borderRadius: '50%',
                  background: 'var(--tm)',
                  display: 'inline-block',
                  animation: `dot-bounce 1.4s var(--e-io) ${delay}ms infinite`,
                  willChange: 'transform, opacity',
                }}
              />
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div style={{ flexShrink: 0, borderTop: '1px solid var(--b0)' }}>
        <ChatInput />
      </div>

    </div>
  )
}
